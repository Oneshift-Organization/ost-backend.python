from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.entities.workload_pool import (
    AuxiliaryCosts,
    PoolCapacity,
    PoolCosts,
    PoolKey,
    WorkloadPool,
)
from app.domain.services.sku_matcher import extract_family

if TYPE_CHECKING:
    from app.domain.entities.billing_enriched import BillingEnriched
    from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository

logger = logging.getLogger(__name__)

_HOURS_IN_MONTH = Decimal("730")
_COMPUTE_ROLES = frozenset({"core", "ram", "os_license"})
# component_type -> AuxiliaryCosts scalar bucket name (data-driven dispatch).
_AUX_BUCKET = {
    "gke_control_plane": "gke_control_plane",
    "network_egress": "network_egress",
    "storage_ssd": "storage_ssd",
    "storage_hdd": "storage_hdd",
    "database": "database",
}


@dataclass
class AggregateResult:
    pools: tuple[WorkloadPool, ...]
    auxiliary: AuxiliaryCosts
    unmapped_cost: Decimal
    hours_window: Decimal
    confidence_breakdown: dict[str, Decimal] = field(default_factory=dict)


class BillingAggregator:
    """Converts BillingEnriched lines into WorkloadPools + auxiliary buckets.

    Classification is a dispatch on the line's ``component_type`` (assigned by
    the SkuMatcher) — no regex over descriptions, no per-cloud branching
    (spec §5.3). FOCUS-aligned: consumption (usage) is separated from cost;
    capacity is derived by dividing usage hours by the billing window.
    """

    def __init__(self, sku_repo: ICloudSkuRepository | None = None) -> None:
        # Kept for construction compatibility; classification no longer needs
        # the catalog (component_type lives on the enriched line).
        self._sku_repo = sku_repo

    async def aggregate(
        self,
        items: list[BillingEnriched],
        source_provider: str,
    ) -> AggregateResult:
        if not items:
            return AggregateResult(
                pools=(),
                auxiliary=AuxiliaryCosts(),
                unmapped_cost=Decimal("0"),
                hours_window=_HOURS_IN_MONTH,
                confidence_breakdown={},
            )

        hours_window = _HOURS_IN_MONTH  # default; can be refined by usage window

        pool_buckets: dict[PoolKey, dict[str, Decimal]] = defaultdict(
            lambda: {
                "core_hours": Decimal("0"),
                "ram_gib_hours": Decimal("0"),
                "compute_cost": Decimal("0"),
                "license_cost": Decimal("0"),
            }
        )
        aux_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        orphan_cost = Decimal("0")
        orphan_skus: list[str] = []
        confidence_breakdown: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

        for item in items:
            cost = item.cost
            component_type = item.component_type or "unclassified"
            confidence_breakdown[item.mapping_confidence or "missing"] += cost

            if component_type in _COMPUTE_ROLES:
                self._accumulate_compute(pool_buckets, item, component_type, cost)
            elif component_type in _AUX_BUCKET:
                aux_totals[_AUX_BUCKET[component_type]] += cost
            else:
                orphan_cost += cost
                orphan_skus.append(item.sku_id or item.usage_type or "unknown")

        pools: list[WorkloadPool] = []
        for key, bucket in pool_buckets.items():
            vcpus = bucket["core_hours"] / hours_window if hours_window > 0 else Decimal("0")
            ram_gib = bucket["ram_gib_hours"] / hours_window if hours_window > 0 else Decimal("0")
            pools.append(
                WorkloadPool(
                    pool_key=key,
                    capacity=PoolCapacity(vcpus=vcpus, ram_gib=ram_gib),
                    costs=PoolCosts(
                        compute_base=bucket["compute_cost"],
                        os_licensing=bucket["license_cost"],
                    ),
                    hours_window=hours_window,
                )
            )

        return AggregateResult(
            pools=tuple(pools),
            auxiliary=AuxiliaryCosts(
                gke_control_plane=aux_totals["gke_control_plane"],
                storage_ssd=aux_totals["storage_ssd"],
                storage_hdd=aux_totals["storage_hdd"],
                network_egress=aux_totals["network_egress"],
                database=aux_totals["database"],
                orphan=orphan_cost,
                orphan_skus=tuple(sorted(set(orphan_skus))),
            ),
            unmapped_cost=orphan_cost,
            hours_window=hours_window,
            confidence_breakdown=dict(confidence_breakdown),
        )

    def _accumulate_compute(
        self,
        pool_buckets: dict[PoolKey, dict[str, Decimal]],
        item: BillingEnriched,
        component_type: str,
        cost: Decimal,
    ) -> None:
        key = PoolKey(
            project_id=item.project_id or "_unknown",
            machine_family=self._family_of(item),
            region=item.region or "global",
        )
        bucket = pool_buckets[key]
        if component_type == "core":
            bucket["core_hours"] += item.usage_amount or Decimal("0")
            bucket["compute_cost"] += cost
        elif component_type == "ram":
            bucket["ram_gib_hours"] += item.usage_amount or Decimal("0")
            bucket["compute_cost"] += cost
        else:  # os_license
            bucket["license_cost"] += cost

    @staticmethod
    def _family_of(item: BillingEnriched) -> str:
        metadata = item.sku_metadata or {}
        family = metadata.get("family")
        if isinstance(family, str) and family:
            return family
        return extract_family(item.usage_type) or "general"
