"""In-memory end-to-end gate for SKU matching accuracy (spec §7/§8).

Wires the *real* GCP seed rules -> SkuMatcher -> BillingEnriched ->
BillingAggregator -> accuracy metrics, with no database. It proves the
classification pipeline reaches >=95% coverage on a representative GCP mix and
that accounting integrity holds (sum of buckets == total). Catalog seed
completeness against a live report is U6's concern; this asserts the *logic*.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.cloud_sku import CloudSku
from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.accuracy_metrics import compute_accuracy
from app.domain.services.sku_matcher import SkuMatcher
from app.infrastructure.database.seed_classification_rules import GCP_RULES
from app.services.billing_aggregator import BillingAggregator

_USER = uuid4()
_REPORT = uuid4()

# (descriptor, unit, usage_amount, cost) — a representative GCP 5d9bc55e mix.
_LINES: list[tuple[str, str, str, str]] = [
    ("E2 Instance Core running in Americas", "hour", "9636", "1971.00"),
    ("E2 Instance Ram running in Americas", "gibibyte hour", "38544", "520.00"),
    ("N2 Instance Core running in Americas", "hour", "584", "177.70"),
    ("N2 Instance Ram running in Americas", "gibibyte hour", "2336", "60.00"),
    ("Kubernetes Clusters Management Fee", "hour", "1460", "106.58"),
    ("Cloud SQL for PostgreSQL: Zonal - vCPU", "hour", "1460", "420.00"),
    ("Network Internet Egress from Americas to EMEA", "gibibyte", "5000", "8329.11"),
    ("SSD backed PD Capacity", "gibibyte month", "1000", "170.00"),
    ("Standard PD Capacity", "gibibyte month", "2000", "80.00"),
    ("Licensing Fee for Windows Server", "hour", "730", "200.00"),
    # genuinely unknown line -> orphan (kept small, < 5% of total)
    ("Mysterious premium support add-on", "unit", "1", "120.00"),
]


def _rules() -> list[SkuClassificationRule]:
    return [
        SkuClassificationRule(
            provider="gcp",
            pattern=pattern,
            kind=kind,
            component_type=component_type,
            family=family,
            confidence=confidence,
            priority=priority,
        )
        for (pattern, kind, component_type, family, confidence, priority) in GCP_RULES
    ]


def _catalog() -> list[CloudSku]:
    return [
        CloudSku(
            sku_id="e2-standard-2", provider="gcp", service_name="Compute Engine",
            currency="USD", service_type="virtual_server", category="compute",
            component_type="unclassified", vcpus=Decimal("2"), ram_gb=Decimal("8"),
        ),
    ]


def _enrich(matcher: SkuMatcher) -> tuple[list[BillingEnriched], Decimal]:
    items: list[BillingEnriched] = []
    total = Decimal("0")
    for descriptor, unit, usage, cost_str in _LINES:
        cost = Decimal(cost_str)
        total += cost
        m = matcher.match(descriptor, usage_unit=unit)
        metadata = {"component_type": m.component_type}
        if m.family:
            metadata["family"] = m.family
        items.append(
            BillingEnriched(
                user_id=_USER,
                report_id=_REPORT,
                provider="gcp",
                billing_period="2025-11",
                service_name=descriptor,
                cost=cost,
                currency="USD",
                source_line_hash=uuid4().hex,
                pricing_source="sku_catalog" if m.sku_id else "fallback",
                pricing_confidence="low",
                usage_type=descriptor,
                usage_amount=Decimal(usage),
                usage_unit=unit,
                region="global",
                project_id="proj-id-001",
                component_type=m.component_type,
                mapping_confidence=m.mapping_confidence,
                match_layer=m.match_layer,
                sku_metadata=metadata,
            )
        )
    return items, total


async def test_pipeline_meets_accuracy_gate() -> None:
    matcher = SkuMatcher(_catalog(), _rules())
    items, total = _enrich(matcher)

    result = await BillingAggregator().aggregate(items, "gcp")
    aux = result.auxiliary

    metrics = compute_accuracy(total, aux.orphan, result.confidence_breakdown)

    # ── Gate: coverage >= 95%, orphan <= 5% ──
    assert metrics.coverage >= Decimal("0.95"), metrics
    assert metrics.orphan_ratio <= Decimal("0.05"), metrics

    # ── GKE control plane and Cloud SQL no longer land in orphan ──
    assert aux.gke_control_plane > 0
    assert aux.database > 0

    # ── Accounting integrity: pools + aux + orphan == total ──
    pools_total = sum((p.monthly_cost for p in result.pools), Decimal("0"))
    aux_total = (
        aux.gke_control_plane
        + aux.storage_ssd
        + aux.storage_hdd
        + aux.network_egress
        + aux.database
        + aux.orphan
    )
    assert pools_total + aux_total == total

    # ── Confidence breakdown is reported and sums to total ──
    assert sum(result.confidence_breakdown.values(), Decimal("0")) == total


async def test_compute_pools_have_capacity() -> None:
    matcher = SkuMatcher(_catalog(), _rules())
    items, _ = _enrich(matcher)
    result = await BillingAggregator().aggregate(items, "gcp")

    # E2 and N2 cores produce non-zero vCPU capacity.
    families = {p.pool_key.machine_family for p in result.pools}
    assert "E2" in families
    assert any(p.capacity.vcpus > 0 for p in result.pools)
