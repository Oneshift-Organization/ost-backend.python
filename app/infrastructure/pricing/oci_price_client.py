"""Cliente da OCI Price List API (cetools, pública) — Wave 3b.

OCI precifica compute flex por **OCPU/hora + Memória GB/hora separados** (não
por instância como Azure/AWS). Buscamos as duas taxas por família flex e geramos
**configurações sintéticas** — shapes reais e ordenáveis (VM.Standard.E5.Flex com
N OCPUs e M GB) — cada uma com preço por-instância = N*ocpu + M*mem. Assim o
packer as consome como qualquer candidato, sem lógica OCI espalhada nele.

OCPU = 2 vCPU no x86 (E/X); 1 vCPU no ARM (A, Ampere — sem SMT). Sem dependência
externa (urllib). Roda offline, nunca no caminho crítico.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint

_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD"
_SOURCE = "oci_cetools_api"
# Família flex em "Compute - Standard - <FAM>" (VM). Exclui Bare Metal, Dense IO,
# Ax, Micro, Cloud@Customer, Windows — não são compute VM de propósito geral.
_FAM = re.compile(r"^(?:OCI - )?Compute - Standard - (E\d|A\d|X\d+)\b", re.IGNORECASE)
_EXCLUDE = ("Ax", "Cloud@Customer", "Micro", "Windows", "Dense", "Bare Metal")

# Grade de configurações geradas: OCPUs x (GB de RAM por OCPU). Duas razões cobrem
# ~4 e ~8 GB/vCPU (x86), dando ao packer folga para casar pools variados.
_OCPU_STEPS = (1, 2, 4, 8, 16, 32)
_GB_PER_OCPU = (8, 16)


def _fetch(timeout: float) -> list[dict[str, Any]]:
    req = urllib.request.Request(  # noqa: S310 - host fixo (apexapps.oracle.com)
        _URL, headers={"User-Agent": "oneshift-pricing/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host fixo
        data: dict[str, Any] = json.loads(resp.read())
    return data.get("items") or []


def _flex_rates(items: list[dict[str, Any]]) -> dict[str, dict[str, Decimal]]:
    """Extrai {familia: {ocpu, mem}} das linhas flex (que têm as duas taxas)."""
    rates: dict[str, dict[str, Decimal]] = {}
    for it in items:
        name = it.get("displayName") or ""
        m = _FAM.match(name)
        if not m or any(x in name for x in _EXCLUDE):
            continue
        prices = (it.get("currencyCodeLocalizations") or [{}])[0].get("prices") or []
        if not prices:
            continue
        value = Decimal(str(prices[0].get("value")))
        metric = it.get("metricName") or ""
        bucket = rates.setdefault(m.group(1).upper(), {})
        if "OCPU" in metric:
            bucket["ocpu"] = value
        elif "emory" in metric or "igabyte" in metric:
            bucket["mem"] = value
    return {
        fam: r
        for fam, r in rates.items()
        if r.get("ocpu", Decimal("0")) > 0 and "mem" in r
    }


def fetch_oci_compute_prices(*, timeout: float = 30.0) -> list[CloudPricePoint]:
    """Configs flex sintéticas com preço por-instância derivado das taxas reais."""
    rates = _flex_rates(_fetch(timeout))
    now = datetime.now(UTC)
    out: list[CloudPricePoint] = []

    for fam, r in rates.items():
        ocpu_rate, mem_rate = r["ocpu"], r["mem"]
        vcpu_per_ocpu = 1 if fam.startswith("A") else 2  # ARM não tem SMT
        for ocpu in _OCPU_STEPS:
            for gb_per in _GB_PER_OCPU:
                ram_gb = ocpu * gb_per
                price = ocpu * ocpu_rate + ram_gb * mem_rate
                out.append(
                    CloudPricePoint(
                        provider="oci",
                        sku_key=f"VM.Standard.{fam}.Flex.{ocpu}c{ram_gb}g",
                        service_name="OCI Compute",
                        service_type="virtual_server",
                        region="us-ashburn-1",
                        price=price,
                        currency="USD",
                        unit="instance_hour",
                        vcpus=Decimal(ocpu * vcpu_per_ocpu),
                        ram_gib=Decimal(ram_gb),
                        source=_SOURCE,
                        confidence="official",
                        captured_at=now,
                    )
                )
    return out
