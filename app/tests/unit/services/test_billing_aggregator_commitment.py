"""Guarda simétrica capacidade↔custo no BillingAggregator (Precision Engine v3).

Invariante: uma linha com papel de compute (core/ram) só soma custo ao pool se
somou capacidade. Linhas de compromisso/reserva (Savings Plan, RI, CUD) têm
custo mas unidade não-temporal / uso zero — seu custo vai para `commitment`,
nunca infla o preço/vCPU do pool.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.services.billing_aggregator import BillingAggregator


def _line(
    *,
    component_type: str,
    cost: str,
    usage_amount: str | None,
    usage_unit: str | None,
    usage_type: str = "BoxUsage:m7g.2xlarge",
    project_id: str = "p1",
) -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider="aws",
        billing_period="2026-03",
        service_name="AmazonEC2",
        cost=Decimal(cost),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type=usage_type,
        usage_amount=None if usage_amount is None else Decimal(usage_amount),
        usage_unit=usage_unit,
        project_id=project_id,
        component_type=component_type,
        mapping_confidence="high",
    )


@pytest.mark.asyncio
async def test_per_vcpu_line_becomes_pool_capacity() -> None:
    """Linha já faturada por-vCPU (estilo GCP 'N2 Instance Core'): sem shape,
    usage é vCPU-hora direto — não expande."""
    agg = BillingAggregator()
    res = await agg.aggregate(
        [
            _line(
                component_type="core",
                cost="730",
                usage_amount="1460",
                usage_unit="hours",
                usage_type="N2 Instance Core running in Americas",
                project_id="gcp-proj",
            )
        ],
        "gcp",
    )
    assert res.auxiliary.commitment == Decimal("0")
    assert len(res.pools) == 1
    # 1460 core-hours / 730h janela = 2 vCPU (sem expansão de shape)
    assert res.pools[0].capacity.vcpus == Decimal("2")
    assert res.pools[0].costs.compute_base == Decimal("730")


@pytest.mark.asyncio
async def test_boxusage_expanded_by_instance_shape() -> None:
    """Linha por-instância (AWS BoxUsage:m7g.2xlarge): usage é hora-de-instância.
    Expande pelos 8 vCPU / 32 GiB do shape para ambos os eixos."""
    agg = BillingAggregator()
    res = await agg.aggregate(
        [_line(component_type="core", cost="730", usage_amount="730", usage_unit="hours")],
        "aws",
    )
    assert len(res.pools) == 1
    cap = res.pools[0].capacity
    # 730 instance-hours * 8 vCPU / 730h = 8 vCPU; * 32 GiB / 730h = 32 GiB
    assert cap.vcpus == Decimal("8")
    assert cap.ram_gib == Decimal("32")
    # Custo dimensionado por estimativa é rastreado para premissas.
    assert res.estimated_capacity_cost == Decimal("730")


@pytest.mark.asyncio
async def test_commitment_line_does_not_inflate_pool() -> None:
    """Savings Plan: custo alto, sem unidade temporal, uso zero.

    Sem a guarda simétrica, os $8184 entrariam no compute_cost de um pool com
    ~0 vCPU -> preço/vCPU absurdo. Devem ir para `commitment`."""
    agg = BillingAggregator()
    res = await agg.aggregate(
        [
            _line(component_type="core", cost="730", usage_amount="730", usage_unit="hours"),
            _line(
                component_type="core",
                cost="8184",
                usage_amount="0",
                usage_unit=None,
                usage_type="ComputeSP:3yrNoUpfront",
            ),
        ],
        "aws",
    )
    assert res.auxiliary.commitment == Decimal("8184")
    # O pool só carrega o custo da linha com capacidade real.
    total_pool_cost = sum((p.costs.compute_base for p in res.pools), Decimal("0"))
    assert total_pool_cost == Decimal("730")


@pytest.mark.asyncio
async def test_cost_conservation_holds() -> None:
    """Nada some: soma de pools + commitment == custo total das linhas."""
    agg = BillingAggregator()
    lines = [
        _line(component_type="core", cost="100", usage_amount="730", usage_unit="hours"),
        _line(component_type="core", cost="50", usage_amount="0", usage_unit=None),
        _line(component_type="ram", cost="20", usage_amount="730", usage_unit="gibibyte hour"),
    ]
    res = await agg.aggregate(lines, "aws")
    pool_cost = sum((p.monthly_cost for p in res.pools), Decimal("0"))
    assert pool_cost + res.auxiliary.commitment == Decimal("170")
