"""Infra dos testes de integração (Onda 8).

Estes testes exercitam a pipeline INTEIRA contra Postgres real: ingestão →
enriquecimento → contexto → payload. São a rede de segurança dos invariantes que
cada onda do Precision Engine estabeleceu.

**Pulam graciosamente sem banco.** `pytest` local continua rápido e ninguém é
obrigado a subir container para rodar teste unitário; no CI o service container
de Postgres faz os gates valerem.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.domain.entities.billing_report import BillingReport
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.pg_exchange_rate_repo import (
    PgExchangeRateRepository,
)
from app.infrastructure.repositories.pg_sku_classification_rule_repo import (
    PgSkuClassificationRuleRepository,
)
from app.infrastructure.repositories.pg_unmapped_billing_repo import (
    PgUnmappedBillingRepository,
)
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.use_cases.analysis.build_billing_enriched import BuildBillingEnrichedUseCase
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)
from app.use_cases.billing.ingest_billing_file import (
    IngestBillingFileInput,
    IngestBillingFileUseCase,
)

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = UUID("00000000-0000-0000-0000-0000000000e2")

# Tabelas de referência que os gates exigem. Sem elas os testes mediriam o vazio
# e passariam por acidente — melhor pular declarando o motivo.
_REQUIRED_SEEDS = (
    ("cloud_services_comparation", "DE-PARA curado"),
    ("cloud_services_metadata", "metadata de expectativa de migração"),
    ("cloud_sku_catalog", "catálogo de SKUs"),
    ("sku_classification_rules", "regras de classificação"),
)


async def _probe() -> str | None:
    """Devolve o motivo do skip, ou None se o ambiente está pronto."""
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            for table, descricao in _REQUIRED_SEEDS:
                n = (await s.execute(text(f"select count(*) from {table}"))).scalar()
                if not n:
                    return f"seed ausente: {table} ({descricao})"
    except Exception as exc:
        return f"Postgres indisponível: {type(exc).__name__}"
    return None


_THIS_DIR = Path(__file__).parent


def pytest_collection_modifyitems(config, items) -> None:
    """Marca como `integration` só os testes DESTE pacote.

    O hook recebe todos os itens coletados na sessão, não apenas os do
    diretório do conftest — sem o filtro de caminho, a suíte inteira virava
    integração e `-m "not integration"` não rodava nada.
    """
    for item in items:
        if _THIS_DIR in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _db_ready() -> None:
    reason = await _probe()
    if reason:
        pytest.skip(f"integração pulada — {reason}")


async def _ingest(csv: Path) -> UUID:
    sm = get_sessionmaker()
    async with sm() as session:
        report = BillingReport(user_id=TEST_USER, source_filename=csv.name)
        await PgBillingReportRepository(session).save(report)
        await session.commit()

    async with sm() as session:
        uc = IngestBillingFileUseCase(
            PgBillingReportRepository(session),
            PgBillingItemRepository(session),
            BuildBillingEnrichedUseCase(
                PgBillingReportRepository(session),
                PgBillingItemRepository(session),
                PgBillingEnrichedRepository(session),
                PgCloudSkuRepository(session),
                PgSkuClassificationRuleRepository(session),
                PgUnmappedBillingRepository(session),
            ),
        )
        await uc.execute(
            IngestBillingFileInput(
                user_id=TEST_USER, report_id=report.id, file_path=csv
            )
        )
        await session.commit()
    return report.id


async def _context(report_id: UUID):
    sm = get_sessionmaker()
    async with sm() as session:
        uc = BuildMigrationContextUseCase(
            PgBillingReportRepository(session),
            PgBillingEnrichedRepository(session),
            PGServiceMetadataRepository(session),
            PGServiceComparisonRepository(session),
            sku_repo=PgCloudSkuRepository(session),
            fx_repo=PgExchangeRateRepository(session),
            price_repo=PgCloudPricePointRepository(session),
        )
        return await uc.execute(
            BuildMigrationContextInput(user_id=TEST_USER, report_id=report_id)
        )


async def _cleanup(report_id: UUID) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (
            "unmapped_billing_lines",
            "billing_enriched",
            "billing_items",
            "billing_reports",
        ):
            col = "id" if table == "billing_reports" else "report_id"
            await session.execute(
                text(f"delete from {table} where {col} = :rid"), {"rid": report_id}
            )
        await session.commit()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def aws_context(_db_ready):
    """Contexto completo do bill AWS real (Cost Explorer, mar/2026)."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv")
    try:
        yield await _context(rid)
    finally:
        await _cleanup(rid)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def gcp_context(_db_ready):
    """Contexto completo do bill GCP real (Cost Table, dez/2025, em BRL)."""
    rid = await _ingest(FIXTURES / "gcp_cost_table_full.csv")
    try:
        yield await _context(rid)
    finally:
        await _cleanup(rid)
