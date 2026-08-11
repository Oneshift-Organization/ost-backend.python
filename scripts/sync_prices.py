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

from app.infrastructure.database.seed_category_rates import category_rate_points
from app.core.config import get_settings
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.pricing.aws_price_client import fetch_aws_ec2_prices
from app.infrastructure.pricing.azure_price_client import fetch_azure_vm_prices
from app.infrastructure.pricing.gcp_price_client import fetch_gcp_compute_prices
from app.infrastructure.pricing.category_rate_client import (
    fetch_azure_category_rates,
    fetch_oci_category_rates,
)
from app.infrastructure.pricing.oci_price_client import fetch_oci_compute_prices
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.use_cases.catalog.sync_cloud_prices import SyncCloudPricesUseCase


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="eastus")
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--gcp-region", default="us-central1")
    parser.add_argument(
        "--providers", default="azure,oci", help="csv: azure,oci"
    )
    args = parser.parse_args()
    wanted = {p.strip() for p in args.providers.split(",") if p.strip()}

    def _category_live() -> list:
        return [*fetch_azure_category_rates(args.region), *fetch_oci_category_rates()]

    fetchers = {
        "azure": lambda: fetch_azure_vm_prices(args.region, max_pages=args.max_pages),
        "oci": fetch_oci_compute_prices,
        # AWS e GCP usam a região própria de cada um, não `--region` (que é
        # nomenclatura Azure). Passar "eastus" para a AWS levantaria ValueError.
        "aws": lambda: fetch_aws_ec2_prices(region=args.aws_region),
        "gcp": lambda: fetch_gcp_compute_prices(
            api_key=get_settings().gcp_billing_api_key, region=args.gcp_region
        ),
        "categories": category_rate_points,       # taxas de referência (seed)
        "categories-live": _category_live,         # taxas oficiais (fetch)
    }

    sm = get_sessionmaker()
    async with sm() as session:
        uc = SyncCloudPricesUseCase(PgCloudPricePointRepository(session))
        for provider in ("azure", "oci", "aws", "gcp", "categories", "categories-live"):
            if provider not in wanted:
                continue
            res = await uc.execute(provider, fetchers[provider])
            print(f"{provider}: fetched={res.fetched} upserted={res.upserted}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
