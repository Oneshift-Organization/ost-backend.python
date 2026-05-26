"""Schema canônico de billing após normalização.

Toda saída de parser DEVE bater com este shape antes de virar `BillingItem` persistido.
"""

from datetime import datetime
from decimal import Decimal
from typing import TypedDict


class NormalizedBillingRecord(TypedDict, total=False):
    user_id: str
    job_id: str

    provider: str
    schema_version: str
    account_id: str
    account_name: str | None

    service: str
    service_raw: str
    resource_id: str | None
    resource_type: str | None
    region: str | None

    usage_start: datetime | None
    usage_end: datetime | None
    billing_period: str

    cost: Decimal
    cost_discounted: Decimal
    currency: str

    usage_amount: Decimal | None
    usage_unit: str | None

    tags: dict
    is_correction: bool
    is_estimate: bool

    raw_payload: dict
    source_file: str
    line_hash: str


NORMALIZED_COLUMNS: tuple[str, ...] = (
    "user_id",
    "job_id",
    "provider",
    "schema_version",
    "account_id",
    "account_name",
    "service",
    "service_raw",
    "resource_id",
    "resource_type",
    "region",
    "usage_start",
    "usage_end",
    "billing_period",
    "cost",
    "cost_discounted",
    "currency",
    "usage_amount",
    "usage_unit",
    "tags",
    "is_correction",
    "is_estimate",
    "raw_payload",
    "source_file",
    "line_hash",
)
