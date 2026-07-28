from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.entities.workload_pool import AuxiliaryCosts, FamilyPool, WorkloadPool


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
    # match_basis: "pool_capacity" (L2 hardware) or "curated_only" (L3 only)
    match_basis: str = "pool_capacity"
    instances: int = 1
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
    # keyed by "{project_id}/{family}/{region}" — equivalents per compute pool
    pool_equivalents: dict[str, list[EquivalentService]] = field(default_factory=dict)
    # equivalents for the database auxiliary bucket
    database_equivalents: list[EquivalentService] = field(default_factory=list)

    # ── Precision Engine v3 ────────────────────────────────────────────────
    # Pools agregados por (família, região) — unidade de comparação cross-cloud.
    family_pools: tuple[FamilyPool, ...] = ()
    # keyed by "{family}/{region}" — equivalentes dimensionados sobre o agregado
    family_equivalents: dict[str, list[EquivalentService]] = field(default_factory=dict)
    # Equivalentes por categoria auxiliar não-compute (storage/egress), calculados
    # por razão de taxa. keyed pela categoria → lista de {provider, custo, delta}.
    category_equivalents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Moeda em que os custos comparados estão expressos (pode diferir de `currency`,
    # que é a moeda de origem do billing).
    analysis_currency: str = "USD"
    # Ledger de premissas: fx aplicado, fonte de preços, janela, cobertura.
    premissas: dict[str, Any] = field(default_factory=dict)
    # Roll-up por provedor + recomendação (backend calcula, LLM narra).
    resumo_executivo: dict[str, Any] = field(default_factory=dict)
