"""Matcher prefere preços reais (cloud_price_points) ao catálogo seed (Wave 3).

Invariante: quando há price point oficial para um provedor, o candidato e a
confiança vêm dele; senão, cai no catálogo seed com confiança 'low'.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.services.equivalence_matcher import EquivalenceMatcher


class _SkuRepo(ICloudSkuRepository):
    def __init__(self, by_provider: dict[str, list[CloudSku]]) -> None:
        self._by = by_provider

    async def get_by_id(self, provider: str, sku_id: str) -> CloudSku | None:
        return None

    async def find_equivalents(
        self, *, provider, service_type, min_vcpus=None, min_ram_gb=None, limit=10
    ):
        return self._by.get(provider, [])

    async def list_by_provider(self, provider: str) -> list[CloudSku]:
        return self._by.get(provider, [])

    async def list_providers(self) -> list[str]:
        return list(self._by)

    async def bulk_upsert(self, skus):
        return len(skus), 0


class _PriceRepo(ICloudPricePointRepository):
    def __init__(self, by_provider: dict[str, list[CloudPricePoint]]) -> None:
        self._by = by_provider

    async def find(self, *, provider, service_type, region=None):
        return self._by.get(provider, [])

    async def rates_for_category(self, category: str):
        return []

    async def providers_with_prices(self, service_type: str) -> list[str]:
        return list(self._by)

    async def bulk_upsert(self, points):
        return len(points), 0


def _sku(price: str) -> CloudSku:
    return CloudSku(
        sku_id="seed-vm", provider="azure", service_name="Azure VM", currency="USD",
        service_type="virtual_server", category="compute",
        price_per_unit=Decimal(price), vcpus=Decimal("4"), ram_gb=Decimal("16"),
    )


def _point(price: str) -> CloudPricePoint:
    return CloudPricePoint(
        provider="azure", sku_key="Standard_D4s_v5", service_name="Azure Virtual Machines",
        service_type="virtual_server", region="eastus", price=Decimal(price), currency="USD",
        unit="instance_hour", vcpus=Decimal("4"), ram_gib=Decimal("16"),
        source="azure_retail_api", confidence="official", captured_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_prefers_price_point_over_seed() -> None:
    matcher = EquivalenceMatcher(
        _SkuRepo({"azure": [_sku("9.99")]}),   # seed caro/errado
        _PriceRepo({"azure": [_point("0.20")]}),  # preço real
    )
    opts = await matcher.match_pool(
        vcpus=Decimal("4"), ram_gib=Decimal("16"), monthly_cost=Decimal("200"),
        service_type="virtual_server", target_providers=["azure"],
    )
    assert len(opts) == 1
    assert opts[0].sku_id == "Standard_D4s_v5"          # veio do price point
    assert opts[0].financials.pricing_confidence == "official"
    # 0.20/h * 730h = 146 (não o seed de 9.99)
    assert opts[0].financials.estimated_monthly_cost == Decimal("146.00")


@pytest.mark.asyncio
async def test_falls_back_to_seed_when_no_price_point() -> None:
    matcher = EquivalenceMatcher(
        _SkuRepo({"gcp": [_sku("0.50")]}),
        _PriceRepo({}),  # sem preço real para gcp
    )
    opts = await matcher.match_pool(
        vcpus=Decimal("4"), ram_gib=Decimal("16"), monthly_cost=Decimal("200"),
        service_type="virtual_server", target_providers=["gcp"],
    )
    assert len(opts) == 1
    assert opts[0].financials.pricing_confidence == "low"


@pytest.mark.asyncio
async def test_works_without_price_repo() -> None:
    """Sem price_repo (None), comporta-se como antes: só catálogo, confiança low."""
    matcher = EquivalenceMatcher(_SkuRepo({"oci": [_sku("0.30")]}))
    opts = await matcher.match_pool(
        vcpus=Decimal("4"), ram_gib=Decimal("16"), monthly_cost=Decimal("200"),
        service_type="virtual_server", target_providers=["oci"],
    )
    assert len(opts) == 1
    assert opts[0].financials.pricing_confidence == "low"


@pytest.mark.asyncio
async def test_empate_prefere_menos_instancias() -> None:
    """Dois SKUs cobrem a mesma capacidade pelo MESMO custo: 16vCPU x2 e
    4vCPU x8. O score empata; o desempate tem que ser por menos máquinas —
    sem isso o vencedor era quem viesse primeiro do banco (não-reproduzível)."""
    grande = CloudPricePoint(
        provider="azure", sku_key="Standard_D16", service_name="Azure VM",
        service_type="virtual_server", region="eastus", price=Decimal("0.40"),
        currency="USD", unit="instance_hour", vcpus=Decimal("16"), ram_gib=Decimal("64"),
    )
    pequeno = CloudPricePoint(
        provider="azure", sku_key="Standard_D4", service_name="Azure VM",
        service_type="virtual_server", region="eastus", price=Decimal("0.10"),
        currency="USD", unit="instance_hour", vcpus=Decimal("4"), ram_gib=Decimal("16"),
    )
    # Nas duas ordens de entrada o resultado tem que ser o mesmo.
    for candidatos in ([grande, pequeno], [pequeno, grande]):
        matcher = EquivalenceMatcher(_SkuRepo({}), _PriceRepo({"azure": candidatos}))
        opts = await matcher.match_pool(
            vcpus=Decimal("32"), ram_gib=Decimal("128"), monthly_cost=Decimal("1000"),
            service_type="virtual_server", target_providers=["azure"],
        )
        assert opts[0].instances == 2, "deveria preferir 2x16vCPU a 8x4vCPU"
        assert opts[0].sku_id == "Standard_D16"
