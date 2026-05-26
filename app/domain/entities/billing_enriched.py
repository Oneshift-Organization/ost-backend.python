from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.core.exceptions import ValidationError

_CURRENCY_LEN = 3
_PERIOD_LEN = 7


@dataclass(slots=True)
class BillingEnriched:
    user_id: UUID
    report_id: UUID
    provider: str
    billing_period: str
    service_name: str
    cost: Decimal
    currency: str
    source_line_hash: str
    pricing_source: str
    pricing_confidence: str
    scope: str = "line_item"
    service_type: str | None = None
    category: str | None = None
    usage_type: str | None = None
    region: str | None = None
    usage_amount: Decimal | None = None
    usage_unit: str | None = None
    project_id: str | None = None
    sku_id: str | None = None
    sku_metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not isinstance(self.cost, Decimal):
            raise ValidationError("cost must be Decimal")
        if not self.currency or len(self.currency) != _CURRENCY_LEN:
            raise ValidationError("currency must be ISO-4217 (3 chars)")
        if not self.billing_period or len(self.billing_period) != _PERIOD_LEN:
            raise ValidationError("billing_period must be YYYY-MM")
