from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.core.exceptions import ValidationError
from app.domain.value_objects.cloud_provider import CloudProvider

_CURRENCY_LEN = 3
_PERIOD_LEN = 7  # "YYYY-MM"


@dataclass(slots=True)
class BillingItem:
    """One normalized line item of a billing report."""

    user_id: UUID
    report_id: UUID
    provider: CloudProvider
    service: str
    cost: Decimal
    currency: str
    billing_period: str
    usage_start: datetime | None = None
    usage_end: datetime | None = None
    account_id: str | None = None
    resource_id: str | None = None
    region: str | None = None
    cost_discounted: Decimal | None = None
    usage_amount: Decimal | None = None
    usage_unit: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    service_raw: str | None = None
    is_correction: bool = False
    is_estimate: bool = False
    line_hash: str = ""
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not isinstance(self.cost, Decimal):
            raise ValidationError("cost must be Decimal")
        if not self.currency or len(self.currency) != _CURRENCY_LEN:
            raise ValidationError("currency must be ISO-4217 (3 chars)")
        if not self.billing_period or len(self.billing_period) != _PERIOD_LEN:
            raise ValidationError("billing_period must be YYYY-MM")
