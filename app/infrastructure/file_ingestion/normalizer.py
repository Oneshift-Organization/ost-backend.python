"""DataFrame → BillingItem entity list.

Receives a validated normalized DataFrame and produces a list of `BillingItem`
ready for `IBillingItemRepository.bulk_insert`.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pandas as pd

from app.domain.entities.billing_item import BillingItem
from app.domain.value_objects.cloud_provider import CloudProvider


def dataframe_to_items(df: pd.DataFrame, report_id: UUID) -> list[BillingItem]:
    """Convert canonical DataFrame to BillingItem entities.

    `df` must already be validated. `report_id` is injected here (not in parsers)
    because the report row is created by the use case BEFORE parsing begins.
    """
    items: list[BillingItem] = []
    for record in df.to_dict(orient="records"):
        items.append(
            BillingItem(
                user_id=UUID(cast(str, record["user_id"])),
                report_id=report_id,
                provider=CloudProvider(record["provider"]),
                service=cast(str, record["service"]),
                cost=record["cost"],
                currency=cast(str, record["currency"]),
                billing_period=cast(str, record["billing_period"]),
                usage_start=record.get("usage_start"),
                usage_end=record.get("usage_end"),
                account_id=record.get("account_id"),
                resource_id=record.get("resource_id"),
                region=record.get("region"),
                cost_discounted=record.get("cost_discounted"),
                usage_amount=record.get("usage_amount"),
                usage_unit=record.get("usage_unit"),
                tags=record.get("tags") or {},
                is_correction=bool(record.get("is_correction", False)),
                is_estimate=bool(record.get("is_estimate", False)),
                line_hash=cast(str, record.get("line_hash", "")),
            )
        )
    return items


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with duplicate `line_hash` — keeping first occurrence."""
    if "line_hash" not in df.columns:
        return df
    return df.drop_duplicates(subset=["line_hash"], keep="first").reset_index(drop=True)
