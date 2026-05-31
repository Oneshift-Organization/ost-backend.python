#! /usr/bin/env python3
"""CLI entry point to sync GCP SKUs from Cloud Billing Catalog API.

Usage:
    python -m app.cli.sync_catalog

Requires:
    - GOOGLE_APPLICATION_CREDENTIALS env var or gcp_service_account_path in config
    - Database connection
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.core.config import get_settings
from app.infrastructure.cloud_clients.gcp_catalog_client import GcpCatalogClient
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.use_cases.catalog.sync_gcp_catalog import SyncGcpCatalogUseCase

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)-30s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("cli.sync_catalog")


async def main() -> None:
    settings = get_settings()

    logger.info("service_account_path: %s", settings.gcp_service_account_path or "(ADC fallback)")

    client = GcpCatalogClient()
    sm = get_sessionmaker()

    async with sm() as session:
        repo = PgCloudSkuRepository(session)
        use_case = SyncGcpCatalogUseCase(sku_repo=repo, catalog_client=client)
        result = await use_case.execute()

    print()
    print("═" * 50)
    print("  GCP Catalog Sync Result")
    print("═" * 50)
    print(f"  Raw SKUs from API:   {result.total_raw:>6}")
    print(f"  Filtered out:         {result.skipped_filter:>6}")
    print(f"  Inserted:             {result.inserted:>6}")
    print(f"  Updated:              {result.updated:>6}")
    print(f"  With vCPU/RAM specs:  {result.with_specs:>6}")
    print(f"  Without specs:        {result.without_specs:>6}")
    if result.errors:
        print(f"  Errors:               {len(result.errors):>6}")
        for err in result.errors:
            print(f"    - {err}")
    print("═" * 50)


if __name__ == "__main__":
    asyncio.run(main())
