from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class CloudSku:
    sku_id: str
    provider: str
    service_name: str
    currency: str
    service_type: str | None = None
    category: str | None = None
    region: str | None = None
    usage_unit: str | None = None
    price_per_unit: Decimal | None = None
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None
    attributes: dict[str, Any] | None = None
    effective_date: datetime | None = None
    collected_at: datetime | None = None
