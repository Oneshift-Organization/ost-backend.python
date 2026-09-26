"""GCP Cloud Billing export (BigQuery) — padrão e detalhado.

Colunas e semântica conferidas na documentação do Google (fontes em
``PLANO_CUR_BIGQUERY.md`` §1). O que este parser entrega:

- **quantidade na unidade de preço** (``usage.amount_in_pricing_units`` +
  ``usage.pricing_unit``). ``usage.amount`` vem na unidade BASE — segundos,
  byte-segundos —, que o agregador não reconhece: a capacidade de compute saía
  zero e o custo inteiro ia para "compromisso";
- **custo efetivo** (``cost``): ``cost`` + créditos estruturais (CUD, SUD,
  desconto negociado, free tier) + parcela rateada da taxa de compromisso.
  Crédito PROMOCIONAL não reduz a base (RN3): acaba, e faria o cliente
  parecer gastar menos do que gasta;
- **natureza** (``charge_kind``) por ``cost_type`` e ``adjustment_info``;
- **shape medido** de ``system_labels`` (``machine_spec``, ``cores``,
  ``memory``) e o recurso de ``resource.global_name``;
- ``capacity_unit`` explícito: linha de core é vCPU-hora, linha de RAM é
  GiB-hora — o agregador não precisa adivinhar pelo texto do SKU.

Formatos: NDJSON (inclusive ``.json.gz`` do ``bq extract``), array JSON, CSV
(achatado com ``service.description`` ou ``service_description``, campos
repetidos como JSON numa coluna), XLSX e Parquet. NDJSON é lido em fluxo;
CSV/XLSX/Parquet em blocos.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from app.domain.services.rateio import repartir
from app.domain.value_objects.charge_kind import ChargeKind
from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.consolidacao import Consolidador
from app.infrastructure.file_ingestion.loaders import is_parquet, is_real_zip_file, iter_tabular
from app.infrastructure.file_ingestion.unidades import normalizar_unidade

_ZERO = Decimal("0")
_INVOICE_MONTH_LEN = 6  # "YYYYMM"

# Fora da base (RN3): promoção acaba, e free tier é benefício do provedor de
# ORIGEM — não migra, e a cotação do destino também não o tem. Comparar origem
# com free tier contra destino sem ele não é comparar igual com igual.
_CREDITO_PROMOCIONAL = frozenset({"PROMOTION", "FREE_TIER"})
_CREDITO_CUD = frozenset({"COMMITTED_USAGE_DISCOUNT", "COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE"})
# Ajuste que CORRIGE custo recorrente fica na base; o resto é crédito.
_AJUSTE_DE_USO = frozenset({"USAGE_CORRECTION", "PRICE_CORRECTION", "METADATA_CORRECTION"})

# CSV exportado por ferramenta (Looker Studio, consultas próprias) achata com
# `_` em vez de `.`. Só os campos que o parser lê.
_ACHATADOS = {
    "service_id": "service.id",
    "service_description": "service.description",
    "sku_id": "sku.id",
    "sku_description": "sku.description",
    "project_id": "project.id",
    "project_name": "project.name",
    "location_location": "location.location",
    "location_region": "location.region",
    "location_zone": "location.zone",
    "resource_name": "resource.name",
    "resource_global_name": "resource.global_name",
    "usage_amount": "usage.amount",
    "usage_unit": "usage.unit",
    "usage_amount_in_pricing_units": "usage.amount_in_pricing_units",
    "usage_pricing_unit": "usage.pricing_unit",
    "invoice_month": "invoice.month",
    "adjustment_info_type": "adjustment_info.type",
}
_CAMPOS_JSON = (
    "credits",
    "labels",
    "system_labels",
    "tags",
    "service",
    "sku",
    "project",
    "location",
    "resource",
    "usage",
    "invoice",
    "adjustment_info",
)

_BYTES_GIB = Decimal(2**30)


def _dec(valor: object) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip()
    if not texto or texto.casefold() in {"nan", "none", "null"}:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _campo(raw: dict, pai: str, filho: str) -> Any:
    valor = raw.get(pai)
    return valor.get(filho) if isinstance(valor, dict) else None


@lru_cache(maxsize=65536)
def _data(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor.replace(" UTC", "+00:00"))
    except ValueError:
        ts = pd.to_datetime(valor, utc=True, errors="coerce")
        return None if pd.isna(ts) else ts.to_pydatetime()
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _pares(lista: object) -> dict[str, str]:
    """`[{key, value}]` (labels, system_labels, tags) → dict."""
    if not isinstance(lista, list):
        return {}
    saida: dict[str, str] = {}
    for item in lista:
        if isinstance(item, dict) and item.get("key") is not None:
            saida[str(item["key"])] = "" if item.get("value") is None else str(item["value"])
    return saida


def _creditos(lista: object) -> list[tuple[str, Decimal]]:
    if not isinstance(lista, list):
        return []
    saida = []
    for c in lista:
        if isinstance(c, dict):
            valor = _dec(c.get("amount"))
            if valor is not None:
                saida.append((str(c.get("type") or ""), valor))
    return saida


def _natureza(raw: dict, sku: str) -> ChargeKind:  # noqa: PLR0911 - cascata por cost_type
    tipo = str(raw.get("cost_type") or "regular").casefold().replace("_", " ")
    if tipo == "tax":
        return ChargeKind.TAX
    if tipo == "rounding error":
        return ChargeKind.ADJUSTMENT
    if tipo == "adjustment":
        ajuste = _campo(raw, "adjustment_info", "type")
        if ajuste in _AJUSTE_DE_USO:
            return ChargeKind.USAGE
        return ChargeKind.CREDIT if ajuste else ChargeKind.ADJUSTMENT
    if sku.startswith("Commitment"):
        return ChargeKind.COMMITMENT
    # "E2 Instance Core running free tier discount": o free tier também vem como
    # SKU próprio, com custo negativo e HORAS positivas. Pela escada ele era
    # `core` e somava capacidade às horas já contadas no uso — um pool fantasma
    # de custo negativo. É crédito, e sai da base pelo mesmo motivo acima.
    if "free tier" in sku.casefold():
        return ChargeKind.CREDIT
    return ChargeKind.USAGE


def _quantidade(raw: dict, sku: str) -> tuple[Decimal | None, str | None]:
    """Quantidade na unidade de PREÇO. Fallback converte a unidade base."""
    q_preco = _dec(_campo(raw, "usage", "amount_in_pricing_units"))
    u_preco = _campo(raw, "usage", "pricing_unit")
    if q_preco is not None and u_preco:
        unidade, mult = normalizar_unidade(str(u_preco))
        return q_preco * mult, unidade

    quantidade = _dec(_campo(raw, "usage", "amount"))
    unidade_base = str(_campo(raw, "usage", "unit") or "")
    if quantidade is None:
        return None, normalizar_unidade(unidade_base)[0] if unidade_base else None
    if unidade_base == "seconds":
        return quantidade / 3600, "hours"
    if unidade_base == "byte-seconds" and "Ram" in sku:
        return quantidade / (_BYTES_GIB * 3600), "gibibyte hour"
    unidade, mult = normalizar_unidade(unidade_base)
    return quantidade * mult, unidade


def _periodo(raw: dict, inicio: datetime | None) -> str:
    mes = str(_campo(raw, "invoice", "month") or "")
    if len(mes) == _INVOICE_MONTH_LEN and mes.isdigit():
        return f"{mes[:4]}-{mes[4:]}"
    return inicio.strftime("%Y-%m") if inicio else "0000-00"


def _capacidade(sku: str) -> str | None:
    """Linha de core é vCPU-hora; de RAM, GiB-hora. Explícito, não adivinhado."""
    if "Instance Core" in sku or "Core running in" in sku:
        return "vcpu"
    if "Instance Ram" in sku or "Ram running in" in sku:
        return "gib"
    return None


class GCPParser(BaseParser):
    _SIGNATURE_TOKENS = ("billing_account_id", "usage_start_time")

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(tok in text for tok in self._SIGNATURE_TOKENS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        consolidador = Consolidador(provider="gcp")
        detalhado = False
        for raw, identidade in _linhas_origem(file_path):
            linha = self._canonica(raw, identidade)
            detalhado = detalhado or bool(linha["resource_id"])
            consolidador.adicionar(linha)
        self._set_version("GCP_BILLING_DETAILED" if detalhado else "GCP_BILLING_STD")
        df = consolidador.dataframe(
            user_id=str(self.user_id),
            job_id=str(self.job_id),
            schema_version=self.get_schema_version(),
            source_file=file_path.name,
        )
        return ratear_compromisso(df)

    def _canonica(self, raw: dict, identidade: str) -> dict[str, Any]:
        service = _campo(raw, "service", "description") or _campo(raw, "service", "id") or "unknown"
        sku = str(_campo(raw, "sku", "description") or "")
        custo = _dec(raw.get("cost")) or _ZERO
        creditos = _creditos(raw.get("credits"))
        promocional = sum((v for t, v in creditos if t in _CREDITO_PROMOCIONAL), _ZERO)
        cud = sum((v for t, v in creditos if t in _CREDITO_CUD), _ZERO)
        todos = sum((v for _, v in creditos), _ZERO)
        efetivo = custo + todos - promocional

        quantidade, unidade = _quantidade(raw, sku)
        inicio = _data(str(raw.get("usage_start_time") or ""))
        rotulos = _pares(raw.get("system_labels"))
        projeto = _campo(raw, "project", "id")

        atributos: dict[str, str] = {}
        spec = rotulos.get("compute.googleapis.com/machine_spec")
        if spec:
            atributos["instance_type"] = spec
        cores = _dec(rotulos.get("compute.googleapis.com/cores"))
        if cores is not None and cores > 0:
            atributos["vcpus"] = str(cores)
        memoria = _dec(rotulos.get("compute.googleapis.com/memory"))
        if memoria is not None and memoria > 0:
            atributos["memory_gib"] = str((memoria / 1024).normalize())
        if projeto:
            atributos["project_id"] = str(projeto)
        if raw.get("billing_account_id"):
            atributos["billing_account_id"] = str(raw["billing_account_id"])
        if _campo(raw, "resource", "name"):
            atributos["resource_name"] = str(_campo(raw, "resource", "name"))
        capacidade = _capacidade(sku)
        if capacidade:
            atributos["capacity_unit"] = capacidade
        # Snapshot e machine image são backup, não disco (ver parser da AWS).
        if "Snapshot" in sku or "Machine Image" in sku:
            atributos["storage_kind"] = "snapshot"

        tags = _pares(raw.get("labels"))
        for chave, valor in _pares(raw.get("tags")).items():
            tags.setdefault(chave, valor)

        natureza = _natureza(raw, sku)
        return {
            "billing_period": _periodo(raw, inicio),
            "account_id": str(raw.get("billing_account_id") or ""),
            "account_name": _campo(raw, "project", "name"),
            "region": _campo(raw, "location", "region") or None,
            "resource_id": (
                _campo(raw, "resource", "global_name") or _campo(raw, "resource", "name") or None
            ),
            "service": str(service),
            "service_raw": str(service),
            "usage_type": sku or None,
            "operation": None,
            "resource_type": spec or None,
            "charge_kind": natureza.value,
            "usage_unit": unidade,
            "currency": (str(raw.get("currency") or "USD")).upper()[:3],
            "cost_basis": "amortized",
            "cost": efetivo,
            "cost_billed": custo + todos,
            "cost_discounted": efetivo,
            "usage_amount": quantidade,
            "on_demand": _dec(raw.get("cost_at_list")),
            "usage_start": inicio,
            "usage_end": _data(str(raw.get("usage_end_time") or "")),
            "is_estimate": False,
            # Mesma marca de antes: ajuste e arredondamento são correções.
            "is_correction": str(raw.get("cost_type") or "").casefold().replace("_", " ")
            in {"adjustment", "rounding error"},
            "tags": tags,
            "attributes": atributos,
            "identity": identidade,
            "line_type": str(raw.get("cost_type") or "regular"),
            "somas": {
                "cud_credit": cud,
                "promotion_credit": promocional,
                "cost_before_credits": custo,
            },
            "raw": {
                "service_id": _campo(raw, "service", "id"),
                "sku_id": _campo(raw, "sku", "id"),
                "sku_description": sku or None,
                "cost_type": raw.get("cost_type"),
            },
        }


def ratear_compromisso(df: pd.DataFrame) -> pd.DataFrame:
    """Taxa de compromisso (SKU `Commitment…`) → uso coberto pelo CUD.

    O uso coberto aparece com o preço sob demanda e um crédito de CUD que o
    anula; a taxa do compromisso vem numa linha à parte. Sem o rateio, a VM
    coberta custa zero e o compromisso custa tudo — o mesmo pool fantasma do
    Savings Plan da AWS.

    Rateio na proporção do crédito de CUD de cada linha, por período e moeda.
    O export não mostra a ociosidade do CUD, então a taxa vai INTEIRA para o
    uso coberto: é ESTIMATIVA, marcada em ``attributes.commitment_allocation``.
    Sem uso coberto no período, a taxa fica onde está (vai para o balde de
    compromisso).
    """
    if df.empty or "charge_kind" not in df.columns:
        return df
    df = df.copy()
    grupos: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
        lambda: {"taxas": [], "cobertas": []}
    )
    for i, linha in enumerate(df.itertuples(index=False)):
        chave = (linha.billing_period, linha.currency)
        if linha.charge_kind == ChargeKind.COMMITMENT.value:
            grupos[chave]["taxas"].append(i)
        elif linha.charge_kind == ChargeKind.USAGE.value and _dec(
            (linha.attributes or {}).get("cud_credit")
        ):
            grupos[chave]["cobertas"].append(i)

    custos = df["cost"].tolist()
    atributos = [dict(a or {}) for a in df["attributes"].tolist()]
    for g in grupos.values():
        if not g["taxas"] or not g["cobertas"]:
            continue
        taxa = sum((custos[i] for i in g["taxas"]), _ZERO)
        pesos = [abs(_dec(atributos[i]["cud_credit"]) or _ZERO) for i in g["cobertas"]]
        for i, parte in zip(g["cobertas"], repartir(taxa, pesos), strict=True):
            custos[i] = custos[i] + parte
            atributos[i]["commitment_allocated"] = str(parte)
            atributos[i]["commitment_allocation"] = "proporcional_ao_credito_cud"
        for i in g["taxas"]:
            atributos[i]["allocated_to_usage"] = str(custos[i])
            custos[i] = _ZERO
    df["cost"] = custos
    df["cost_discounted"] = custos
    df["attributes"] = atributos
    return df


# ── leitura dos formatos ─────────────────────────────────────────────────────


def _abrir_texto(file_path: Path):
    with open(file_path, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    if gz:
        return gzip.open(file_path, "rt", encoding="utf-8-sig", errors="replace")
    return open(file_path, encoding="utf-8-sig", errors="replace")


def _primeiro_caractere(file_path: Path) -> str:
    with _abrir_texto(file_path) as fh:
        while True:
            ch = fh.read(1)
            if not ch or not ch.isspace():
                return ch


def _linhas_origem(file_path: Path) -> Iterator[tuple[dict, str]]:
    """(linha como dict aninhado, identidade para deduplicar)."""
    tabular = is_parquet(file_path) or (
        file_path.suffix.lower() in {".xlsx", ".xlsm"} and is_real_zip_file(file_path)
    )
    primeiro = "" if tabular else _primeiro_caractere(file_path)
    if primeiro == "{":
        with _abrir_texto(file_path) as fh:
            for bruta in fh:
                texto = bruta.strip()
                if texto:
                    # parse_float=Decimal: o custo nunca passa por float.
                    yield json.loads(texto, parse_float=Decimal), texto
        return
    if primeiro == "[":
        with _abrir_texto(file_path) as fh:
            dados = json.load(fh, parse_float=Decimal)
        for item in dados if isinstance(dados, list) else []:
            if isinstance(item, dict):
                yield item, json.dumps(item, sort_keys=True, default=str)
        return
    for bloco in iter_tabular(file_path):
        colunas = list(bloco.columns)
        for valores in zip(*(bloco[c].tolist() for c in colunas), strict=True):
            plano = dict(zip(colunas, valores, strict=True))
            identidade = hashlib.blake2b(
                "\x1f".join(str(v) for v in valores).encode(), digest_size=16
            ).hexdigest()
            yield _desachatar(plano), identidade


def _desachatar(plano: dict[str, str]) -> dict[str, Any]:
    """Linha de CSV → o mesmo dict aninhado do NDJSON."""
    aninhado: dict[str, Any] = {}
    for chave_original, valor in plano.items():
        chave = _ACHATADOS.get(chave_original, chave_original)
        texto = valor.strip() if isinstance(valor, str) else valor
        if chave in _CAMPOS_JSON and isinstance(texto, str) and texto[:1] in ("[", "{"):
            with contextlib.suppress(ValueError):
                texto = json.loads(texto, parse_float=Decimal)
        if texto == "":
            texto = None
        if "." in chave:
            pai, filho = chave.split(".", 1)
            destino = aninhado.setdefault(pai, {})
            if isinstance(destino, dict):
                destino[filho] = texto
        elif isinstance(texto, dict) and isinstance(aninhado.get(chave), dict):
            aninhado[chave].update(texto)
        else:
            aninhado[chave] = texto
    return aninhado
