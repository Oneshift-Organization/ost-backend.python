"""Agregação de pools por família (Precision Engine v3).

Move a agregação para ANTES do matching. Antes, cada projeto era dimensionado
separadamente e os resultados somados na serialização — o que produzia um
equivalente com ``technical_match`` de um projeto e ``financeiro`` de dezenove
(hardware 2,1x subdimensionado em relação ao custo). Agregando primeiro, o
matcher vê a capacidade real da família e dimensiona uma única vez.

Função pura, sem I/O. Ver ``docs/refactor/precision-engine-v3-spec.md``.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.entities.workload_pool import (
    FamilyPool,
    FamilyPoolKey,
    PoolCapacity,
    PoolCosts,
    WorkloadPool,
)


def aggregate_by_family(pools: tuple[WorkloadPool, ...]) -> tuple[FamilyPool, ...]:
    """Soma pools por (família, região), preservando os projetos de origem.

    Pools sem capacidade (vcpus e ram zerados) são descartados: não há o que
    dimensionar. Seus custos já estão contabilizados nos buckets auxiliares.
    """
    buckets: dict[FamilyPoolKey, dict] = {}

    for p in pools:
        if p.capacity.vcpus <= 0 and p.capacity.ram_gib <= 0:
            continue
        key = FamilyPoolKey(
            machine_family=p.pool_key.machine_family,
            region=p.pool_key.region,
        )
        b = buckets.setdefault(
            key,
            {
                "vcpus": Decimal("0"),
                "ram_gib": Decimal("0"),
                "compute_base": Decimal("0"),
                "os_licensing": Decimal("0"),
                "projects": [],
                "hours_window": p.hours_window,
            },
        )
        b["vcpus"] += p.capacity.vcpus
        b["ram_gib"] += p.capacity.ram_gib
        b["compute_base"] += p.costs.compute_base
        b["os_licensing"] += p.costs.os_licensing
        if p.pool_key.project_id not in b["projects"]:
            b["projects"].append(p.pool_key.project_id)

    return tuple(
        FamilyPool(
            pool_key=key,
            capacity=PoolCapacity(vcpus=b["vcpus"], ram_gib=b["ram_gib"]),
            costs=PoolCosts(
                compute_base=b["compute_base"],
                os_licensing=b["os_licensing"],
            ),
            hours_window=b["hours_window"],
            source_projects=tuple(b["projects"]),
        )
        for key, b in buckets.items()
    )
