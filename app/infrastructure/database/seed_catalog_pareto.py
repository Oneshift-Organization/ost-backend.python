from __future__ import annotations

import json
from asyncio import run
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from app.infrastructure.database.session import get_sessionmaker

CATALOG_SEED: list[dict] = [
    # ── AWS EC2 Compute (Graviton3: m7g, r7g, c7g) ──
    {"sku_id": "BoxUsage:m7g.medium", "vcpus": 1, "ram_gb": 4, "price": "0.034"},
    {"sku_id": "BoxUsage:m7g.large", "vcpus": 2, "ram_gb": 8, "price": "0.068"},
    {"sku_id": "BoxUsage:m7g.xlarge", "vcpus": 4, "ram_gb": 16, "price": "0.136"},
    {"sku_id": "BoxUsage:m7g.2xlarge", "vcpus": 8, "ram_gb": 32, "price": "0.272"},
    {"sku_id": "BoxUsage:m7g.4xlarge", "vcpus": 16, "ram_gb": 64, "price": "0.544"},
    {"sku_id": "BoxUsage:r7g.large", "vcpus": 2, "ram_gb": 16, "price": "0.085"},
    {"sku_id": "BoxUsage:r7g.xlarge", "vcpus": 4, "ram_gb": 32, "price": "0.170"},
    {"sku_id": "BoxUsage:r7g.2xlarge", "vcpus": 8, "ram_gb": 64, "price": "0.340"},
    {"sku_id": "BoxUsage:r7g.4xlarge", "vcpus": 16, "ram_gb": 128, "price": "0.680"},
    {"sku_id": "BoxUsage:c7g.medium", "vcpus": 1, "ram_gb": 2, "price": "0.027"},
    {"sku_id": "BoxUsage:c7g.large", "vcpus": 2, "ram_gb": 4, "price": "0.054"},
    {"sku_id": "BoxUsage:c7g.xlarge", "vcpus": 4, "ram_gb": 8, "price": "0.108"},
    {"sku_id": "BoxUsage:c7g.2xlarge", "vcpus": 8, "ram_gb": 16, "price": "0.216"},
    {"sku_id": "BoxUsage:c7g.4xlarge", "vcpus": 16, "ram_gb": 32, "price": "0.432"},
    {"sku_id": "SpotUsage:c7g.2xlarge", "vcpus": 8, "ram_gb": 16, "price": "0.064"},
    {"sku_id": "SpotUsage:m7g.2xlarge", "vcpus": 8, "ram_gb": 32, "price": "0.081"},
    # AWS EC2 Compute (Graviton4: m8g, c8g, r8g)
    {"sku_id": "BoxUsage:m8g.xlarge", "vcpus": 4, "ram_gb": 16, "price": "0.136"},
    {"sku_id": "BoxUsage:m8g.2xlarge", "vcpus": 8, "ram_gb": 32, "price": "0.272"},
    {"sku_id": "BoxUsage:c8g.xlarge", "vcpus": 4, "ram_gb": 8, "price": "0.108"},
    {"sku_id": "BoxUsage:c8g.2xlarge", "vcpus": 8, "ram_gb": 16, "price": "0.216"},
    {"sku_id": "BoxUsage:r8g.large", "vcpus": 2, "ram_gb": 16, "price": "0.085"},
    {"sku_id": "BoxUsage:r8g.xlarge", "vcpus": 4, "ram_gb": 32, "price": "0.170"},
    # AWS EC2 Compute (Graviton2: t4g, is4gen)
    {"sku_id": "BoxUsage:t4g.nano", "vcpus": 2, "ram_gb": 0.5, "price": "0.0042"},
    {"sku_id": "BoxUsage:t4g.micro", "vcpus": 2, "ram_gb": 1, "price": "0.0084"},
    {"sku_id": "BoxUsage:t4g.small", "vcpus": 2, "ram_gb": 2, "price": "0.0168"},
    {"sku_id": "BoxUsage:t4g.medium", "vcpus": 2, "ram_gb": 4, "price": "0.0336"},
    {"sku_id": "BoxUsage:t4g.large", "vcpus": 2, "ram_gb": 8, "price": "0.0672"},
    {"sku_id": "BoxUsage:t4g.xlarge", "vcpus": 4, "ram_gb": 16, "price": "0.1344"},
    {"sku_id": "BoxUsage:is4gen.medium", "vcpus": 2, "ram_gb": 16, "price": "0.054"},
    {"sku_id": "BoxUsage:is4gen.large", "vcpus": 4, "ram_gb": 32, "price": "0.108"},
    {"sku_id": "BoxUsage:is4gen.xlarge", "vcpus": 4, "ram_gb": 32, "price": "0.216"},
    # AWS EC2 Compute (AMD: m7a, m8a, t3a, m5a)
    {"sku_id": "BoxUsage:m7a.xlarge", "vcpus": 4, "ram_gb": 32, "price": "0.152"},
    {"sku_id": "BoxUsage:m7a.2xlarge", "vcpus": 8, "ram_gb": 64, "price": "0.304"},
    {"sku_id": "BoxUsage:m8a.2xlarge", "vcpus": 8, "ram_gb": 64, "price": "0.304"},
    {"sku_id": "BoxUsage:t3a.nano", "vcpus": 2, "ram_gb": 0.5, "price": "0.0042"},
    {"sku_id": "BoxUsage:t3a.micro", "vcpus": 2, "ram_gb": 1, "price": "0.0084"},
    {"sku_id": "BoxUsage:t3a.small", "vcpus": 2, "ram_gb": 2, "price": "0.0168"},
    {"sku_id": "BoxUsage:t3a.large", "vcpus": 2, "ram_gb": 8, "price": "0.0672"},
    {"sku_id": "BoxUsage:t3a.xlarge", "vcpus": 4, "ram_gb": 16, "price": "0.1344"},
    {"sku_id": "BoxUsage:t3a.2xlarge", "vcpus": 8, "ram_gb": 32, "price": "0.2688"},
    {"sku_id": "BoxUsage:m5a.large", "vcpus": 2, "ram_gb": 8, "price": "0.076"},
    {"sku_id": "BoxUsage:m5a.xlarge", "vcpus": 4, "ram_gb": 16, "price": "0.152"},
    {"sku_id": "BoxUsage:m5a.2xlarge", "vcpus": 8, "ram_gb": 32, "price": "0.304"},
    # AWS EC2 Compute (Intel: c4, t2)
    {"sku_id": "BoxUsage:c4.large", "vcpus": 2, "ram_gb": 3.75, "price": "0.051"},
    {"sku_id": "BoxUsage:c4.xlarge", "vcpus": 4, "ram_gb": 7.5, "price": "0.102"},
    {"sku_id": "BoxUsage:c4.2xlarge", "vcpus": 8, "ram_gb": 15, "price": "0.204"},
    {"sku_id": "BoxUsage:c4.4xlarge", "vcpus": 16, "ram_gb": 30, "price": "0.408"},
    {"sku_id": "BoxUsage:t2.nano", "vcpus": 1, "ram_gb": 0.5, "price": "0.0058"},
    {"sku_id": "BoxUsage:t2.micro", "vcpus": 1, "ram_gb": 1, "price": "0.0116"},
    {"sku_id": "BoxUsage:t2.small", "vcpus": 1, "ram_gb": 2, "price": "0.023"},
    {"sku_id": "BoxUsage:t2.medium", "vcpus": 2, "ram_gb": 4, "price": "0.0464"},
    {"sku_id": "BoxUsage:t2.large", "vcpus": 2, "ram_gb": 8, "price": "0.0928"},
    {"sku_id": "BoxUsage:t2.xlarge", "vcpus": 4, "ram_gb": 16, "price": "0.1856"},
    # ── AWS RDS ──
    {"sku_id": "HeavyUsage:db.t4g.medium", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 2, "ram_gb": 4, "price": "0.067"},
    {"sku_id": "HeavyUsage:db.t4g.large", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 2, "ram_gb": 8, "price": "0.134"},
    {"sku_id": "HeavyUsage:db.t4g.xlarge", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 4, "ram_gb": 16, "price": "0.268"},
    {"sku_id": "HeavyUsage:db.t4g.2xlarge", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 8, "ram_gb": 32, "price": "0.536"},
    {"sku_id": "HeavyUsage:db.r8g.large", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 2, "ram_gb": 16, "price": "0.136"},
    {"sku_id": "HeavyUsage:db.r8g.xlarge", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 4, "ram_gb": 32, "price": "0.272"},
    {"sku_id": "HeavyUsage:db.r8g.4xl", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 16, "ram_gb": 128, "price": "1.088"},
    {"sku_id": "HeavyUsage:db.r8g.8xl", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 32, "ram_gb": 256, "price": "2.176"},
    {"sku_id": "HeavyUsage:db.r8g.12xl", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 48, "ram_gb": 384, "price": "3.264"},
    {"sku_id": "InstanceUsageIOOptimized:db.r8g.16xl", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 64, "ram_gb": 512, "price": "4.352"},
    {"sku_id": "Multi-AZUsage:db.t4g.xlarge", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 4, "ram_gb": 16, "price": "0.536"},
    {"sku_id": "Multi-AZUsage:db.t4g.2xlarge", "service_name": "AmazonRDS", "service_type": "relational_database", "category": "database", "vcpus": 8, "ram_gb": 32, "price": "1.072"},
    # ── GCP Compute ──
    {"sku_id": "e2-micro", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 2, "ram_gb": 1, "price": "0.0076"},
    {"sku_id": "e2-small", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 2, "ram_gb": 2, "price": "0.0151"},
    {"sku_id": "e2-standard-2", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 2, "ram_gb": 8, "price": "0.067"},
    {"sku_id": "e2-standard-4", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 4, "ram_gb": 16, "price": "0.134"},
    {"sku_id": "e2-standard-8", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 8, "ram_gb": 32, "price": "0.268"},
    {"sku_id": "e2-standard-16", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 16, "ram_gb": 64, "price": "0.536"},
    {"sku_id": "e2-highmem-2", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 2, "ram_gb": 16, "price": "0.132"},
    {"sku_id": "e2-highmem-4", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 4, "ram_gb": 32, "price": "0.264"},
    {"sku_id": "e2-highmem-8", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 8, "ram_gb": 64, "price": "0.528"},
    {"sku_id": "n2-standard-8", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 8, "ram_gb": 32, "price": "0.340"},
    {"sku_id": "n2-standard-16", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 16, "ram_gb": 64, "price": "0.680"},
    {"sku_id": "n2-highmem-8", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 8, "ram_gb": 64, "price": "0.520"},
    {"sku_id": "n2-highmem-16", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 16, "ram_gb": 128, "price": "1.040"},
    {"sku_id": "c2-standard-4", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 4, "ram_gb": 16, "price": "0.190"},
    {"sku_id": "c2-standard-8", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 8, "ram_gb": 32, "price": "0.380"},
    {"sku_id": "c2-standard-16", "provider": "GCP", "service_name": "Compute Engine", "vcpus": 16, "ram_gb": 64, "price": "0.760"},
    # ── OCI Compute ──
    {"sku_id": "VM.Standard3.Flex", "provider": "OCI", "service_name": "Compute", "vcpus": 4, "ram_gb": 32, "price": "0.112"},
    {"sku_id": "VM.Optimized3.Flex", "provider": "OCI", "service_name": "Compute", "vcpus": 8, "ram_gb": 64, "price": "0.384"},
    {"sku_id": "VM.Standard.A1.Flex", "provider": "OCI", "service_name": "Compute", "vcpus": 4, "ram_gb": 24, "price": "0.082"},
    {"sku_id": "VM.Standard2.1", "provider": "OCI", "service_name": "Compute", "vcpus": 1, "ram_gb": 15, "price": "0.028"},
    {"sku_id": "VM.Standard2.2", "provider": "OCI", "service_name": "Compute", "vcpus": 2, "ram_gb": 30, "price": "0.056"},
    {"sku_id": "VM.Standard2.4", "provider": "OCI", "service_name": "Compute", "vcpus": 4, "ram_gb": 60, "price": "0.112"},
    {"sku_id": "VM.Standard2.8", "provider": "OCI", "service_name": "Compute", "vcpus": 8, "ram_gb": 120, "price": "0.224"},
    # ── GCP Cloud SQL (RDS equivalents) ──
    {"sku_id": "db-custom-2-3840", "provider": "GCP", "service_name": "Cloud SQL", "service_type": "relational_database", "category": "database", "vcpus": 2, "ram_gb": 3.75, "price": "0.077"},
    {"sku_id": "db-custom-4-15360", "provider": "GCP", "service_name": "Cloud SQL", "service_type": "relational_database", "category": "database", "vcpus": 4, "ram_gb": 15, "price": "0.166"},
    {"sku_id": "db-custom-8-30720", "provider": "GCP", "service_name": "Cloud SQL", "service_type": "relational_database", "category": "database", "vcpus": 8, "ram_gb": 30, "price": "0.332"},
    # ── OCI MySQL HeatWave (RDS equivalents) ──
    {"sku_id": "MySQL.HeatWave.VM.Standard.E3.1", "provider": "OCI", "service_name": "MySQL HeatWave", "service_type": "relational_database", "category": "database", "vcpus": 1, "ram_gb": 8, "price": "0.038"},
    {"sku_id": "MySQL.HeatWave.VM.Standard.E3.2", "provider": "OCI", "service_name": "MySQL HeatWave", "service_type": "relational_database", "category": "database", "vcpus": 2, "ram_gb": 16, "price": "0.076"},
    {"sku_id": "MySQL.HeatWave.VM.Standard.E3.4", "provider": "OCI", "service_name": "MySQL HeatWave", "service_type": "relational_database", "category": "database", "vcpus": 4, "ram_gb": 32, "price": "0.152"},
    {"sku_id": "MySQL.HeatWave.VM.Standard.E3.8", "provider": "OCI", "service_name": "MySQL HeatWave", "service_type": "relational_database", "category": "database", "vcpus": 8, "ram_gb": 64, "price": "0.304"},
]

BASE_DEFAULTS: dict = {
    "provider": "AWS",
    "service_name": "AmazonEC2",
    "service_type": "virtual_server",
    "category": "compute",
    "region": "global",
    "usage_unit": "Hrs",
    "currency": "USD",
    "attributes": {},
}


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        now = datetime.now(timezone.utc)
        total = 0
        skipped = 0
        for entry in CATALOG_SEED:
            price = Decimal(entry.pop("price"))
            merged = {**BASE_DEFAULTS, **entry}
            merged["price_per_unit"] = price
            merged["effective_date"] = now

            result = await session.execute(
                text("""
                    INSERT INTO cloud_sku_catalog
                        (sku_id, provider, service_name, service_type, category, region, usage_unit,
                         price_per_unit, currency, vcpus, ram_gb, attributes, effective_date)
                    VALUES
                        (:sku_id, :provider, :service_name, :service_type, :category, :region, :usage_unit,
                         :price_per_unit, :currency, :vcpus, :ram_gb, CAST(:attributes AS jsonb), :effective_date)
                    ON CONFLICT (sku_id) DO NOTHING
                """),
                {
                    "sku_id": merged["sku_id"],
                    "provider": merged["provider"],
                    "service_name": merged["service_name"],
                    "service_type": merged["service_type"],
                    "category": merged["category"],
                    "region": merged["region"],
                    "usage_unit": merged["usage_unit"],
                    "price_per_unit": price,
                    "currency": merged["currency"],
                    "vcpus": Decimal(str(merged["vcpus"])),
                    "ram_gb": Decimal(str(merged["ram_gb"])),
                    "attributes": json.dumps(merged["attributes"]),
                    "effective_date": now,
                },
            )
            if result.rowcount > 0:
                total += 1
            else:
                skipped += 1

        await session.commit()
        print(f"Seeded {total} new SKUs (skipped {skipped} existing)")


if __name__ == "__main__":
    run(main())
