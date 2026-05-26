from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class PoolKey:
    project_id: str
    machine_family: str
    region: str


@dataclass(frozen=True, slots=True)
class PoolCapacity:
    vcpus: Decimal
    ram_gib: Decimal


@dataclass(frozen=True, slots=True)
class PoolCosts:
    compute_base: Decimal
    os_licensing: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class WorkloadPool:
    pool_key: PoolKey
    capacity: PoolCapacity
    costs: PoolCosts
    hours_window: Decimal
    os_license_type: str | None = None
    gke_control_plane_cost: Decimal | None = None

    @property
    def monthly_cost(self) -> Decimal:
        return self.costs.compute_base + self.costs.os_licensing


@dataclass(frozen=True, slots=True)
class AuxiliaryCosts:
    gke_control_plane: Decimal = Decimal("0")
    storage_ssd: Decimal = Decimal("0")
    storage_hdd: Decimal = Decimal("0")
    network_egress: Decimal = Decimal("0")
    orphan: Decimal = Decimal("0")
    orphan_skus: tuple[str, ...] = ()

    @property
    def any_positive(self) -> bool:
        return any((
            self.gke_control_plane > 0,
            self.storage_ssd > 0,
            self.storage_hdd > 0,
            self.network_egress > 0,
            self.orphan > 0,
        ))
