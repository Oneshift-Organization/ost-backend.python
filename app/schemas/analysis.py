from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EquivalentServiceItem(BaseModel):
    provider: str
    service_name: str
    category: str
    service_type: str
    metadata: dict[str, Any] | None = None
    docs_costs: str | None = None


class ServiceSummaryItem(BaseModel):
    service_name: str
    service_type: str = "unknown"
    total_cost: Decimal
    line_count: int = 0
    equivalents: list[EquivalentServiceItem] = Field(default_factory=list)


class ServiceCategoryGroupItem(BaseModel):
    category: str
    billing_total: Decimal
    services: list[ServiceSummaryItem] = Field(default_factory=list)


class MigrationContextResponse(BaseModel):
    source_provider: str
    billing_period: str
    total_spend: Decimal
    currency: str = "USD"
    categories: list[ServiceCategoryGroupItem] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    report_id: UUID
