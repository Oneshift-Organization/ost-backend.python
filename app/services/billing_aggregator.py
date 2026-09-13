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
from app.domain.services.instance_shape import parse_instance_shape
from app.domain.services.sku_matcher import extract_family

if TYPE_CHECKING:
    from app.domain.entities.billing_enriched import BillingEnriched
    from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository

logger = logging.getLogger(__name__)

_HOURS_IN_MONTH = Decimal("730")
_COMPUTE_ROLES = frozenset({"core", "ram", "os_license"})
# Only accumulate usage into capacity pools when the billing unit is time-based.
# Second-billed units (Cloud Run, serverless) would inflate vcpus by ~3600x.
_HOUR_UNITS = frozenset({"hour", "hours", "hr", "hrs", "h", "minute", "minutes", "min"})
# component_type -> AuxiliaryCosts scalar bucket name (data-driven dispatch).
_AUX_BUCKET = {
    "gke_control_plane": "gke_control_plane",
    "network_egress": "network_egress",
    "storage_ssd": "storage_ssd",
    "storage_hdd": "storage_hdd",
    "object_storage": "object_storage",
    "database": "database",
}


@dataclass
class AggregateResult:
    pools: tuple[WorkloadPool, ...]
    auxiliary: AuxiliaryCosts
    unmapped_cost: Decimal
    hours_window: Decimal
    confidence_breakdown: dict[str, Decimal] = field(default_factory=dict)
    # Custo de compute cuja capacidade foi ESTIMADA por shape (instance-hours →
    # vCPU/RAM), não faturada por-vCPU. Alimenta premissas.dimensionamento_estimado.
    estimated_capacity_cost: Decimal = Decimal("0")


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
                "shaped_cost": Decimal("0"),
            }
        )
        aux_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        commitment_cost = Decimal("0")
        orphan_cost = Decimal("0")
        orphan_skus: list[str] = []
        confidence_breakdown: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

        for item in items:
            cost = item.cost
            component_type = item.component_type or "unclassified"
            confidence_breakdown[item.mapping_confidence or "missing"] += cost

            if component_type in _COMPUTE_ROLES:
                commitment_cost += self._accumulate_compute(
                    pool_buckets, item, component_type, cost
                )
            elif component_type in _AUX_BUCKET:
                aux_totals[_AUX_BUCKET[component_type]] += cost
            else:
                orphan_cost += cost
                orphan_skus.append(item.sku_id or item.usage_type or "unknown")

        pools: list[WorkloadPool] = []
        estimated_capacity_cost = Decimal("0")
        for key, bucket in pool_buckets.items():
            estimated_capacity_cost += bucket["shaped_cost"]
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
                object_storage=aux_totals["object_storage"],
                network_egress=aux_totals["network_egress"],
                database=aux_totals["database"],
                commitment=commitment_cost,
                orphan=orphan_cost,
                orphan_skus=tuple(sorted(set(orphan_skus))),
            ),
            unmapped_cost=orphan_cost,
            hours_window=hours_window,
            confidence_breakdown=dict(confidence_breakdown),
            estimated_capacity_cost=estimated_capacity_cost,
        )

    def _accumulate_compute(
        self,
        pool_buckets: dict[PoolKey, dict[str, Decimal]],
        item: BillingEnriched,
        component_type: str,
        cost: Decimal,
    ) -> Decimal:
        """Acumula uma linha de compute. Retorna o custo que NÃO virou pool.

        Invariante: custo só entra no pool quando a capacidade correspondente
        entrou. Uma linha core/ram cuja unidade não é temporal (ou cujo uso é
        zero) é compromisso/reserva — Savings Plan, RI, CUD — e seu custo é
        devolvido como `commitment` em vez de inflar o preço/vCPU do pool.
        """
        usage = item.usage_amount or Decimal("0")
        unit_parts = (item.usage_unit or "").lower().split()
        unit_base = unit_parts[0] if unit_parts else ""
        is_hour_unit = unit_base in _HOUR_UNITS

        if component_type == "os_license":
            # Licença é custo recorrente legítimo do pool sem unidade de capacidade.
            key = self._pool_key(item)
            pool_buckets[key]["license_cost"] += cost
            return Decimal("0")

        # Faturamento por INSTÂNCIA (AWS BoxUsage): usage é hora-de-instância, não
        # hora-de-vCPU. Uma única linha carrega os dois eixos — expande ambos pelo
        # shape (m7g.2xlarge = 8 vCPU / 32 GiB). Sem shape (ex.: 'N2 Instance Core'
        # do GCP, já por-vCPU), segue o caminho por-unidade abaixo.
        shape = parse_instance_shape(item.usage_type) or parse_instance_shape(
            (item.sku_metadata or {}).get("instance_type")
        )
        if shape is not None and is_hour_unit and usage > 0:
            key = self._pool_key(item)
            bucket = pool_buckets[key]
            bucket["core_hours"] += usage * shape.vcpus
            bucket["ram_gib_hours"] += usage * shape.ram_gib
            bucket["compute_cost"] += cost
            bucket["shaped_cost"] += cost
            return Decimal("0")

        # core / ram: a capacidade só acumula com unidade temporal (core/ram) ou
        # GiB (ram). Sem isso, a linha não representa capacidade viva.
        capacity_accrues = is_hour_unit or (
            component_type == "ram" and "gib" in (item.usage_unit or "").lower()
        )
        if not capacity_accrues or usage <= 0:
            return cost

        key = self._pool_key(item)
        bucket = pool_buckets[key]
        if component_type == "core":
            bucket["core_hours"] += usage
        else:  # ram
            bucket["ram_gib_hours"] += usage
        bucket["compute_cost"] += cost
        return Decimal("0")

    @staticmethod
    def _pool_key(item: BillingEnriched) -> PoolKey:
        return PoolKey(
            project_id=item.project_id or "_unknown",
            machine_family=BillingAggregator._family_of(item),
            region=item.region or "global",
        )

    @staticmethod
    def _family_of(item: BillingEnriched) -> str:
        metadata = item.sku_metadata or {}
        family = metadata.get("family")
        if isinstance(family, str) and family:
            return family
        return extract_family(item.usage_type) or "general"
