#!/usr/bin/env python3
"""Trigger analysis pipeline directly for a report_id.

Usage:
    python scripts/run_analysis.py <report_id> [user_id]

Defaults to dev user 00000000-0000-0000-0000-000000000001.
"""

from __future__ import annotations

import sys
import asyncio
import json
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.domain.entities.analysis_job import AnalysisJob
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.llm.mock_client import MockLLMClient
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.use_cases.analysis.run_analysis_pipeline import RunAnalysisPipelineUseCase

DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_analysis.py <report_id> [user_id]", file=sys.stderr)
        sys.exit(1)

    report_id = UUID(sys.argv[1])
    user_id = UUID(sys.argv[2]) if len(sys.argv) > 2 else DEV_USER_ID

    settings = get_settings()
    print(f"Report ID: {report_id}")
    print(f"User ID:   {user_id}")
    print(f"LLM:       {settings.llm_provider}")
    print()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        job = AnalysisJob(
            job_id=uuid4(),
            user_id=user_id,
            report_id=report_id,
        )
        job_repo = PgAnalysisJobRepository(session)
        await job_repo.create(job)
        await session.commit()
        print(f"Created job: {job.job_id}")

        print("Running pipeline...")
        llm_client = MockLLMClient()
        orchestrator = LLMOrchestrator(llm_client=llm_client)

        uc = RunAnalysisPipelineUseCase(
            analysis_job_repo=job_repo,
            report_repo=PgBillingReportRepository(session),
            item_repo=PgBillingEnrichedRepository(session),
            metadata_repo=PGServiceMetadataRepository(session),
            comparison_repo=PGServiceComparisonRepository(session),
            sku_repo=PgCloudSkuRepository(session),
            llm_orchestrator=orchestrator,
        )

        await uc.execute(job)
        await session.commit()

        # Refresh and show result
        final = await job_repo.get_by_id(job.job_id, user_id)
        if final is None:
            print("ERROR: job not found after execution")
            sys.exit(1)

        print(f"\nStatus: {final.status}")
        if final.error:
            print(f"Error: {final.error}")

        if final.ai_structured_output:
            print("\n=== AI Structured Output ===")
            print(json.dumps(final.ai_structured_output, indent=2, default=str))

        if final.llm_output:
            print(f"\nLLM Output: {final.llm_output[:500]}...")

        # Also query pools to verify project_id
        from sqlalchemy import text

        rows = await session.execute(
            text("""
                SELECT project_id, COUNT(*) as cnt
                FROM billing_enriched
                WHERE report_id = :rid
                GROUP BY project_id
                ORDER BY cnt DESC
                LIMIT 5
            """),
            {"rid": report_id},
        )
        print("\n=== project_id distribution ===")
        for row in rows:
            print(f"  {row.project_id}: {row.cnt} rows")


if __name__ == "__main__":
    asyncio.run(main())
