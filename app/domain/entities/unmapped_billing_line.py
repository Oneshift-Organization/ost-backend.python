from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4


@dataclass(slots=True)
class UnmappedBillingLine:
    user_id: UUID
    report_id: UUID
    provider: str
    billing_period: str
    usage_type: str
    reason: str
    raw_payload: dict[str, Any]
    source_line_hash: str
    service_name: str | None = None
    region: str | None = None
    detected_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
