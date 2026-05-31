#!/usr/bin/env python3
"""Re-run enrichment for a given report_id.

Usage:
    python scripts/re_enrich_report.py <report_id> [user_id]

Defaults to dev user 00000000-0000-0000-0000-000000000001.
"""

from __future__ import annotations

import sys
import asyncio
from uuid import UUID

from app.core.config import get_settings
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.pg_sku_classification_rule_repo import (
    PgSkuClassificationRuleRepository,
)
from app.infrastructure.repositories.pg_unmapped_billing_repo import (
    PgUnmappedBillingRepository,
)
from app.use_cases.analysis.build_billing_enriched import (
    BuildBillingEnrichedInput,
    BuildBillingEnrichedUseCase,
)

DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/re_enrich_report.py <report_id> [user_id]", file=sys.stderr)
        sys.exit(1)

    report_id = UUID(sys.argv[1])
    user_id = UUID(sys.argv[2]) if len(sys.argv) > 2 else DEV_USER_ID

    settings = get_settings()
    print(f"Report ID: {report_id}")
    print(f"User ID:   {user_id}")
    print(f"Database:  {settings.database_url}")
    print(f"Env:       {settings.app_env}")
    print()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        uc = BuildBillingEnrichedUseCase(
            report_repo=PgBillingReportRepository(session),
            item_repo=PgBillingItemRepository(session),
            enriched_repo=PgBillingEnrichedRepository(session),
            sku_repo=PgCloudSkuRepository(session),
            rule_repo=PgSkuClassificationRuleRepository(session),
            unmapped_repo=PgUnmappedBillingRepository(session),
        )
        inserted = await uc.execute(
            BuildBillingEnrichedInput(user_id=user_id, report_id=report_id)
        )
        await session.commit()

    print(f"Enriched rows inserted/replaced: {inserted}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
