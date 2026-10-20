"""DataFrame → BillingItem entity list.

Receives a validated normalized DataFrame and produces a list of `BillingItem`
ready for `IBillingItemRepository.bulk_insert`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pandas as pd

from app.domain.entities.billing_item import BillingItem
from app.domain.value_objects.cloud_provider import CloudProvider


def _nan_to_none(value: object) -> object:
    """Convert pandas NaN/NaT to None, leaving other values unchanged."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


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
                usage_start=_nan_to_none(record.get("usage_start")),
                usage_end=_nan_to_none(record.get("usage_end")),
                account_id=_nan_to_none(record.get("account_id")),
                resource_id=_nan_to_none(record.get("resource_id")),
                region=_nan_to_none(record.get("region")),
                cost_discounted=_nan_to_none(record.get("cost_discounted")),
                usage_amount=_nan_to_none(record.get("usage_amount")),
                usage_unit=_nan_to_none(record.get("usage_unit")),
                tags=_build_tags(record),
                service_raw=_nan_to_none(record.get("service_raw")),
                is_correction=bool(record.get("is_correction", False)),
                is_estimate=bool(record.get("is_estimate", False)),
                line_hash=cast(str, record.get("line_hash", "")),
                usage_type=_str_or_none(record.get("usage_type")),
                resource_type=_str_or_none(record.get("resource_type")),
                charge_kind=_str_or_none(record.get("charge_kind")),
                cost_billed=_decimal_or_none(record.get("cost_billed")),
                attributes=_as_dict(record.get("attributes")),
            )
        )
    return items


def _str_or_none(value: object) -> str | None:
    limpo = _nan_to_none(value)
    return None if limpo is None or limpo == "" else str(limpo)


def _decimal_or_none(value: object) -> Decimal | None:
    limpo = _nan_to_none(value)
    return limpo if isinstance(limpo, Decimal) else None


def _as_dict(value: object) -> dict[str, Any]:
    """Atributos só como dict. NaN (coluna ausente em parte das linhas) vira {}."""
    return value if isinstance(value, dict) else {}


def _build_tags(record: dict) -> dict:
    tags = record.get("tags") or {}
    raw_payload = record.get("raw_payload") or {}
    if not isinstance(tags, dict):
        tags = {"_tags_raw": tags}
    if isinstance(raw_payload, dict):
        tags = {**tags, "raw_payload": raw_payload}
    else:
        tags = {**tags, "raw_payload": {}}
    return tags


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with duplicate `line_hash` — keeping first occurrence."""
    if "line_hash" not in df.columns:
        return df
    return df.drop_duplicates(subset=["line_hash"], keep="first").reset_index(drop=True)
