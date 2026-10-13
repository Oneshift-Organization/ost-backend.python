"""Cliente da Azure Retail Prices API (pública, sem auth) — Wave 3.

Busca preços on-demand de Virtual Machines para uma região e devolve
``CloudPricePoint`` já com specs (via ``parse_azure_shape``). Filtra para Linux
pay-as-you-go: exclui Windows, Spot e Low Priority. Sem dependência externa
(urllib da stdlib) — roda num sync offline, nunca no caminho crítico.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.services.azure_shape import parse_azure_shape

_BASE = "https://prices.azure.com/api/retail/prices"
_SOURCE = "azure_retail_api"
_HTTP_TOO_MANY_REQUESTS = 429
# Famílias workhorse de propósito geral/compute/memória. Especializadas (M mem
# extrema, L storage, N GPU, H HPC, G) têm preço distorcido para compute comum.
_GENERAL_FAMILIES = frozenset({"b", "d", "e", "f"})
# Computação confidencial (DCsv3, ECasv5): enclave e memória cifrada, preço de
# especializada. Começa com D e E, então passava no filtro de família e virava
# destino de pool comum.
_CONFIDENCIAIS = frozenset({"dc", "ec"})
_SERIE = re.compile(r"^Standard_([A-Za-z]+)\d")


def _serie(arm: str) -> str:
    """`Standard_DC2s_v3` → `dc`; `Standard_D4s_v5` → `d`."""
    m = _SERIE.match(arm)
    return m.group(1).lower() if m else ""


def _espera(tentativa: int, retry_after: str | None) -> float:
    """Segundos até a próxima tentativa: o `Retry-After` da API, se vier."""
    try:
        return min(float(retry_after), 60.0) if retry_after else float(2**tentativa)
    except ValueError:
        return float(2**tentativa)


def _get_json(url: str, *, timeout: float, retries: int = 6) -> dict[str, Any]:
    """GET com User-Agent e backoff em 429 e em timeout (a API rate-limita).

    Uma coleta de cinco regiões faz ~40 chamadas seguidas e a API responde 429
    ou segura a conexão até estourar o tempo. Desistir na primeira derrubava o
    sync inteiro.
    """
    req = urllib.request.Request(  # noqa: S310 - host fixo (prices.azure.com)
        url, headers={"User-Agent": "oneshift-pricing/1.0"}
    )
    for attempt in range(retries):
        ultima = attempt == retries - 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host fixo
                data: dict[str, Any] = json.loads(resp.read())
                return data
        except urllib.error.HTTPError as e:
            if e.code != _HTTP_TOO_MANY_REQUESTS or ultima:
                raise
            time.sleep(_espera(attempt, e.headers.get("Retry-After")))
        except TimeoutError:
            if ultima:
                raise
            time.sleep(_espera(attempt, None))
    return {}


def _excluded(item: dict[str, Any]) -> bool:
    product = (item.get("productName") or "")
    sku = (item.get("skuName") or "") + " " + (item.get("meterName") or "")
    if "Windows" in product:
        return True
    if "Spot" in sku or "Low Priority" in sku:
        return True
    if (item.get("unitOfMeasure") or "") != "1 Hour":
        return True
    return item.get("type") != "Consumption"


def fetch_azure_vm_prices(
    region: str = "eastus",
    *,
    max_pages: int = 8,
    timeout: float = 30.0,
) -> list[CloudPricePoint]:
    """Preços on-demand de VMs Linux numa região. Dedup por armSkuName (menor preço)."""
    flt = (
        f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption'"
    )
    url: str | None = f"{_BASE}?{urllib.parse.urlencode({'$filter': flt})}"
    now = datetime.now(UTC)
    best: dict[str, CloudPricePoint] = {}
    pages = 0

    while url and pages < max_pages:
        payload = _get_json(url, timeout=timeout)
        for item in payload.get("Items", []):
            if _excluded(item):
                continue
            arm = item.get("armSkuName") or ""
            # SKUs "constrained" (Standard_DS12-1_v2) pagam a instância cheia por
            # menos vCPU — preço/vCPU distorcido; fora para packing de compute.
            if "-" in arm:
                continue
            shape = parse_azure_shape(arm)
            if shape is None:
                continue
            family = arm.split("_", 1)[1][:1].lower() if "_" in arm else ""
            if family not in _GENERAL_FAMILIES:
                continue
            if _serie(arm) in _CONFIDENCIAIS:
                continue
            price = Decimal(str(item.get("retailPrice") or 0))
            if price <= 0:
                continue
            existing = best.get(arm)
            if existing is not None and existing.price <= price:
                continue
            best[arm] = CloudPricePoint(
                provider="azure",
                sku_key=arm,
                service_name="Azure Virtual Machines",
                service_type="virtual_server",
                region=region,
                price=price,
                currency=item.get("currencyCode") or "USD",
                unit="instance_hour",
                vcpus=shape.vcpus,
                ram_gib=shape.ram_gib,
                source=_SOURCE,
                confidence="official",
                captured_at=now,
            )
        url = payload.get("NextPageLink")
        pages += 1

    return list(best.values())
