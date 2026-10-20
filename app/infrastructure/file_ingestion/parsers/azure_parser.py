"""Azure Cost Management — cost details (EA, MCA, PAYG).

Campos conferidos na documentação da Microsoft (fontes em
``PLANO_CUR_BIGQUERY.md`` §1). Correções em relação à versão anterior:

- ``ServiceName`` só existe em PAYG. Sem a coluna, ``str(None)`` virava o
  serviço literal "None" em todo export EA/MCA — agora cai em ``MeterCategory``;
- ``Tags`` do export vem SEM as chaves externas (``"env": "prod","app": "x"``),
  que não é JSON: as tags sumiam;
- ``ChargeType``/``PricingModel`` decidem a natureza: compra de reserva ou de
  Savings Plan é compromisso, não uso;
- ``UnitOfMeasure`` traz multiplicador (``"10 Hours"``): a quantidade é
  normalizada, senão a capacidade não acumula;
- ``AdditionalInfo`` traz o tamanho da VM (``ServiceType``) e o vCPU medido.

Base de custo: o valor exportado. No dataset *amortized* ele já distribui a
reserva sobre o uso; no *actual*, o uso coberto sai zero e a compra aparece como
compromisso — o caso que ``cobertura_por_compromisso`` declara.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from app.domain.services.azure_shape import familia_vm
from app.domain.value_objects.charge_kind import ChargeKind
from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.consolidacao import Consolidador
from app.infrastructure.file_ingestion.loaders import iter_tabular, ler_colunas
from app.infrastructure.file_ingestion.unidades import normalizar_unidade

_ZERO = Decimal("0")

# Nome da coluna por campo, na ordem de preferência (EA/MCA/PAYG variam).
_CAMPOS: dict[str, tuple[str, ...]] = {
    "cost": ("CostInBillingCurrency", "costInBillingCurrency", "Cost", "PreTaxCost"),
    "date": ("Date", "date", "UsageDateTime", "UsageDate"),
    "subscription": ("SubscriptionId", "subscriptionId", "SubscriptionGuid"),
    "subscription_name": ("SubscriptionName", "subscriptionName"),
    "service": ("ServiceName",),
    "meter_category": ("MeterCategory", "meterCategory"),
    "meter_subcategory": ("MeterSubCategory", "meterSubCategory"),
    "meter_name": ("MeterName", "meterName"),
    "resource": ("ResourceId", "resourceId", "InstanceId", "InstanceName"),
    "location": ("ResourceLocation", "resourceLocation", "Location", "MeterRegion"),
    "quantity": ("Quantity", "quantity", "UsageQuantity"),
    "unit": ("UnitOfMeasure", "unitOfMeasure"),
    "currency": ("BillingCurrency", "billingCurrency", "BillingCurrencyCode", "Currency"),
    "tags": ("Tags", "tags"),
    "charge_type": ("ChargeType", "chargeType"),
    "pricing_model": ("PricingModel", "pricingModel"),
    "additional_info": ("AdditionalInfo", "additionalInfo"),
    "payg_price": ("PayGPrice", "paygPrice", "payGPrice"),
    "resource_group": ("ResourceGroup", "resourceGroup", "ResourceGroupName"),
    "billing_period_start": ("BillingPeriodStartDate", "billingPeriodStartDate"),
}

_COMPROMISSO = frozenset({"reservation", "savingsplan", "savings plan"})
# Só o MCA tem câmbio de preço para faturamento.
_COLUNAS_MCA = frozenset(
    {"ExchangeRatePricingToBilling", "exchangeRatePricingToBilling", "costInUsd"}
)
# Linhas de VM cobradas por hora de instância (MeterCategory + unidade de hora).
_CATEGORIA_VM = "virtual machines"
# Disco gerenciado é cobrado por disco-mês ("1/Month") no TIER (P30, E20, S10…).
# O tamanho do tier é publicado; convertido, vira GB-mês MEDIDO — sem isso a
# Azure seria a única nuvem sem tamanho de disco.
_TIER_DISCO = re.compile(r"\b[PES](\d{1,2})\b")
_GIB_POR_TIER = {
    1: 4, 2: 8, 3: 16, 4: 32, 6: 64, 10: 128, 15: 256, 20: 512, 30: 1024,
    40: 2048, 50: 4096, 60: 8192, 70: 16384, 80: 32767,
}

# `Standard_D4s_v5` → família `DSV5` (letra de família + sufixos + geração).
def _dec(valor: object) -> Decimal | None:
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
    """Data do export. MCA usa `MM/DD/YYYY`; EA e exports novos, ISO."""
    if not valor:
        return None
    for formato in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(valor.strip()[:19], formato).replace(tzinfo=UTC)
        except ValueError:
            continue
    ts = pd.to_datetime(valor, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.to_pydatetime()


@lru_cache(maxsize=16384)
def _tags(bruto: str) -> tuple[tuple[str, str], ...]:
    """Tags do export: JSON com ou SEM as chaves externas."""
    texto = (bruto or "").strip()
    if not texto:
        return ()
    if not texto.startswith("{"):
        texto = "{" + texto + "}"
    try:
        mapa = json.loads(texto)
    except ValueError:
        return ()
    if not isinstance(mapa, dict):
        return ()
    return tuple((str(k), "" if v is None else str(v)) for k, v in mapa.items())


@lru_cache(maxsize=16384)
def _info(bruto: str) -> tuple[tuple[str, str], ...]:
    """`AdditionalInfo` (JSON) → pares conhecidos."""
    if not bruto:
        return ()
    try:
        mapa = json.loads(bruto)
    except ValueError:
        return ()
    if not isinstance(mapa, dict):
        return ()
    return tuple((str(k), str(v)) for k, v in mapa.items() if v not in (None, ""))


def _natureza(charge_type: str, pricing_model: str) -> ChargeKind:
    tipo = charge_type.casefold().replace(" ", "")
    modelo = pricing_model.casefold()
    if tipo in {"unusedreservation", "unusedsavingsplan"}:
        return ChargeKind.COMMITMENT
    if tipo == "purchase":
        return ChargeKind.COMMITMENT if modelo in _COMPROMISSO else ChargeKind.OTHER
    if tipo == "refund":
        return ChargeKind.REFUND
    if tipo == "roundingadjustment":
        return ChargeKind.ADJUSTMENT
    return ChargeKind.USAGE


def _regiao(bruto: str) -> str | None:
    """`East US` / `EastUS` / `eastus` → `eastus` (a forma do ARM)."""
    texto = (bruto or "").strip()
    if not texto or texto.casefold() in {"unassigned", "global", "all regions"}:
        return None
    return texto.replace(" ", "").casefold()


def _disco_em_gb(
    valores: dict[str, str], unidade: str | None, quantidade: Decimal | None
) -> tuple[str | None, Decimal | None]:
    """Disco-mês de um tier → GB-mês (tier medido x tamanho publicado do tier)."""
    if quantidade is None or "managed disks" not in valores["meter_subcategory"].casefold():
        return unidade, quantidade
    if "month" not in (unidade or "").casefold():
        return unidade, quantidade
    m = _TIER_DISCO.search(valores["meter_name"])
    gib = _GIB_POR_TIER.get(int(m.group(1))) if m else None
    if gib is None:
        return unidade, quantidade
    return "gibibyte month", quantidade * gib


class AzureParser(BaseParser):
    _SIGNATURE_TOKENS = ("CostInBillingCurrency", "MeterCategory")

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(tok in text for tok in self._SIGNATURE_TOKENS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        colunas = ler_colunas(file_path)
        presentes = set(colunas)
        mapa = {
            campo: next((c for c in opcoes if c in presentes), None)
            for campo, opcoes in _CAMPOS.items()
        }
        if "SubscriptionGuid" in presentes:
            self._set_version("AZURE_PAYG")
        elif presentes & _COLUNAS_MCA:
            self._set_version("AZURE_MCA")
        else:
            self._set_version("AZURE_EA")

        consolidador = Consolidador(provider="azure")
        # Todas as colunas: a identidade para deduplicar é a linha INTEIRA — só
        # as mapeadas juntariam linhas legítimas que diferem numa coluna não lida.
        for bloco in iter_tabular(file_path):
            for linha in self._linhas(bloco, mapa):
                consolidador.adicionar(linha)
        return consolidador.dataframe(
            user_id=str(self.user_id),
            job_id=str(self.job_id),
            schema_version=self.get_schema_version(),
            source_file=file_path.name,
        )

    def _linhas(self, bloco: pd.DataFrame, mapa: dict[str, str | None]) -> Any:
        n = len(bloco)
        vazio = [""] * n
        col = {
            campo: (bloco[nome].tolist() if nome and nome in bloco.columns else vazio)
            for campo, nome in mapa.items()
        }
        todas = [bloco[c].tolist() for c in bloco.columns]
        for i in range(n):
            valores = {campo: str(col[campo][i] or "") for campo in col}
            categoria = valores["meter_category"]
            servico = valores["service"] or categoria or "unknown"
            descritor = " | ".join(
                p for p in (categoria, valores["meter_subcategory"], valores["meter_name"]) if p
            )
            unidade, mult = normalizar_unidade(valores["unit"])
            quantidade = _dec(valores["quantity"])
            if quantidade is not None:
                quantidade *= mult
            # Preço de lista vale para a quantidade NA UNIDADE DO MEDIDOR (disco-mês);
            # depois da conversão para GB-mês, multiplicar dava sob demanda x1024.
            preco_lista = _dec(valores["payg_price"])
            sob_demanda = preco_lista * quantidade if preco_lista and quantidade else None
            unidade, quantidade = _disco_em_gb(valores, unidade, quantidade)
            custo = _dec(valores["cost"]) or _ZERO
            dia = _data(valores["date"])
            info = dict(_info(valores["additional_info"]))
            tamanho = info.get("ServiceType") or None
            atributos: dict[str, str] = {}
            if tamanho and tamanho.casefold().startswith("standard_"):
                atributos["instance_type"] = tamanho
                familia = familia_vm(tamanho)
                if familia:
                    atributos["instance_family"] = familia
            vcpus = _dec(info.get("VCPUs"))
            if vcpus is not None and vcpus > 0:
                atributos["vcpus"] = str(vcpus)
            if valores["subscription"]:
                atributos["project_id"] = valores["subscription"]
            if valores["resource_group"]:
                atributos["resource_group"] = valores["resource_group"]
            if categoria.casefold() == _CATEGORIA_VM and unidade == "hours" and tamanho:
                atributos["capacity_unit"] = "instance"
            if "snapshot" in descritor.casefold():
                atributos["storage_kind"] = "snapshot"
            periodo = valores["billing_period_start"][:7] if valores["billing_period_start"] else ""
            if len(periodo) != 7 or periodo[4] != "-":  # noqa: PLR2004 - "YYYY-MM"
                periodo = dia.strftime("%Y-%m") if dia else "0000-00"
            natureza = _natureza(valores["charge_type"], valores["pricing_model"])
            yield {
                "billing_period": periodo,
                "account_id": valores["subscription"],
                "account_name": valores["subscription_name"] or None,
                "region": _regiao(valores["location"]),
                "resource_id": valores["resource"].casefold() or None,
                "service": servico,
                "service_raw": servico,
                "usage_type": descritor or None,
                "operation": valores["pricing_model"] or None,
                "resource_type": atributos.get("instance_type"),
                "charge_kind": natureza.value,
                "usage_unit": unidade,
                "currency": (valores["currency"] or "USD").upper()[:3],
                "cost_basis": "amortized",
                "cost": custo,
                "cost_billed": custo,
                "cost_discounted": custo,
                "usage_amount": quantidade,
                "on_demand": sob_demanda,
                "usage_start": dia,
                "usage_end": dia,
                "is_estimate": False,
                "is_correction": False,
                "tags": dict(_tags(valores["tags"])),
                "attributes": atributos,
                "identity": hashlib.blake2b(
                    "\x1f".join(str(c[i]) for c in todas).encode(), digest_size=16
                ).hexdigest(),
                "line_type": valores["charge_type"] or "Usage",
                "raw": {
                    "MeterCategory": categoria or None,
                    "MeterSubCategory": valores["meter_subcategory"] or None,
                    "MeterName": valores["meter_name"] or None,
                    "ChargeType": valores["charge_type"] or None,
                    "PricingModel": valores["pricing_model"] or None,
                },
            }
