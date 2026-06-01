"""Tests for _aggregate_pools_by_family (W6).

Invariants:
- All source projects preserved in source_projects lists
- vcpus/ram/costs sum correctly
- Equivalents aggregated: cost_delta = sum of constituent deltas
- No duplication (same provider+service counted once per family)
- Families with no equivalents in missing list
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.use_cases.analysis.run_analysis_pipeline import _aggregate_pools_by_family

# ── minimal fixtures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _PoolKey:
    project_id: str
    machine_family: str
    region: str


@dataclass(frozen=True)
class _Capacity:
    vcpus: Decimal
    ram_gib: Decimal


@dataclass(frozen=True)
class _Costs:
    compute_base: Decimal
    os_licensing: Decimal = Decimal("0")


@dataclass(frozen=True)
class _Pool:
    pool_key: _PoolKey
    capacity: _Capacity
    costs: _Costs

    @property
    def monthly_cost(self) -> Decimal:
        return self.costs.compute_base + self.costs.os_licensing


@dataclass(frozen=True)
class _TM:
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None


@dataclass(frozen=True)
class _Fin:
    estimated_monthly_cost: Decimal | None = None
    cost_delta: Decimal | None = None
    pricing_confidence: str = "low"


@dataclass(frozen=True)
class _Equiv:
    provider: str
    service_name: str
    category: str = "compute"
    service_type: str = "virtual_server"
    match_basis: str = "pool_capacity"
    instances: int = 1
    metadata: dict | None = None
    docs_costs: str | None = None
    technical_match: _TM | None = None
    financials: _Fin | None = None


def _pool(proj: str, family: str, vcpus: float, cost: float) -> _Pool:
    return _Pool(
        pool_key=_PoolKey(proj, family, "global"),
        capacity=_Capacity(Decimal(str(vcpus)), Decimal(str(vcpus * 4))),
        costs=_Costs(Decimal(str(cost))),
    )


def _equiv(provider: str, delta: float) -> _Equiv:
    est = abs(float(Decimal(str(1000)) + Decimal(str(delta))))
    return _Equiv(
        provider=provider,
        service_name="AmazonEC2" if provider == "AWS" else "Compute",
        financials=_Fin(
            estimated_monthly_cost=Decimal(str(est)),
            cost_delta=Decimal(str(delta)),
        ),
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_three_e2_projects_collapse_to_one_family():
    pools = tuple([
        _pool("proj-001", "E2", 13.2, 1971.0),
        _pool("proj-002", "E2", 4.7, 706.0),
        _pool("proj-003", "E2", 2.7, 389.0),
    ])
    result, missing = _aggregate_pools_by_family(pools, {})

    assert len(result) == 1
    fam = result[0]
    assert fam["family"] == "E2"
    assert set(fam["source_projects"]) == {"proj-001", "proj-002", "proj-003"}
    assert round(fam["capacity"]["vcpus"], 1) == 20.6
    assert round(fam["costs"]["compute_base"], 1) == 3066.0
    assert fam["equivalents"] == []
    assert "E2/global" in missing


def test_two_families_stay_separate():
    pools = tuple([
        _pool("proj-001", "E2", 13.2, 1971.0),
        _pool("proj-001", "N2", 0.8, 178.0),
    ])
    result, _ = _aggregate_pools_by_family(pools, {})

    families = {r["family"] for r in result}
    assert families == {"E2", "N2"}


def test_equivalents_cost_delta_summed():
    pools = tuple([
        _pool("proj-001", "E2", 13.2, 1971.0),
        _pool("proj-002", "E2", 4.7, 706.0),
    ])
    pool_equiv = {
        "proj-001/E2/global": [_equiv("AWS", -1574.0)],
        "proj-002/E2/global": [_equiv("AWS", -647.0)],
    }
    result, missing = _aggregate_pools_by_family(pools, pool_equiv)

    assert len(result) == 1
    equivs = result[0]["equivalents"]
    assert len(equivs) == 1  # one AWS entry, not two
    assert equivs[0]["provider"] == "AWS"
    fin = equivs[0]["financials"]
    delta = Decimal(str(fin["cost_delta"]))
    assert delta == Decimal("-2221.0")  # -1574 + -647
    assert missing == []


def test_no_duplication_same_provider_different_projects():
    pools = tuple([
        _pool("proj-001", "E2", 13.2, 1971.0),
        _pool("proj-002", "E2", 4.7, 706.0),
        _pool("proj-003", "E2", 2.7, 389.0),
    ])
    pool_equiv = {
        "proj-001/E2/global": [_equiv("AWS", -1574.0), _equiv("OCI", -1685.0)],
        "proj-002/E2/global": [_equiv("AWS", -647.0), _equiv("OCI", -543.0)],
        "proj-003/E2/global": [_equiv("AWS", -342.0)],
    }
    result, _ = _aggregate_pools_by_family(pools, pool_equiv)

    equivs = result[0]["equivalents"]
    providers = [e["provider"] for e in equivs]
    assert providers.count("AWS") == 1
    assert providers.count("OCI") == 1


def test_source_projects_completeness():
    projects = [f"proj-{i:03d}" for i in range(10)]
    pools = tuple([_pool(p, "E2", 1.0, 100.0) for p in projects])
    result, _ = _aggregate_pools_by_family(pools, {})

    assert set(result[0]["source_projects"]) == set(projects)


def test_vcpus_sum_invariant():
    pools = tuple([
        _pool("proj-001", "E2", 13.2, 1971.0),
        _pool("proj-017", "E2", 4.7, 706.0),
        _pool("proj-009", "E2", 2.7, 389.0),
    ])
    result, _ = _aggregate_pools_by_family(pools, {})

    expected_total = Decimal("13.2") + Decimal("4.7") + Decimal("2.7")
    assert round(Decimal(str(result[0]["capacity"]["vcpus"])), 1) == expected_total


def test_zero_capacity_pools_excluded():
    pools = tuple([
        _pool("proj-001", "E2", 0.0, 0.0),
        _pool("proj-002", "N2", 2.0, 200.0),
    ])
    result, _ = _aggregate_pools_by_family(pools, {})

    assert len(result) == 1
    assert result[0]["family"] == "N2"
