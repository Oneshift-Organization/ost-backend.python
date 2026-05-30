from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.domain.entities.workload_pool import (
    AuxiliaryCosts,
    PoolCapacity,
    PoolCosts,
    PoolKey,
    WorkloadPool,
)
from app.domain.services.sku_normalization import normalize_provider

if TYPE_CHECKING:
    from app.domain.entities.billing_enriched import BillingEnriched
    from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository

logger = logging.getLogger(__name__)

_HOURS_IN_MONTH = Decimal("730")
_GCP_FAMILIES = frozenset({
    "e2", "n1", "n2", "n2d", "n4", "n4d",
    "c2", "c2d", "c3",
    "m1", "m2", "m3",
    "g2", "z3", "t2d", "t2a", "h3",
})

_FAMILY_PATTERN = re.compile(
    r"(?P<family>" + "|".join(sorted(_GCP_FAMILIES, key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


def _extract_machine_family(description: str | None) -> str | None:
    if not description:
        return None
    m = _FAMILY_PATTERN.search(description)
    if m:
        return m.group("family").upper()
    if "compute optimized" in description.lower():
        return "C2"
    if "memory optimized" in description.lower() or "memory-optimized" in description.lower():
        return "M3"
    return None


def _is_gcp_core(desc: str | None, unit: str | None) -> bool:
    unit_lower = (unit or "").lower()
    desc_lower = (desc or "").lower()
    if "core" in desc_lower and ("hour" in unit_lower or "minute" in unit_lower):
        return True
    return False


def _is_gcp_ram(desc: str | None, unit: str | None) -> bool:
    unit_lower = (unit or "").lower()
    desc_lower = (desc or "").lower()
    if "ram" in desc_lower and ("gibibyte" in unit_lower or "gibyte" in unit_lower):
        return True
    return False


def _is_gke_control_plane(desc: str | None, service_name: str | None) -> bool:
    name = (service_name or "").lower()
    desc_lower = (desc or "").lower()
    if "kubernetes" in name and ("cluster" in desc_lower or "control" in desc_lower or "fee" in desc_lower):
        return True
    return False


def _is_os_license(desc: str | None, service_type: str | None) -> bool:
    if not desc:
        return False
    desc_lower = desc.lower()
    return any(kw in desc_lower for kw in ("windows", "sles", "rhel", "ubuntu pro"))


def _default_family(desc: str | None) -> str:
    family = _extract_machine_family(desc)
    return family or "general"


@dataclass
class AggregateResult:
    pools: tuple[WorkloadPool, ...]
    auxiliary: AuxiliaryCosts
    unmapped_cost: Decimal
    hours_window: Decimal


class BillingAggregator:
    """Converts BillingEnriched lines into WorkloadPools + auxiliary buckets.

    FOCUS-aligned: consumption (usage) is separated from cost.
    Capacity is derived by dividing usage hours by the billing window.
    """

    def __init__(self, sku_repo: ICloudSkuRepository | None = None) -> None:
        self._sku_repo = sku_repo
        self._catalog_cache: dict[str, dict[str, Any]] | None = None

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
            )

        catalog = await self._load_catalog(source_provider)

        # usage window from first/last usage_start if available
        hours_window = _HOURS_IN_MONTH  # default; can be refined

        pool_buckets: dict[PoolKey, dict[str, Decimal]] = defaultdict(
            lambda: {"core_hours": Decimal("0"), "ram_gib_hours": Decimal("0"),
                     "compute_cost": Decimal("0"), "license_cost": Decimal("0")}
        )
        gke_cost = Decimal("0")
        storage_ssd = Decimal("0")
        storage_hdd = Decimal("0")
        network_egress = Decimal("0")
        orphan_cost = Decimal("0")
        orphan_skus: list[str] = []

        for item in items:
            cost = item.cost
            cat_entry = catalog.get(item.sku_id or "")
            desc = (cat_entry or {}).get("description") if cat_entry else None
            svc_type = cat_entry.get("service_type") if cat_entry else item.service_type
            cat = cat_entry.get("category") if cat_entry else item.category
            unit = item.usage_unit or ""

            # classify
            if _is_gke_control_plane(desc, item.service_name):
                gke_cost += cost
            elif _is_gcp_core(desc, unit) or (svc_type == "virtual_server" and "hour" in unit.lower() and "gibibyte" not in unit.lower() and item.sku_id and catalog.get(item.sku_id, {}).get("vcpus", 0) > 0):
                family = _default_family(desc)
                key = PoolKey(
                    project_id=item.project_id or "_unknown",
                    machine_family=family,
                    region=item.region or "global",
                )
                pool_buckets[key]["core_hours"] += item.usage_amount or Decimal("0")
                pool_buckets[key]["compute_cost"] += cost
            elif _is_gcp_ram(desc, unit) or (svc_type == "virtual_server" and "gibibyte" in unit.lower() and item.sku_id and catalog.get(item.sku_id, {}).get("ram_gb", 0) > 0):
                family = _default_family(desc)
                key = PoolKey(
                    project_id=item.project_id or "_unknown",
                    machine_family=family,
                    region=item.region or "global",
                )
                pool_buckets[key]["ram_gib_hours"] += item.usage_amount or Decimal("0")
                pool_buckets[key]["compute_cost"] += cost
            elif _is_os_license(desc, svc_type):
                family = _default_family(desc)
                key = PoolKey(
                    project_id=item.project_id or "_unknown",
                    machine_family=family,
                    region=item.region or "global",
                )
                pool_buckets[key]["license_cost"] += cost
            elif cat in ("networking", "networking_generic") or ("network" in (desc or "").lower() and "transfer" in (desc or "").lower()):
                network_egress += cost
            elif "storage" in (cat or "").lower() or "file" in (svc_type or "").lower():
                if "ssd" in (desc or "").lower():
                    storage_ssd += cost
                else:
                    storage_hdd += cost
            else:
                orphan_cost += cost
                if item.sku_id:
                    orphan_skus.append(item.sku_id)

        pools: list[WorkloadPool] = []
        for key, bucket in pool_buckets.items():
            vcpus = bucket["core_hours"] / hours_window if hours_window > 0 else Decimal("0")
            ram_gib = bucket["ram_gib_hours"] / hours_window if hours_window > 0 else Decimal("0")
            pools.append(WorkloadPool(
                pool_key=key,
                capacity=PoolCapacity(vcpus=vcpus, ram_gib=ram_gib),
                costs=PoolCosts(
                    compute_base=bucket["compute_cost"],
                    os_licensing=bucket["license_cost"],
                ),
                hours_window=hours_window,
            ))

        return AggregateResult(
            pools=tuple(pools),
            auxiliary=AuxiliaryCosts(
                gke_control_plane=gke_cost,
                storage_ssd=storage_ssd,
                storage_hdd=storage_hdd,
                network_egress=network_egress,
                orphan=orphan_cost,
                orphan_skus=tuple(sorted(set(orphan_skus))),
            ),
            unmapped_cost=orphan_cost,
            hours_window=hours_window,
        )

    async def _load_catalog(self, provider: str) -> dict[str, dict[str, Any]]:
        if self._sku_repo is None:
            return {}
        if self._catalog_cache is not None:
            return self._catalog_cache
        entries = await self._sku_repo.list_by_provider(normalize_provider(provider))
        catalog: dict[str, dict[str, Any]] = {}
        for entry in entries:
            desc = (entry.attributes or {}).get("description", "") if entry.attributes else ""
            catalog[entry.sku_id] = {
                "description": desc,
                "service_type": entry.service_type,
                "category": entry.category,
                "vcpus": entry.vcpus or Decimal("0"),
                "ram_gb": entry.ram_gb or Decimal("0"),
            }
        self._catalog_cache = catalog
        return catalog


