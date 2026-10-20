"""Campos de export detalhado sobrevivem ao banco (E1, migração f6a7b8c9d0e1)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.billing_item import BillingItem
from app.domain.entities.billing_report import BillingReport
from app.domain.value_objects.cloud_provider import CloudProvider
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_USER = uuid4()


async def _novo_report(session) -> BillingReport:
    report = BillingReport(user_id=_USER, source_filename="cur.csv.gz")
    await PgBillingReportRepository(session).save(report)
    return report


async def test_billing_items_ida_e_volta(_db_ready) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        report = await _novo_report(session)
        repo = PgBillingItemRepository(session)
        await repo.bulk_insert(
            [
                BillingItem(
                    user_id=_USER,
                    report_id=report.id,
                    provider=CloudProvider.AWS,
                    service="AmazonEC2",
                    cost=Decimal("12.5"),
                    currency="USD",
                    billing_period="2026-03",
                    resource_id="i-0abc",
                    usage_type="BoxUsage:m7g.2xlarge",
                    resource_type="m7g.2xlarge",
                    charge_kind="usage",
                    cost_billed=Decimal("0"),
                    attributes={"vcpus": "8", "memory_gib": "32"},
                ),
                # Linha do caminho legado: nada dos campos novos.
                BillingItem(
                    user_id=_USER,
                    report_id=report.id,
                    provider=CloudProvider.AWS,
                    service="AmazonS3",
                    cost=Decimal("1"),
                    currency="USD",
                    billing_period="2026-03",
                ),
            ]
        )
        await session.commit()

    try:
        async with sm() as session:
            lidos = await PgBillingItemRepository(session).list_by_report(
                report.id, _USER, limit=10
            )
            por_servico = {i.service: i for i in lidos}
            ec2 = por_servico["AmazonEC2"]
            assert ec2.usage_type == "BoxUsage:m7g.2xlarge"
            assert ec2.resource_type == "m7g.2xlarge"
            assert ec2.charge_kind == "usage"
            assert ec2.cost_billed == Decimal("0")
            assert ec2.attributes == {"vcpus": "8", "memory_gib": "32"}

            legado = por_servico["AmazonS3"]
            assert legado.usage_type is None
            assert legado.charge_kind is None
            assert legado.attributes == {}
            # No banco, a linha legada fica NULL — não `{}`.
            bruto = (
                await session.execute(
                    text(
                        "select attributes is null from billing_items "
                        "where report_id = :r and service = 'AmazonS3'"
                    ),
                    {"r": report.id},
                )
            ).scalar_one()
            assert bruto is True
    finally:
        await _apagar(report.id)


async def test_billing_enriched_ida_e_volta(_db_ready) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        report = await _novo_report(session)
        await PgBillingEnrichedRepository(session).bulk_insert(
            [
                BillingEnriched(
                    user_id=_USER,
                    report_id=report.id,
                    provider="aws",
                    billing_period="2026-03",
                    service_name="AmazonEC2",
                    cost=Decimal("12.5"),
                    currency="USD",
                    source_line_hash="a" * 64,
                    pricing_source="fallback",
                    pricing_confidence="low",
                    resource_id="i-0abc",
                    attributes={"instance_type": "m7g.2xlarge"},
                )
            ]
        )
        await session.commit()

    try:
        async with sm() as session:
            [lido] = await PgBillingEnrichedRepository(session).list_by_report(
                report.id, _USER, limit=10
            )
            assert lido.resource_id == "i-0abc"
            assert lido.attributes == {"instance_type": "m7g.2xlarge"}
    finally:
        await _apagar(report.id)


async def _apagar(report_id) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        for tabela, coluna in (
            ("billing_enriched", "report_id"),
            ("billing_items", "report_id"),
            ("billing_reports", "id"),
        ):
            await session.execute(
                text(f"delete from {tabela} where {coluna} = :r"), {"r": report_id}
            )
        await session.commit()
