#!/usr/bin/env python3
"""Popula cloud_price_points com preços oficiais (Precision Engine v3 — Wave 3).

Roda offline (script/cron), nunca no caminho crítico da análise.

Uso:
    python scripts/sync_prices.py               # Azure (eastus)
    python scripts/sync_prices.py --region westus2
"""

from __future__ import annotations

import argparse
import asyncio

from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.pricing.azure_price_client import fetch_azure_vm_prices
from app.infrastructure.pricing.oci_price_client import fetch_oci_compute_prices
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.use_cases.catalog.sync_cloud_prices import SyncCloudPricesUseCase


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="eastus")
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument(
        "--providers", default="azure,oci", help="csv: azure,oci"
    )
    args = parser.parse_args()
    wanted = {p.strip() for p in args.providers.split(",") if p.strip()}

    fetchers = {
        "azure": lambda: fetch_azure_vm_prices(args.region, max_pages=args.max_pages),
        "oci": fetch_oci_compute_prices,
    }

    sm = get_sessionmaker()
    async with sm() as session:
        uc = SyncCloudPricesUseCase(PgCloudPricePointRepository(session))
        for provider in ("azure", "oci"):
            if provider not in wanted:
                continue
            res = await uc.execute(provider, fetchers[provider])
            print(f"{provider}: fetched={res.fetched} upserted={res.upserted}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
