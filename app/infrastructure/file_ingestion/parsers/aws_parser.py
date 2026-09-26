"""AWS Cost and Usage Report — CUR 2.0 e CUR legado.

Colunas e semântica conferidas na documentação da AWS (fontes em
``PLANO_CUR_BIGQUERY.md`` §1). O que este parser entrega, por linha consolidada:

- **custo amortizado** (``cost``): o que a linha custa de fato, com Savings Plan
  e reserva distribuídos sobre o uso que cobriram (fórmula da AWS/CUDOS). Na
  base faturada, uma instância coberta por SP soma zero — a cobertura aparece
  em ``SavingsPlanCoveredUsage`` e é anulada pela ``SavingsPlanNegation``;
- **valor faturado** (``cost_billed``): o unblended, para reconciliar com a fatura;
- **natureza** (``charge_kind``): o que o enriquecimento faz com a linha;
- **região** de ``product_region_code`` (``product_region`` não existe no CUR 2.0);
- **conta de uso** (``line_item_usage_account_id``), não a pagadora;
- **shape medido** do mapa ``product`` (vCPU, memória), que prevalece sobre a
  estimativa pelo nome do tipo;
- **descritor** (``usage_type``) para a escada de SKU.

Leitura em blocos e só das colunas usadas: um CUR horário não cabe inteiro na
memória. As linhas são consolidadas por recurso e tipo de uso no mês
(``consolidacao.py``).

Degradação declarada: o CUR 2.0 deixa escolher colunas. Sem as de Savings
Plan/reserva, a linha coberta fica na base faturada, marcada em
``attributes.cost_basis = "billed"``. Sem nem ``line_item_line_item_type``,
toda linha é ``usage`` na base faturada — o comportamento anterior, agora dito.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
from app.infrastructure.file_ingestion.unidades import normalizar_unidade

_ZERO = Decimal("0")

BASE_AMORTIZADA = "amortized"
BASE_FATURADA = "billed"

# RN2: tipo de linha da AWS → natureza.
_NATUREZA: dict[str, ChargeKind] = {
    "Usage": ChargeKind.USAGE,
    "DiscountedUsage": ChargeKind.USAGE,
    "SavingsPlanCoveredUsage": ChargeKind.USAGE,
    "SavingsPlanRecurringFee": ChargeKind.COMMITMENT,
    "SavingsPlanUpfrontFee": ChargeKind.COMMITMENT,
    "SavingsPlanNegation": ChargeKind.COMMITMENT,
    "RIFee": ChargeKind.COMMITMENT,
    "Tax": ChargeKind.TAX,
    "Credit": ChargeKind.CREDIT,
    "Refund": ChargeKind.REFUND,
    "Fee": ChargeKind.OTHER,
    "FlateRateSubscription": ChargeKind.OTHER,
}

# Tipos de linha cujo custo real só existe com as colunas de compromisso.
_PRECISA_SP = frozenset(
    {
        "SavingsPlanCoveredUsage",
        "SavingsPlanNegation",
        "SavingsPlanRecurringFee",
        "SavingsPlanUpfrontFee",
    }
)
_PRECISA_RI = frozenset({"DiscountedUsage", "RIFee"})

# `m7g.2xlarge`, `db.r8g.4xlarge`, `cache.t4g.micro` — sufixo do tipo de uso.
_TIPO_NO_USO = re.compile(r":((?:db\.|cache\.)?[a-z][a-z0-9-]*\.[a-z0-9]+)$")
# Tipos de uso cobrados por HORA DE INSTÂNCIA: a quantidade é instância-hora,
# e a capacidade sai de quantidade vezes shape. Explícito aqui para o agregador não
# adivinhar pelo texto (EBSOptimized e CPUCredits também têm o tipo no nome e
# não são capacidade).
_POR_INSTANCIA = re.compile(
    r"(?:^|[-:])(BoxUsage|SpotUsage|DedicatedUsage|HostUsage|InstanceUsage"
    r"|InstanceUsageIOOptimized|Multi-AZUsage|Multi-AZClusterUsage|NodeUsage):"
)
_MEMORIA = re.compile(r"^\s*([\d.,]+)\s*(GiB|GB)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class _Colunas:
    """Nome real de cada campo neste arquivo (CUR 2.0 ou legado)."""

    campos: dict[str, str | None]
    tags: dict[str, str]  # coluna -> nome da tag (tags achatadas)
    produto: dict[str, str]  # coluna -> atributo (atributos de produto achatados)

    def __getitem__(self, campo: str) -> str | None:
        return self.campos.get(campo)

    @property
    def lidas(self) -> set[str]:
        return {c for c in self.campos.values() if c} | set(self.tags) | set(self.produto)


# Candidatos por campo, em ordem de preferência. CUR 2.0 primeiro; depois as
# variantes que aparecem em exports feitos por consulta SQL ("método 1" da AWS).
_CUR2 = {
    "cost": ("line_item_unblended_cost",),
    "cost_net": ("line_item_net_unblended_cost",),
    "tipo": ("line_item_line_item_type",),
    "usage_type": ("line_item_usage_type",),
    "operation": ("line_item_operation",),
    "service": ("line_item_product_code",),
    "resource": ("line_item_resource_id",),
    "amount": ("line_item_usage_amount",),
    "unit": ("pricing_unit",),
    "tags": ("resource_tags",),
    "invoice": ("bill_invoice_id",),
    "payer": ("bill_payer_account_id",),
    "usage_account": ("line_item_usage_account_id",),
    "usage_account_name": ("line_item_usage_account_name",),
    "region": ("product_region_code", "product_region"),
    "period": ("bill_billing_period_start_date",),
    "currency": ("line_item_currency_code",),
    "start": ("line_item_usage_start_date",),
    "end": ("line_item_usage_end_date",),
    "instance_type": ("product_instance_type",),
    "product_map": ("product",),
    "description": ("line_item_line_item_description",),
    "on_demand": ("pricing_public_on_demand_cost",),
    "sp_effective": ("savings_plan_savings_plan_effective_cost",),
    "sp_net_effective": ("savings_plan_net_savings_plan_effective_cost",),
    "sp_total": ("savings_plan_total_commitment_to_date",),
    "sp_used": ("savings_plan_used_commitment",),
    "sp_arn": ("savings_plan_savings_plan_a_r_n",),
    "ri_effective": ("reservation_effective_cost",),
    "ri_net_effective": ("reservation_net_effective_cost",),
    "ri_unused_upfront": ("reservation_unused_amortized_upfront_fee_for_billing_period",),
    "ri_net_unused_upfront": ("reservation_net_unused_amortized_upfront_fee_for_billing_period",),
    "ri_unused_recurring": ("reservation_unused_recurring_fee",),
    "ri_net_unused_recurring": ("reservation_net_unused_recurring_fee",),
    "ri_arn": ("reservation_reservation_a_r_n",),
    "identity": ("identity_line_item_id",),
    "interval": ("identity_time_interval",),
}
_LEGADO = {
    "cost": ("lineItem/UnblendedCost",),
    "cost_net": ("lineItem/NetUnblendedCost",),
    "tipo": ("lineItem/LineItemType",),
    "usage_type": ("lineItem/UsageType",),
    "operation": ("lineItem/Operation",),
    "service": ("lineItem/ProductCode",),
    "resource": ("lineItem/ResourceId",),
    "amount": ("lineItem/UsageAmount",),
    "unit": ("pricing/unit",),
    "tags": (),
    "invoice": ("bill/InvoiceId",),
    "payer": ("bill/PayerAccountId",),
    "usage_account": ("lineItem/UsageAccountId",),
    "usage_account_name": (),
    # No legado, `product/region` já é o código (us-east-1); `product/location`
    # é o nome por extenso.
    "region": ("product/regionCode", "product/region"),
    "period": ("bill/BillingPeriodStartDate",),
    "currency": ("lineItem/CurrencyCode",),
    "start": ("lineItem/UsageStartDate",),
    "end": ("lineItem/UsageEndDate",),
    "instance_type": ("product/instanceType",),
    "product_map": (),
    "description": ("lineItem/LineItemDescription",),
    "on_demand": ("pricing/publicOnDemandCost",),
    "sp_effective": ("savingsPlan/SavingsPlanEffectiveCost",),
    "sp_net_effective": ("savingsPlan/NetSavingsPlanEffectiveCost",),
    "sp_total": ("savingsPlan/TotalCommitmentToDate",),
    "sp_used": ("savingsPlan/UsedCommitment",),
    "sp_arn": ("savingsPlan/SavingsPlanARN",),
    "ri_effective": ("reservation/EffectiveCost",),
    "ri_net_effective": ("reservation/NetEffectiveCost",),
    "ri_unused_upfront": ("reservation/UnusedAmortizedUpfrontFeeForBillingPeriod",),
    "ri_net_unused_upfront": ("reservation/NetUnusedAmortizedUpfrontFeeForBillingPeriod",),
    "ri_unused_recurring": ("reservation/UnusedRecurringFee",),
    "ri_net_unused_recurring": ("reservation/NetUnusedRecurringFee",),
    "ri_arn": ("reservation/ReservationARN",),
    "identity": ("identity/LineItemId",),
    "interval": ("identity/TimeInterval",),
}

# Atributo de produto → chave canônica em `attributes`.
_ATRIBUTOS = {
    "vcpu": "vcpus",
    "memory": "memory_gib",
    "operating_system": "operating_system",
    "operatingsystem": "operating_system",
    "tenancy": "tenancy",
    "database_engine": "database_engine",
    "databaseengine": "database_engine",
    "deployment_option": "deployment_option",
    "deploymentoption": "deployment_option",
    "cache_engine": "cache_engine",
    "cacheengine": "cache_engine",
    "volume_api_name": "volume_type",
    "volumeapiname": "volume_type",
    "storage_class": "storage_class",
    "storageclass": "storage_class",
    "physical_processor": "physical_processor",
    "physicalprocessor": "physical_processor",
}


def _mapear(colunas: list[str], cur2: bool) -> _Colunas:
    presentes = set(colunas)
    candidatos = _CUR2 if cur2 else _LEGADO
    campos = {
        campo: next((c for c in opcoes if c in presentes), None)
        for campo, opcoes in candidatos.items()
    }
    tags: dict[str, str] = {}
    produto: dict[str, str] = {}
    for c in colunas:
        if c.startswith("resourceTags/"):  # legado: resourceTags/user:Name
            tags[c] = _nome_tag(c[len("resourceTags/") :])
        elif c.startswith("resource_tags_"):  # achatado: resource_tags_user_name
            tags[c] = _nome_tag(c[len("resource_tags_") :])
        elif c.startswith("product/"):
            chave = c[len("product/") :].casefold()
            if chave in _ATRIBUTOS:
                produto[c] = _ATRIBUTOS[chave]
        elif c.startswith("product_") and c[len("product_") :] in _ATRIBUTOS:
            produto[c] = _ATRIBUTOS[c[len("product_") :]]
    return _Colunas(campos=campos, tags=tags, produto=produto)


def _nome_tag(bruto: str) -> str:
    """`user:env`, `user_env` → `env`; `aws:createdBy`, `aws_createdBy` → `aws:createdBy`.

    O prefixo `user` é ruído (toda tag de cliente o tem); o `aws` distingue tag
    gerada pela AWS, então fica — normalizado para a forma com dois-pontos.
    """
    for prefixo in ("user:", "user_"):
        if bruto.startswith(prefixo):
            return bruto[len(prefixo) :]
    for prefixo in ("aws:", "aws_"):
        if bruto.startswith(prefixo):
            return "aws:" + bruto[len(prefixo) :]
    return bruto


def _dec(valor: object) -> Decimal | None:
    """Monetário/quantidade via texto — nunca float. Vazio vira None."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.casefold() in {"nan", "none", "null"}:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


@lru_cache(maxsize=65536)
def _data(valor: str) -> datetime | None:
    """ISO-8601 do CUR (`2026-03-01T00:00:00Z`), com cache.

    `pd.to_datetime` por valor custava 27 dos 30 s de um CUR de 33 mil linhas:
    ele adivinha o formato a cada chamada. As datas de um export se repetem
    (31 dias, 744 horas), então o cache resolve quase tudo; o pandas fica só
    para formato fora do ISO.
    """
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor)
    except ValueError:
        ts = pd.to_datetime(valor, utc=True, errors="coerce")
        return None if pd.isna(ts) else ts.to_pydatetime()
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _periodo(bruto: str, inicio: datetime | None) -> str:
    texto = (bruto or "").strip().replace("/", "-")
    if len(texto) >= 7 and texto[4] == "-":  # noqa: PLR2004 - "YYYY-MM"
        return texto[:7]
    return inicio.strftime("%Y-%m") if inicio else "0000-00"


def custo_amortizado(  # noqa: PLR0911 - um retorno por tipo de linha
    tipo: str, v: dict[str, Decimal | None], tem_sp: bool, tem_ri: bool
) -> tuple[Decimal, str]:
    """Custo efetivo da linha e a base em que ele ficou.

    Fórmula da AWS (Well-Architected / CUDOS). As colunas ``net_*`` — só
    presentes quando há desconto negociado no período — prevalecem.
    """
    unblended = v["cost"] or _ZERO
    liquido = v["cost_net"] if v["cost_net"] is not None else unblended

    if tipo in _PRECISA_SP and not tem_sp:
        return unblended, BASE_FATURADA
    if tipo in _PRECISA_RI and not tem_ri:
        return unblended, BASE_FATURADA

    if tipo == "SavingsPlanCoveredUsage":
        efetivo = v["sp_net_effective"] if v["sp_net_effective"] is not None else v["sp_effective"]
        return efetivo or _ZERO, BASE_AMORTIZADA
    if tipo in ("SavingsPlanNegation", "SavingsPlanUpfrontFee"):
        return _ZERO, BASE_AMORTIZADA
    if tipo == "SavingsPlanRecurringFee":
        return (v["sp_total"] or _ZERO) - (v["sp_used"] or _ZERO), BASE_AMORTIZADA
    if tipo == "DiscountedUsage":
        efetivo = v["ri_net_effective"] if v["ri_net_effective"] is not None else v["ri_effective"]
        return efetivo or _ZERO, BASE_AMORTIZADA
    if tipo == "RIFee":
        upfront = (
            v["ri_net_unused_upfront"]
            if v["ri_net_unused_upfront"] is not None
            else v["ri_unused_upfront"]
        )
        recorrente = (
            v["ri_net_unused_recurring"]
            if v["ri_net_unused_recurring"] is not None
            else v["ri_unused_recurring"]
        )
        return (upfront or _ZERO) + (recorrente or _ZERO), BASE_AMORTIZADA
    if tipo == "Fee" and v.get("ri_arn_presente"):
        # Taxa inicial de reserva: no amortizado ela já está espalhada pelo uso.
        return (_ZERO, BASE_AMORTIZADA) if tem_ri else (unblended, BASE_FATURADA)
    return liquido, BASE_AMORTIZADA


def natureza(tipo: str, ri_arn_presente: bool) -> ChargeKind:
    if tipo == "Fee" and ri_arn_presente:
        return ChargeKind.COMMITMENT
    if tipo in _NATUREZA:
        return _NATUREZA[tipo]
    # `Discount`, `BundledDiscount`, `EdpDiscount`, `PrivateRateDiscount`…: o
    # desconto já está nas colunas `net_*`, então a linha sai da base.
    if tipo.endswith("Discount"):
        return ChargeKind.CREDIT
    return ChargeKind.OTHER


@lru_cache(maxsize=16384)
def _produto_cache(bruto: str) -> tuple[tuple[str, str], ...]:
    if not bruto:
        return ()
    try:
        mapa = json.loads(bruto)
    except (ValueError, TypeError):
        return ()
    if not isinstance(mapa, dict):
        return ()
    saida: dict[str, str] = {}
    for chave, valor in mapa.items():
        chave_n = str(chave).casefold()
        if chave_n in _ATRIBUTOS and valor not in (None, ""):
            saida[_ATRIBUTOS[chave_n]] = str(valor)
        elif chave_n in ("region", "instance_type", "instancetype") and valor:
            saida["region" if chave_n == "region" else "instance_type"] = str(valor)
    return tuple(saida.items())


@lru_cache(maxsize=16384)
def _tags_cache(bruto: str) -> tuple[tuple[str, str], ...]:
    tags = BaseParser._parse_tags(bruto)
    return tuple((_nome_tag(k), v) for k, v in tags.items() if v != "")


_unidade = lru_cache(maxsize=1024)(normalizar_unidade)


def _memoria_gib(texto: str) -> str | None:
    m = _MEMORIA.match(texto or "")
    if not m:
        return None
    return str(Decimal(m.group(1).replace(",", "")))


class AWSParser(BaseParser):
    _CUR2_SIGNATURE = "line_item_unblended_cost"
    _LEGACY_SIGNATURE = "lineItem/UnblendedCost"

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return self._CUR2_SIGNATURE in text or self._LEGACY_SIGNATURE in text

    def parse(self, file_path: Path) -> pd.DataFrame:
        colunas = ler_colunas(file_path)
        is_cur2 = self._CUR2_SIGNATURE in colunas
        self._set_version("AWS_CUR2.0" if is_cur2 else "AWS_CUR_LEGACY")
        mapa = _mapear(colunas, is_cur2)
        # Colunas de compromisso presentes = amortização possível.
        tem_sp = bool(mapa["sp_effective"]) and bool(mapa["sp_total"]) and bool(mapa["sp_used"])
        tem_ri = bool(mapa["ri_effective"]) and bool(mapa["ri_unused_recurring"])

        consolidador = Consolidador(provider="aws")
        for bloco in iter_tabular(file_path, colunas=mapa.lidas):
            for linha in self._linhas(bloco, mapa, tem_sp, tem_ri):
                consolidador.adicionar(linha)
        return consolidador.dataframe(
            user_id=str(self.user_id),
            job_id=str(self.job_id),
            schema_version=self.get_schema_version(),
            source_file=file_path.name,
        )

    def _linhas(self, bloco: pd.DataFrame, mapa: _Colunas, tem_sp: bool, tem_ri: bool) -> Any:
        n = len(bloco)
        vazio = [""] * n

        def col(campo: str) -> list[str]:
            nome = mapa[campo]
            return bloco[nome].tolist() if nome and nome in bloco.columns else vazio

        monetarios = (
            "cost",
            "cost_net",
            "sp_effective",
            "sp_net_effective",
            "sp_total",
            "sp_used",
            "ri_effective",
            "ri_net_effective",
            "ri_unused_upfront",
            "ri_net_unused_upfront",
            "ri_unused_recurring",
            "ri_net_unused_recurring",
            "amount",
            "on_demand",
        )
        valores = {campo: col(campo) for campo in monetarios}
        texto = {
            campo: col(campo)
            for campo in (
                "tipo",
                "usage_type",
                "operation",
                "service",
                "resource",
                "unit",
                "tags",
                "invoice",
                "payer",
                "usage_account",
                "usage_account_name",
                "region",
                "period",
                "currency",
                "start",
                "end",
                "instance_type",
                "product_map",
                "description",
                "sp_arn",
                "ri_arn",
                "identity",
                "interval",
            )
        }
        tags_achatadas = {nome: bloco[c].tolist() for c, nome in mapa.tags.items()}
        produto_achatado = {attr: bloco[c].tolist() for c, attr in mapa.produto.items()}

        for i in range(n):
            tipo = texto["tipo"][i] or "Usage"
            ri_arn = texto["ri_arn"][i]
            v: dict[str, Any] = {campo: _dec(valores[campo][i]) for campo in monetarios}
            v["ri_arn_presente"] = bool(ri_arn)
            custo, base = custo_amortizado(tipo, v, tem_sp, tem_ri)
            if not mapa["tipo"]:
                base = BASE_FATURADA  # export sem tipo de linha: nada foi amortizado

            unidade, mult = _unidade(texto["unit"][i])
            quantidade = v["amount"] * mult if v["amount"] is not None else None
            inicio = _data(texto["start"][i])
            conta = texto["usage_account"][i] or texto["payer"][i] or ""

            produto = self._produto(texto["product_map"][i])
            for attr, lista in produto_achatado.items():
                if lista[i]:
                    produto.setdefault(attr, lista[i])
            atributos = self._atributos(
                produto, texto["instance_type"][i], conta, texto["usage_type"][i]
            )

            regiao = texto["region"][i] or produto.get("region") or None
            identidade = texto["identity"][i]
            if identidade and texto["interval"][i]:
                identidade = f"{identidade}|{texto['interval'][i]}"

            yield {
                "billing_period": _periodo(texto["period"][i], inicio),
                "account_id": conta,
                "account_name": texto["usage_account_name"][i] or None,
                "region": regiao,
                "resource_id": texto["resource"][i] or None,
                "service": texto["service"][i] or "unknown",
                "service_raw": texto["service"][i] or "unknown",
                "usage_type": texto["usage_type"][i] or None,
                "operation": texto["operation"][i] or None,
                "resource_type": atributos.get("instance_type"),
                "charge_kind": natureza(tipo, bool(ri_arn)).value,
                "usage_unit": unidade,
                "currency": (texto["currency"][i] or "USD").upper()[:3],
                "cost_basis": base,
                "cost": custo,
                "cost_billed": v["cost"] or _ZERO,
                "cost_discounted": custo,
                "usage_amount": quantidade,
                "on_demand": v["on_demand"],
                "usage_start": inicio,
                "usage_end": _data(texto["end"][i]),
                "is_estimate": not bool(texto["invoice"][i]),
                "is_correction": False,
                "tags": self._tags(texto["tags"][i], tags_achatadas, i),
                "attributes": atributos,
                "identity": identidade or None,
                "line_type": tipo,
                "raw": {
                    "identity_line_item_id": texto["identity"][i] or None,
                    "line_item_line_item_type": tipo,
                    "line_item_product_code": texto["service"][i] or None,
                    "line_item_usage_type": texto["usage_type"][i] or None,
                    "line_item_operation": texto["operation"][i] or None,
                    "line_item_line_item_description": texto["description"][i] or None,
                },
            }

    @staticmethod
    def _produto(bruto: str) -> dict[str, str]:
        """Mapa `product` do CUR 2.0 (JSON no CSV) → atributos conhecidos.

        Cópia do resultado em cache: quem chama acrescenta atributos achatados
        ao dict, e mutar o objeto do cache contaminaria as linhas seguintes.
        """
        return dict(_produto_cache(bruto))

    @staticmethod
    def _atributos(
        produto: dict[str, str], instance_type: str, conta: str, usage_type: str
    ) -> dict[str, str]:
        """Só fato informado pela AWS (RN4). Nada aqui é estimativa."""
        atributos: dict[str, str] = {}
        tipo = instance_type or produto.get("instance_type")
        if not tipo:
            m = _TIPO_NO_USO.search(usage_type or "")
            tipo = m.group(1) if m else None
        if tipo:
            atributos["instance_type"] = tipo
        vcpu = _dec(produto.get("vcpus"))
        if vcpu is not None and vcpu > 0:
            atributos["vcpus"] = str(vcpu)
        memoria = _memoria_gib(produto.get("memory_gib", ""))
        if memoria is not None:
            atributos["memory_gib"] = memoria
        for chave in (
            "operating_system",
            "tenancy",
            "database_engine",
            "deployment_option",
            "cache_engine",
            "volume_type",
            "storage_class",
            "physical_processor",
        ):
            if produto.get(chave):
                atributos[chave] = produto[chave]
        if conta:
            atributos["project_id"] = conta
        if _POR_INSTANCIA.search(usage_type or "") and "ExtendedSupport" not in (usage_type or ""):
            atributos["capacity_unit"] = "instance"
        return atributos

    def _tags(self, bruto: str, achatadas: dict[str, list[str]], i: int) -> dict[str, str]:
        tags = dict(_tags_cache(bruto or ""))
        for nome, lista in achatadas.items():
            if lista[i]:
                tags[nome] = lista[i]
        return tags
