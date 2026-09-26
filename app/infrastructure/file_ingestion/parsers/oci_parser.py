"""OCI Cost Report (formato proprietário, `.csv.gz` a cada 6h).

Colunas conferidas na documentação da Oracle (fontes em
``PLANO_CUR_BIGQUERY.md`` §1). Correções em relação à versão anterior, que lia
nomes que o relatório real não tem:

- conta de ``lineItem/tenantId`` (lia ``tenantId``), compartimento de
  ``product/compartmentName`` (lia ``compartmentName``);
- unidade de ``cost/billingUnitReadable`` (lia ``usage/billedUnit``): sem ela a
  capacidade não acumulava;
- correção por ``lineItem/backReferenceNo`` (lia ``lineItem/backReference``): a
  regra de substituição nunca disparava e a correção era SOMADA à original;
- OCPU ≠ vCPU: em x86, 1 OCPU = 2 vCPU; no Ampere A1, 1 = 1.

Nomes lidos sem diferenciar maiúsculas (a doc mostra ``lineItem/TenantId``, os
relatórios trazem ``lineItem/tenantId``). Os nomes antigos continuam aceitos
como fallback.

Correção substitui a linha referenciada (semântica mantida; a doc não diz se
é delta — ver riscos no plano). Por isso a leitura é em DUAS passadas: a
correção pode estar num bloco posterior ao da linha que ela corrige.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from app.domain.value_objects.charge_kind import ChargeKind
from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.consolidacao import Consolidador
from app.infrastructure.file_ingestion.loaders import iter_tabular, ler_colunas
from app.infrastructure.file_ingestion.unidades import GIB_HORA, GIB_MES, HORAS, normalizar_unidade

_ZERO = Decimal("0")

_CAMPOS: dict[str, tuple[str, ...]] = {
    "reference": ("lineitem/referenceno",),
    "tenant": ("lineitem/tenantid", "tenantid"),
    "start": ("lineitem/intervalusagestart",),
    "end": ("lineitem/intervalusageend",),
    "service": ("product/service",),
    "compartment_id": ("product/compartmentid",),
    "compartment_name": ("product/compartmentname", "compartmentname"),
    "region": ("product/region",),
    "resource": ("product/resourceid",),
    "description": ("product/description",),
    "shape": ("product/resource",),
    "quantity": ("usage/billedquantity",),
    "cost": ("cost/mycost",),
    "currency": ("cost/currencycode",),
    "unit": ("cost/billingunitreadable", "usage/billedunit"),
    "sku": ("cost/productsku",),
    "is_correction": ("lineitem/iscorrection",),
    "back_reference": ("lineitem/backreferenceno", "lineitem/backreference"),
}

# "Compute - Standard - E4 - OCPU" → família E4; Ampere A1 conta 1 vCPU por OCPU.
_FAMILIA = re.compile(r"\b(A1|A2|E2|E3|E4|E5|E6|X9|X7|X10|B1|DenseIO\w*|GPU\w*)\b", re.IGNORECASE)
_UM_VCPU_POR_OCPU = frozenset({"A1"})


def _dec(valor: object) -> Decimal | None:
    texto = str(valor or "").strip()
    if not texto or texto.casefold() in {"nan", "none", "null"}:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


@lru_cache(maxsize=65536)
def _data(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        ts = pd.to_datetime(valor, utc=True, errors="coerce")
        return None if pd.isna(ts) else ts.to_pydatetime()
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


@lru_cache(maxsize=1024)
def unidade_oci(bruta: str) -> tuple[str | None, Decimal]:
    """Unidade legível da OCI → canônica. `OCPU Hours`, `GB Hours`, `GB Month`,
    `Gigabyte Per Hour`… — a grafia varia por serviço."""
    texto = (bruta or "").strip().casefold().replace("_", " ")
    if not texto:
        return None, Decimal("1")
    gb = "gb" in texto.split() or "gigabyte" in texto or texto.startswith("gb")
    if "hour" in texto:
        return (GIB_HORA if gb else HORAS), Decimal("1")
    if "month" in texto and gb:
        return GIB_MES, Decimal("1")
    return normalizar_unidade(bruta)


def _capacidade(descricao: str) -> tuple[str | None, str | None]:
    """(capacity_unit, família) pela descrição do produto de compute."""
    texto = descricao.casefold()
    if not texto.startswith("compute"):
        return None, None
    m = _FAMILIA.search(descricao)
    familia = m.group(1).upper() if m else None
    if "ocpu" in texto or "ecpu" in texto:
        return "vcpu", familia
    if "memory" in texto:
        return "gib", familia
    return None, familia


class OCIParser(BaseParser):
    _SIGNATURE_TOKENS = ("cost/myCost", "lineItem/intervalUsageStart")

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(tok.casefold() in text.casefold() for tok in self._SIGNATURE_TOKENS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        self._set_version("OCI_COST_REPORT")
        colunas = ler_colunas(file_path)
        por_minuscula = {c.casefold(): c for c in colunas}
        mapa = {
            campo: next((por_minuscula[o] for o in opcoes if o in por_minuscula), None)
            for campo, opcoes in _CAMPOS.items()
        }
        tags = {c: c[len("tags/"):] for c in colunas if c.casefold().startswith("tags/")}
        substituidas = self._substituidas(file_path, mapa)

        consolidador = Consolidador(provider="oci")
        lidas = {c for c in mapa.values() if c} | set(tags)
        for bloco in iter_tabular(file_path, colunas=lidas):
            for linha in self._linhas(bloco, mapa, tags, substituidas):
                consolidador.adicionar(linha)
        return consolidador.dataframe(
            user_id=str(self.user_id),
            job_id=str(self.job_id),
            schema_version=self.get_schema_version(),
            source_file=file_path.name,
        )

    @staticmethod
    def _substituidas(file_path: Path, mapa: dict[str, str | None]) -> set[str]:
        """1ª passada: referências que alguma correção substitui."""
        flag, back = mapa["is_correction"], mapa["back_reference"]
        if not flag or not back:
            return set()
        substituidas: set[str] = set()
        for bloco in iter_tabular(file_path, colunas={flag, back}):
            corrige = bloco[flag].astype(str).str.casefold() == "true"
            substituidas.update(v for v in bloco.loc[corrige, back].tolist() if v)
        return substituidas

    def _linhas(
        self,
        bloco: pd.DataFrame,
        mapa: dict[str, str | None],
        tags_cols: dict[str, str],
        substituidas: set[str],
    ) -> Any:
        n = len(bloco)
        vazio = [""] * n
        col = {
            campo: (bloco[nome].tolist() if nome and nome in bloco.columns else vazio)
            for campo, nome in mapa.items()
        }
        tags_valores = {nome: bloco[c].tolist() for c, nome in tags_cols.items()}
        for i in range(n):
            v = {campo: str(col[campo][i] or "") for campo in col}
            referencia = v["reference"]
            correcao = v["is_correction"].casefold() == "true"
            if referencia and referencia in substituidas and not correcao:
                continue  # a correção desta linha vem em outra: não somar as duas
            descricao = v["description"]
            unidade, mult = unidade_oci(v["unit"])
            quantidade = _dec(v["quantity"])
            if quantidade is not None:
                quantidade *= mult
            capacidade, familia = _capacidade(descricao)
            atributos: dict[str, str] = {}
            if capacidade:
                atributos["capacity_unit"] = capacidade
                if capacidade == "vcpu":
                    atributos["vcpus_per_unit"] = "1" if familia in _UM_VCPU_POR_OCPU else "2"
            if familia:
                atributos["instance_family"] = familia
            if v["shape"]:
                atributos["instance_type"] = v["shape"]
            projeto = v["compartment_id"] or v["compartment_name"]
            if projeto:
                atributos["project_id"] = projeto
            if "backup" in descricao.casefold() or "snapshot" in descricao.casefold():
                atributos["storage_kind"] = "snapshot"
            inicio = _data(v["start"])
            custo = _dec(v["cost"]) or _ZERO
            servico = v["service"] or "unknown"
            yield {
                "billing_period": inicio.strftime("%Y-%m") if inicio else "0000-00",
                "account_id": v["tenant"],
                "account_name": v["compartment_name"] or None,
                "region": v["region"] or None,
                "resource_id": v["resource"] or None,
                "service": servico,
                "service_raw": servico,
                "usage_type": descricao or None,
                "operation": v["sku"] or None,
                "resource_type": v["shape"] or None,
                "charge_kind": ChargeKind.USAGE.value,
                "usage_unit": unidade,
                "currency": (v["currency"] or "USD").upper()[:3],
                "cost_basis": "amortized",
                "cost": custo,
                "cost_billed": custo,
                "cost_discounted": custo,
                "usage_amount": quantidade,
                "on_demand": None,
                "usage_start": inicio,
                "usage_end": _data(v["end"]),
                "is_estimate": False,
                "is_correction": correcao,
                "tags": {
                    nome: str(lista[i]) for nome, lista in tags_valores.items() if lista[i]
                },
                "attributes": atributos,
                "identity": referencia or None,
                "line_type": "Correction" if correcao else "Usage",
                "raw": {
                    "lineItem/referenceNo": referencia or None,
                    "product/description": descricao or None,
                    "cost/productSku": v["sku"] or None,
                },
            }
