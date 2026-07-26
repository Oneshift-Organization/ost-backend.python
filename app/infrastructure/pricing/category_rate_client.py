"""Fetch de taxas de categoria (storage/egress) das APIs oficiais — Wave 4b.

Sobe as taxas verificáveis de `reference` para `official` (auto-atualizáveis),
simétrico aos fetchers de compute. Só busca o que mapeia limpo para $/GB(-mês):

- Azure egress: Retail API, "Standard Data Transfer Out" (por GB).
- OCI block storage + egress: cetools (por GB-mês / por GB).

Azure managed disk é por-tier (não linear em GB) e AWS/GCP não têm API pública
sem auth — essas permanecem `reference`, honestamente rotuladas. urllib stdlib.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint

_AZURE = "https://prices.azure.com/api/retail/prices"
_OCI = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD"


def _get(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 - hosts fixos (azure/oracle)
        url, headers={"User-Agent": "oneshift-pricing/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data: dict[str, Any] = json.loads(resp.read())
    return data


def _point(provider: str, category: str, unit: str, rate: Decimal, source: str) -> CloudPricePoint:
    return CloudPricePoint(
        provider=provider,
        sku_key=f"{provider}-{category}-live",
        service_name=f"{provider} {category}",
        service_type=category,
        region="us-east",
        price=rate,
        currency="USD",
        unit=unit,
        vcpus=None,
        ram_gib=None,
        source=source,
        confidence="official",
        captured_at=datetime.now(UTC),
    )


def fetch_azure_category_rates(
    region: str = "eastus", *, timeout: float = 30.0
) -> list[CloudPricePoint]:
    """Egress Azure (Standard Data Transfer Out), tier inicial (o mais caro).

    A tarifa de egress cai por volume; a primeira faixa é a mais alta. Pegamos o
    maior tier padrão como estimativa conservadora (o que a maioria dos custos
    encontra primeiro), não o mínimo de alto volume."""
    flt = (
        f"serviceName eq 'Bandwidth' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    url = f"{_AZURE}?{urllib.parse.urlencode({'$filter': flt})}"
    egress: Decimal | None = None
    for item in _get(url, timeout).get("Items", []):
        if item.get("meterName") != "Standard Data Transfer Out":
            continue
        if item.get("unitOfMeasure") != "1 GB":
            continue
        price = Decimal(str(item.get("retailPrice") or 0))
        if price > 0 and (egress is None or price > egress):
            egress = price
    out: list[CloudPricePoint] = []
    if egress is not None:
        out.append(_point("azure", "network_egress", "gb", egress, "azure_retail_api"))
    return out


def fetch_oci_category_rates(*, timeout: float = 30.0) -> list[CloudPricePoint]:
    """Block volume ($/GB-mês) e egress ($/GB) OCI, das linhas estáveis."""
    items = _get(_OCI, timeout).get("items", [])
    block: Decimal | None = None
    egress: Decimal | None = None
    for it in items:
        name = it.get("displayName") or ""
        prices = (it.get("currencyCodeLocalizations") or [{}])[0].get("prices") or []
        if not prices:
            continue
        value = Decimal(str(prices[0].get("value")))
        if name == "Storage - Block Volume - Storage" and value > 0:
            block = value
        elif name.startswith("Outbound Data Transfer") and value > 0 and egress is None:
            egress = value
    out: list[CloudPricePoint] = []
    if block is not None:
        # Block balanced serve como tier única de SSD e HDD no OCI.
        out.append(_point("oci", "storage_ssd", "gb_month", block, "oci_cetools_api"))
        out.append(_point("oci", "storage_hdd", "gb_month", block, "oci_cetools_api"))
    if egress is not None:
        out.append(_point("oci", "network_egress", "gb", egress, "oci_cetools_api"))
    return out
