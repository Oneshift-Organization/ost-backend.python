from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis_v2 import AnalysisV2Response


class TechnicalMatchItem(BaseModel):
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None


class FinancialEstimateItem(BaseModel):
    unit_price: Decimal | None = None
    estimated_monthly_cost: Decimal | None = None
    cost_delta: Decimal | None = None
    pricing_confidence: str = "low"


class EquivalentServiceItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    service_name: str
    category: str
    service_type: str
    metadata: dict[str, Any] | None = None
    docs_costs: str | None = None
    technical_match: TechnicalMatchItem | None = None
    financials: FinancialEstimateItem | None = None


class ServiceSummaryItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    service_name: str
    service_type: str = "unknown"
    total_cost: Decimal
    line_count: int = 0
    equivalents: list[EquivalentServiceItem] = Field(default_factory=list)


class ServiceCategoryGroupItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    category: str
    billing_total: Decimal
    services: list[ServiceSummaryItem] = Field(default_factory=list)


class SourceUsageItem(BaseModel):
    sku_id: str
    usage_quantity: Decimal
    billed_cost: Decimal
    currency: str = "USD"


class WorkloadSummaryItem(BaseModel):
    canonical_service: str
    source_usage: SourceUsageItem
    equivalents: list[EquivalentServiceItem] = Field(default_factory=list)


class MigrationContextResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_provider: str
    billing_period: str
    total_spend: Decimal
    currency: str = "USD"
    categories: list[ServiceCategoryGroupItem] = Field(default_factory=list)
    workloads: list[WorkloadSummaryItem] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    report_id: UUID


class AnalysisJobResponse(BaseModel):
    job_id: UUID
    report_id: UUID
    external_id: UUID | None = None
    status: str
    llm_output: str | None = None
    error: str | None = None
    result: AnalysisV2Response | None = None
    llm_input_payload: dict | None = None
    created_at: datetime


class AnalysisJobSummary(BaseModel):
    job_id: UUID
    report_id: UUID
    external_id: UUID | None = None
    status: str
    provider: str | None = None
    source_filename: str | None = None
    row_count: int = 0
    recommended_provider: str | None = None
    recommendation: str | None = None
    viability_score: int | None = None
    analysis_confidence: str | None = None
    error: str | None = None
    created_at: datetime
