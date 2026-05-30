from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.entities.workload_pool import AuxiliaryCosts, WorkloadPool


@dataclass(frozen=True)
class ServiceMetadata:
    id: UUID
    provider: str
    category: str
    service_name: str
    metadata: dict[str, Any]
    docs_costs: str | None = None


@dataclass(frozen=True)
class ServiceComparison:
    id: UUID
    category: str
    service_type: str
    provider: str
    service_name: str


@dataclass(frozen=True)
class TechnicalMatch:
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None


@dataclass(frozen=True)
class FinancialEstimate:
    unit_price: Decimal | None = None
    estimated_monthly_cost: Decimal | None = None
    cost_delta: Decimal | None = None
    pricing_confidence: str = "low"


@dataclass(frozen=True)
class EquivalentService:
    provider: str
    service_name: str
    category: str
    service_type: str
    metadata: dict[str, Any] | None = None
    docs_costs: str | None = None
    technical_match: TechnicalMatch | None = None
    financials: FinancialEstimate | None = None


@dataclass(frozen=True)
class BillingItemSummary:
    service_name: str
    provider: str
    total_cost: Decimal
    service_type: str = "unknown"
    currency: str = "USD"
    line_count: int = 0
    equivalents: list[EquivalentService] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceCategoryGroup:
    category: str
    billing_total: Decimal
    services: list[BillingItemSummary] = field(default_factory=list)


@dataclass(frozen=True)
class SourceUsage:
    sku_id: str
    usage_quantity: Decimal
    billed_cost: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class WorkloadSummary:
    canonical_service: str
    source_usage: SourceUsage
    equivalents: list[EquivalentService] = field(default_factory=list)


@dataclass(frozen=True)
class MigrationContext:
    source_provider: str
    billing_period: str
    total_spend: Decimal
    categories: list[ServiceCategoryGroup]
    workloads: list[WorkloadSummary] = field(default_factory=list)
    pools: tuple[WorkloadPool, ...] = ()
    auxiliary: AuxiliaryCosts | None = None
    currency: str = "USD"
    missing_sku_ids: tuple[str, ...] = ()
    mapping_confidence: dict[str, Decimal] = field(default_factory=dict)
