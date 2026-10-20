from decimal import Decimal

from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.services.equivalence_matcher import EquivalenceMatcher


class _MockSkuRepo(ICloudSkuRepository):
    """Minimal mock that replaces ICloudSkuRepository for unit tests."""

    def __init__(
        self,
        get_by_id_result: CloudSku | None = None,
        equivalents_by_provider: dict[str, list[CloudSku]] | None = None,
        list_providers_result: list[str] | None = None,
    ) -> None:
        self.get_by_id_result = get_by_id_result
        self.equivalents_by_provider = equivalents_by_provider or {}
        self.list_providers_result = list_providers_result

    async def get_by_id(self, provider: str, sku_id: str) -> CloudSku | None:
        return self.get_by_id_result

    async def find_equivalents(
        self,
        *,
        provider: str,
        service_type: str,
        min_vcpus: Decimal | None = None,
        min_ram_gb: Decimal | None = None,
        limit: int = 10,
    ) -> list[CloudSku]:
        return self.equivalents_by_provider.get(provider, [])

    async def list_by_provider(self, provider: str) -> list[CloudSku]:
        return self.equivalents_by_provider.get(provider, [])

    async def list_providers(self) -> list[str]:
        return self.list_providers_result or []

    async def bulk_upsert(self, skus: list[CloudSku]) -> tuple[int, int]:
        return len(skus), 0


def _source_sku(
    sku_id: str = "BoxUsage:t3a.medium",
    *,
    provider: str = "aws",
    service_type: str = "virtual_machine",
    vcpus: Decimal | None = Decimal("2"),
    ram_gb: Decimal | None = Decimal("4"),
    price_per_unit: Decimal | None = Decimal("0.037"),
) -> CloudSku:
    return CloudSku(
        sku_id=sku_id,
        provider=provider,
        service_name="EC2",
        service_type=service_type,
        category="compute",
        region="us-east-1",
        usage_unit="Hrs",
        price_per_unit=price_per_unit,
        currency="USD",
        vcpus=vcpus,
        ram_gb=ram_gb,
    )


async def test_equivalence_matcher_exact_and_scaled_match():
    source = _source_sku()
    azure = _source_sku(
        sku_id="Standard_D2as_v5",
        provider="azure",
        vcpus=Decimal("2"),
        ram_gb=Decimal("8"),
        price_per_unit=Decimal("0.048"),
    )
    gcp = _source_sku(
        sku_id="e2-medium",
        provider="gcp",
        vcpus=Decimal("2"),
        ram_gb=Decimal("4"),
        price_per_unit=Decimal("0.033"),
    )

    repo = _MockSkuRepo(
        get_by_id_result=source,
        equivalents_by_provider={
            "azure": [azure],
            "gcp": [gcp],
        },
    )
    matcher = EquivalenceMatcher(sku_repo=repo)

    workload = await matcher.match(
        source_provider="aws",
        sku_id="BoxUsage:t3a.medium",
        usage_quantity=Decimal("1487.56"),
        billed_cost=Decimal("37.73"),
        currency="USD",
        canonical_service="virtual_machine",
        target_providers=["azure", "gcp"],
    )

    assert workload is not None
    assert len(workload.equivalents) == 2

    azure_eq = next(e for e in workload.equivalents if e.provider == "azure")
    assert azure_eq.technical_match is not None
    assert azure_eq.technical_match.vcpus == Decimal("2")
    assert azure_eq.technical_match.ram_gb == Decimal("8")
    assert azure_eq.financials is not None
    assert azure_eq.financials.estimated_monthly_cost == Decimal("71.40288")
    assert azure_eq.financials.cost_delta == Decimal("33.67288")
    assert azure_eq.financials.pricing_confidence == "high (mock)"

    gcp_eq = next(e for e in workload.equivalents if e.provider == "gcp")
    assert gcp_eq.technical_match is not None
    assert gcp_eq.technical_match.vcpus == Decimal("2")
    assert gcp_eq.technical_match.ram_gb == Decimal("4")
    assert gcp_eq.financials is not None
    assert gcp_eq.financials.estimated_monthly_cost == Decimal("49.08948")
    assert gcp_eq.financials.cost_delta == Decimal("11.35948")
    assert gcp_eq.financials.pricing_confidence == "high (mock)"


async def test_get_equivalents_returns_cheapest_per_provider():
    source = _source_sku()
    cheap = _source_sku(
        sku_id="cheap-sku",
        provider="azure",
        vcpus=Decimal("2"),
        ram_gb=Decimal("4"),
        price_per_unit=Decimal("0.030"),
    )
    expensive = _source_sku(
        sku_id="expensive-sku",
        provider="azure",
        vcpus=Decimal("4"),
        ram_gb=Decimal("8"),
        price_per_unit=Decimal("0.060"),
    )

    repo = _MockSkuRepo(
        get_by_id_result=source,
        equivalents_by_provider={
            "azure": [expensive, cheap],
        },
    )
    matcher = EquivalenceMatcher(sku_repo=repo)

    equivalents = await matcher.get_equivalents(
        source_sku_id="BoxUsage:t3a.medium",
        source_provider="aws",
        target_providers=["azure"],
        usage_qty=Decimal("100"),
        source_cost=Decimal("10.00"),
    )

    assert len(equivalents) == 1
    eq = equivalents[0]
    assert eq.provider == "azure"
    assert eq.sku_id == "cheap-sku"
    assert eq.financials is not None
    assert eq.financials.unit_price == Decimal("0.030")
    assert eq.financials.estimated_monthly_cost == Decimal("3.00")
    assert eq.financials.cost_delta == Decimal("-7.00")


async def test_equivalence_matcher_no_match_found_returns_gracefully():
    source = _source_sku(
        sku_id="c7g.metal",
        service_type="bare_metal",
        vcpus=Decimal("64"),
        ram_gb=Decimal("128"),
        price_per_unit=Decimal("4.0"),
    )

    repo = _MockSkuRepo(
        get_by_id_result=source,
        equivalents_by_provider={"azure": [], "gcp": []},
    )
    matcher = EquivalenceMatcher(sku_repo=repo)

    equivalents = await matcher.get_equivalents(
        source_sku_id="c7g.metal",
        source_provider="aws",
        target_providers=["azure", "gcp"],
        usage_qty=Decimal("730"),
        source_cost=Decimal("2920.00"),
    )

    assert len(equivalents) == 0


async def test_equivalence_matcher_source_sku_missing():
    repo = _MockSkuRepo(get_by_id_result=None)
    matcher = EquivalenceMatcher(sku_repo=repo)

    equivalents = await matcher.get_equivalents(
        source_sku_id="UsageType:bizarro",
        source_provider="aws",
        target_providers=["azure", "gcp"],
        usage_qty=Decimal("100"),
        source_cost=Decimal("10.00"),
    )

    assert len(equivalents) == 0


async def test_match_falls_back_to_list_providers():
    source = _source_sku()
    gcp = _source_sku(
        sku_id="e2-medium",
        provider="gcp",
        vcpus=Decimal("2"),
        ram_gb=Decimal("4"),
        price_per_unit=Decimal("0.033"),
    )

    repo = _MockSkuRepo(
        get_by_id_result=source,
        equivalents_by_provider={"gcp": [gcp]},
        list_providers_result=["aws", "gcp"],
    )
    matcher = EquivalenceMatcher(sku_repo=repo)

    workload = await matcher.match(
        source_provider="aws",
        sku_id="BoxUsage:t3a.medium",
        usage_quantity=Decimal("100"),
        billed_cost=Decimal("3.70"),
        currency="USD",
        canonical_service="virtual_machine",
    )

    assert workload is not None
    assert len(workload.equivalents) == 1
    assert workload.equivalents[0].provider == "gcp"


# ── match_pool tests (E1) ──────────────────────────────────────────────────


async def test_match_pool_single_instance_covering_capacity():
    aws_sku = _source_sku(
        sku_id="m7g.2xlarge", provider="aws",
        vcpus=Decimal("8"), ram_gb=Decimal("32"),
        price_per_unit=Decimal("0.272"),
    )
    repo = _MockSkuRepo(equivalents_by_provider={"aws": [aws_sku]})
    matcher = EquivalenceMatcher(sku_repo=repo)

    # pool: 6 vCPU / 24 GiB → one m7g.2xlarge (8/32) covers it → instances = 1
    opts = await matcher.match_pool(
        vcpus=Decimal("6"),
        ram_gib=Decimal("24"),
        monthly_cost=Decimal("1971.00"),
        service_type="virtual_server",
        target_providers=["aws"],
    )

    assert len(opts) == 1
    o = opts[0]
    assert o.provider == "aws"
    assert o.sku_id == "m7g.2xlarge"
    assert o.instances == 1
    assert o.technical_match is not None
    assert o.technical_match.vcpus == Decimal("8")
    assert o.technical_match.ram_gb == Decimal("32")
    assert o.financials is not None
    # Capacidade-hora: o pool ocupa 6/8 = 24/32 = 0,75 da máquina.
    esperado = Decimal("0.272") * Decimal("730") * Decimal("0.75")
    assert o.financials.estimated_monthly_cost == esperado
    assert o.financials.cost_delta is not None
    assert o.financials.cost_delta < 0  # cheaper than source


async def test_match_pool_multiple_instances_when_pool_exceeds_sku():
    small_sku = _source_sku(
        sku_id="e2-standard-4", provider="gcp",
        vcpus=Decimal("4"), ram_gb=Decimal("16"),
        price_per_unit=Decimal("0.134"),
    )
    repo = _MockSkuRepo(equivalents_by_provider={"gcp": [small_sku]})
    matcher = EquivalenceMatcher(sku_repo=repo)

    # pool: 13 vCPU / 52 GiB → 4-vCPU SKU needs ceil(13/4) = 4 instances
    opts = await matcher.match_pool(
        vcpus=Decimal("13"),
        ram_gib=Decimal("52"),
        monthly_cost=Decimal("2000"),
        service_type="virtual_server",
        target_providers=["gcp"],
    )

    assert len(opts) == 1
    o = opts[0]
    assert o.instances == 4
    assert o.technical_match is not None
    assert o.technical_match.vcpus == Decimal("16")  # 4 vcpus * 4 instances
    assert o.financials is not None
    # Provisiona 4, mas cobra as 3,25 instâncias de capacidade que o pool usou.
    expected = Decimal("0.134") * Decimal("730") * Decimal("3.25")
    assert o.financials.estimated_monthly_cost == expected


async def test_match_pool_tiny_pool_pays_used_capacity_on_the_smallest_machine():
    # N1 de São Paulo no bill GCP real: 0,13 vCPU médio, US$ 7,43. Cobrar uma
    # c6a.large inteira ligada 730 h dava US$ 86 (11,6x).
    small = _source_sku(
        sku_id="m7a.medium", provider="aws",
        vcpus=Decimal("1"), ram_gb=Decimal("4"), price_per_unit=Decimal("0.058"),
    )
    big = _source_sku(
        sku_id="m7a.8xlarge", provider="aws",
        vcpus=Decimal("32"), ram_gb=Decimal("128"), price_per_unit=Decimal("1.855"),
    )
    repo = _MockSkuRepo(equivalents_by_provider={"aws": [big, small]})
    matcher = EquivalenceMatcher(sku_repo=repo)

    opts = await matcher.match_pool(
        vcpus=Decimal("0.13"),
        ram_gib=Decimal("0.47"),
        monthly_cost=Decimal("7.43"),
        service_type="virtual_server",
        target_providers=["aws"],
    )

    o = opts[0]
    # A máquina grande sairia quase pelo mesmo custo por capacidade, mas o
    # desperdício de provisionar 32 vCPU para 0,13 a descarta.
    assert (o.sku_id, o.instances) == ("m7a.medium", 1)
    assert o.financials is not None
    esperado = Decimal("0.058") * Decimal("730") * Decimal("0.13")
    assert o.financials.estimated_monthly_cost == esperado


async def test_match_pool_rounding_slack_does_not_provision_a_second_machine():
    # 2,04 GiB medidos num SKU de 2 GiB: a mesma máquina, não duas.
    sku = _source_sku(
        sku_id="t3.small", provider="aws",
        vcpus=Decimal("2"), ram_gb=Decimal("2"), price_per_unit=Decimal("0.0208"),
    )
    repo = _MockSkuRepo(equivalents_by_provider={"aws": [sku]})
    opts = await EquivalenceMatcher(sku_repo=repo).match_pool(
        vcpus=Decimal("0.51"),
        ram_gib=Decimal("2.04"),
        monthly_cost=Decimal("21.24"),
        service_type="virtual_server",
        target_providers=["aws"],
    )
    assert opts[0].instances == 1


async def test_match_pool_no_candidates_returns_empty():
    repo = _MockSkuRepo(equivalents_by_provider={"oci": []})
    matcher = EquivalenceMatcher(sku_repo=repo)

    opts = await matcher.match_pool(
        vcpus=Decimal("8"),
        ram_gib=Decimal("32"),
        monthly_cost=Decimal("500"),
        service_type="virtual_server",
        target_providers=["oci"],
    )

    assert len(opts) == 0


async def test_match_pool_cost_delta_sign():
    cheap = _source_sku(
        sku_id="cheap", provider="oci",
        vcpus=Decimal("8"), ram_gb=Decimal("32"),
        price_per_unit=Decimal("0.10"),  # 73/month
    )
    repo = _MockSkuRepo(equivalents_by_provider={"oci": [cheap]})
    matcher = EquivalenceMatcher(sku_repo=repo)

    opts = await matcher.match_pool(
        vcpus=Decimal("8"),
        ram_gib=Decimal("32"),
        monthly_cost=Decimal("200"),
        service_type="virtual_server",
        target_providers=["oci"],
    )

    assert len(opts) == 1
    assert opts[0].financials is not None
    assert opts[0].financials.cost_delta < 0  # saving
    assert opts[0].financials.cost_delta == Decimal("0.10") * 730 - Decimal("200")
