"""New Azure exports go through the same parser/rules as existing reports."""

import csv
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.infrastructure.database.seed_classification_rules import AZURE_RULES
from app.infrastructure.file_ingestion.normalizer import dataframe_to_items
from app.infrastructure.file_ingestion.parsers.azure_parser import AzureParser
from app.use_cases.analysis.build_billing_enriched import (
    BuildBillingEnrichedInput,
    BuildBillingEnrichedUseCase,
)


@pytest.mark.parametrize(
    "payg,region,multiplier",
    [(False, "East US", 1), (True, "West Europe", 3), (False, "Brazil South", 7)],
)
async def test_new_file_classification_uses_content_not_report_identity(
    tmp_path, payg, region, multiplier
):
    uid, rid = uuid4(), uuid4()
    cases = [
        ("Virtual Machines", "Dsv5 Series", "D8s v5", "core"),
        ("Storage", "Premium SSD Managed Disks", "P30 LRS Disk", "storage_ssd"),
        ("Azure Database for PostgreSQL", "Flexible Server", "Compute", "database"),
        ("Bandwidth", "Inter Region", "Data Transfer Out", "network_egress"),
        ("Uncatalogued Service", "Future Tier", "Meter", "unclassified"),
    ]
    rows = [
        {
            "SubscriptionId": f"synthetic-{rid}",
            "Date": "10/17/2026",
            "MeterCategory": category,
            "MeterSubCategory": sub,
            "MeterName": meter,
            "Quantity": 24 * multiplier,
            "CostInBillingCurrency": 10 * multiplier,
            "ResourceLocation": region,
            "BillingCurrency": "USD",
            "UnitOfMeasure": "1 Hour",
            **({"ServiceName": category} if payg else {}),
        }
        for category, sub, meter, _ in cases
    ]
    path = tmp_path / f"unseen-{rid}.csv"
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    parsed = AzureParser(user_id=uid, job_id=rid).parse(path)
    items = dataframe_to_items(parsed, rid)
    reports, item_repo, enriched, catalog, rules, unmapped = [AsyncMock() for _ in range(6)]
    reports.get_by_id.return_value = SimpleNamespace(status="processing")
    item_repo.list_by_report.return_value = items
    catalog.list_by_provider.return_value = []
    rules.list_active_by_provider.return_value = [
        SkuClassificationRule("AZURE", pattern, kind, component, family, confidence, priority)
        for pattern, kind, component, family, confidence, priority in AZURE_RULES
    ]
    uc = BuildBillingEnrichedUseCase(reports, item_repo, enriched, catalog, rules, unmapped)
    await uc.execute(BuildBillingEnrichedInput(uid, rid))
    saved = enriched.bulk_insert.call_args.args[0]
    assert {line.service_name: line.component_type for line in saved} == {
        c: expected for c, _, _, expected in cases
    }
    assert sum(line.cost for line in saved) == Decimal(50 * multiplier)
    rules.list_active_by_provider.assert_awaited_once_with("azure")
    assert len(unmapped.bulk_insert.call_args.args[0]) == 1  # preserved, never silently dropped
    # A missing deployment seed must be diagnosed before replacing derived data.
    rules.list_active_by_provider.return_value = []
    enriched.reset_mock()
    with pytest.raises(ValueError, match="Classificação indisponível"):
        await uc.execute(BuildBillingEnrichedInput(uid, rid))
    enriched.delete_by_report.assert_not_awaited()
