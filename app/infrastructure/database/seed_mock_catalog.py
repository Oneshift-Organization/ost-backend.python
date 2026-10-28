"""Seed mock cloud_sku_catalog and service_ontology for MVP Mock Engine.

Inserts 4 base SKUs (AWS/Azure/GCP/OCI) and ontology entries so the
EquivalenceMatcher can find hardware-matched equivalents.

Usage:
    python -m app.infrastructure.database.seed_mock_catalog
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.infrastructure.database.models.cloud_sku_catalog import CloudSkuCatalogModel
from app.infrastructure.database.models.service_ontology import ServiceOntologyModel
from app.infrastructure.database.session import get_sessionmaker

MOCK_CATALOG = [
    {
        "sku_id": "BoxUsage:t3a.medium",
        "provider": "AWS",
        "service_name": "Amazon EC2",
        "service_type": "virtual_machine",
        "category": "compute",
        "region": "us-east-1",
        "usage_unit": "Hrs",
        "price_per_unit": Decimal("0.0376"),
        "currency": "USD",
        "vcpus": Decimal("2"),
        "ram_gb": Decimal("4"),
    },
    {
        "sku_id": "Standard_D2as_v5",
        "provider": "Azure",
        "service_name": "Azure Virtual Machines",
        "service_type": "virtual_machine",
        "category": "compute",
        "region": "eastus",
        "usage_unit": "Hrs",
        "price_per_unit": Decimal("0.048"),
        "currency": "USD",
        "vcpus": Decimal("2"),
        "ram_gb": Decimal("8"),
    },
    {
        "sku_id": "e2-medium",
        "provider": "GCP",
        "service_name": "Compute Engine",
        "service_type": "virtual_machine",
        "category": "compute",
        "region": "us-central1",
        "usage_unit": "Hrs",
        "price_per_unit": Decimal("0.033"),
        "currency": "USD",
        "vcpus": Decimal("2"),
        "ram_gb": Decimal("4"),
    },
    {
        "sku_id": "VM.Standard.E4.Flex",
        "provider": "OCI",
        "service_name": "Oracle Cloud Infrastructure Compute",
        "service_type": "virtual_machine",
        "category": "compute",
        "region": "us-ashburn-1",
        "usage_unit": "Hrs",
        "price_per_unit": Decimal("0.025"),
        "currency": "USD",
        "vcpus": Decimal("2"),
        "ram_gb": Decimal("4"),
    },
]

MOCK_ONTOLOGY = [
    {"provider": "aws", "usage_type": "BoxUsage:t3a.medium", "canonical_type": "virtual_machine"},
    {"provider": "aws", "usage_type": "BoxUsage:t3a.medium", "canonical_type": "virtual_machine", "service_name": "AmazonEC2"},
    {"provider": "aws", "usage_type": "BoxUsage:c4.4xlarge", "canonical_type": "virtual_machine"},
    {"provider": "aws", "usage_type": "BoxUsage:c5a.large", "canonical_type": "virtual_machine"},
    {"provider": "aws", "usage_type": "BoxUsage:c5.large", "canonical_type": "virtual_machine"},
    {"provider": "aws", "usage_type": "BoxUsage:c7a.2xlarge", "canonical_type": "virtual_machine"},
    {"provider": "aws", "usage_type": "BoxUsage:m5a.2xlarge", "canonical_type": "virtual_machine"},
    {"provider": "azure", "usage_type": "Standard_D2as_v5", "canonical_type": "virtual_machine"},
    {"provider": "gcp", "usage_type": "e2-medium", "canonical_type": "virtual_machine"},
    {"provider": "oci", "usage_type": "VM.Standard.E4.Flex", "canonical_type": "virtual_machine"},
]


async def main() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        # ── cloud_sku_catalog ──
        await session.execute(delete(CloudSkuCatalogModel))
        await session.flush()
        now = datetime.now(UTC)
        for item in MOCK_CATALOG:
            session.add(
                CloudSkuCatalogModel(
                    sku_id=item["sku_id"],
                    provider=item["provider"],
                    service_name=item["service_name"],
                    service_type=item["service_type"],
                    category=item["category"],
                    region=item["region"],
                    usage_unit=item["usage_unit"],
                    price_per_unit=item["price_per_unit"],
                    currency=item["currency"],
                    vcpus=item["vcpus"],
                    ram_gb=item["ram_gb"],
                    effective_date=now,
                )
            )
        await session.flush()
        sku_count = len(MOCK_CATALOG)

        # ── service_ontology ── (preserve existing, add SKU-level entries)
        existing_types = set()
        result = await session.execute(
            select(ServiceOntologyModel.provider, ServiceOntologyModel.usage_type)
        )
        for row in result:
            existing_types.add((row.provider, row.usage_type))

        onto_count = 0
        for entry in MOCK_ONTOLOGY:
            key = (entry["provider"], entry["usage_type"])
            if key in existing_types:
                continue
            session.add(
                ServiceOntologyModel(
                    id=uuid4(),
                    provider=entry["provider"],
                    usage_type=entry["usage_type"],
                    service_name=entry.get("service_name", entry["canonical_type"]),
                    category="compute",
                    service_type=entry["canonical_type"],
                    confidence="high",
                    version=1,
                    active=True,
                )
            )
            onto_count += 1
            existing_types.add(key)

        await session.commit()

        print(f"Seeded {sku_count} SKUs in cloud_sku_catalog")
        print(f"Added {onto_count} new entries to service_ontology")

        # Verify
        total = await session.scalar(select(func.count()).select_from(CloudSkuCatalogModel))
        print(f"  cloud_sku_catalog total: {total}")

        vms = await session.scalar(
            select(func.count()).where(
                ServiceOntologyModel.service_type == "virtual_machine",
            )
        )
        print(f'  service_ontology "virtual_machine" entries: {vms}')


if __name__ == "__main__":
    asyncio.run(main())
