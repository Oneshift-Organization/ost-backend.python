"""Pools por região real no bill agregado (PLANO_PRECO_REGIONAL R2).

O Cost Explorer da AWS e a Cost Table do GCP não têm coluna de região. Antes,
todo pool caía em `global` e era cotado num lugar só. A linha diz onde roda:
prefixo do `UsageType` na AWS, localidade do SKU no GCP.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.services.billing_aggregator import BillingAggregator


def _linha(provider: str, usage_type: str, region: str | None = None) -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider=provider,
        billing_period="2026-03",
        service_name="compute",
        cost=Decimal("100"),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type=usage_type,
        usage_amount=Decimal("730"),
        usage_unit="hours",
        project_id="p1",
        component_type="core",
        mapping_confidence="high",
        region=region,
    )


async def _regioes(linhas: list[BillingEnriched], provider: str) -> dict[str, Decimal]:
    res = await BillingAggregator().aggregate(linhas, provider)
    return {p.pool_key.region: p.costs.compute_base for p in res.pools}


@pytest.mark.asyncio
async def test_aws_separa_sao_paulo_da_virginia() -> None:
    regioes = await _regioes(
        [
            _linha("aws", "BoxUsage:t3a.medium"),
            _linha("aws", "SAE1-BoxUsage:t3a.medium"),
        ],
        "aws",
    )
    assert regioes == {"us-east-1": Decimal("100"), "sa-east-1": Decimal("100")}


@pytest.mark.asyncio
async def test_gcp_le_a_localidade_do_sku() -> None:
    regioes = await _regioes(
        [
            _linha("gcp", "E2 Instance Core running in Americas"),
            _linha("gcp", "E2 Instance Core running in Sao Paulo"),
        ],
        "gcp",
    )
    assert set(regioes) == {"americas", "southamerica-east1"}


@pytest.mark.asyncio
async def test_coluna_do_export_continua_mandando() -> None:
    regioes = await _regioes(
        [_linha("gcp", "E2 Instance Core running in Americas", region="us-east1")], "gcp"
    )
    assert set(regioes) == {"us-east1"}


@pytest.mark.asyncio
async def test_sem_pista_continua_global() -> None:
    regioes = await _regioes([_linha("gcp", "N2 Custom Instance Core")], "gcp")
    assert set(regioes) == {"global"}
