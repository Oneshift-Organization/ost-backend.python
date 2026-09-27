"""Versioned, additive contract for backend-owned scores and migration strategies."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Provider = Literal["aws", "azure", "gcp", "oci"]
Strategy = Literal["retain", "retire", "rehost", "relocate", "repurchase", "replatform", "refactor"]


class GovernanceEvidence(BaseModel):
    """User declarations, not independently verified compliance certifications."""

    model_config = ConfigDict(extra="forbid")
    provider: Provider
    evidence: str = Field(min_length=1, max_length=1000)
    region_approved: StrictBool | None = None
    compliance_approved: StrictBool | None = None
    operations_ready: StrictBool | None = None


class WorkloadEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workload: str = Field(min_length=1, max_length=250)
    provider: Provider
    evidence: str = Field(min_length=1, max_length=1000)
    retain_required: StrictBool | None = None
    unused_confirmed: StrictBool | None = None
    retirement_approved: StrictBool | None = None
    dependencies_reviewed: StrictBool | None = None
    saas_replacement_approved: StrictBool | None = None
    functional_fit_confirmed: StrictBool | None = None
    data_migration_planned: StrictBool | None = None
    relocation_supported: StrictBool | None = None
    licenses_transferable: StrictBool | None = None
    unchanged_platform_confirmed: StrictBool | None = None


class DecisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    governance: list[GovernanceEvidence] = Field(default_factory=list, max_length=4)
    workloads: list[WorkloadEvidence] = Field(default_factory=list, max_length=300)

    @model_validator(mode="after")
    def unique_evidence(self) -> DecisionContext:
        providers = [e.provider for e in self.governance]
        keys = [(e.provider, " ".join(e.workload.casefold().split())) for e in self.workloads]
        if len(providers) != len(set(providers)) or len(keys) != len(set(keys)):
            raise ValueError("duplicate provider/workload evidence")
        return self


class AnalysisStartOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_context: DecisionContext | None = None
    # Inventário do mesmo provedor, congelado no payload (PLANO_INVENTARIO_API RI14).
    inventory_id: UUID | None = None


class ScoreFactor(BaseModel):
    id: str
    label: str
    weight: float = Field(ge=0, le=1)
    value: float | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(ge=0, le=1)
    lower_bound: float = Field(ge=0, le=100)
    upper_bound: float = Field(ge=0, le=100)
    contribution: float = Field(ge=0, le=100)
    rule: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ScoreAxis(BaseModel):
    id: str
    label: str
    weight: float = Field(ge=0, le=1)
    value: float | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(ge=0, le=1)
    lower_bound: float = Field(ge=0, le=100)
    upper_bound: float = Field(ge=0, le=100)
    contribution: float = Field(ge=0, le=100)
    factors: list[ScoreFactor]


class ScenarioScore(BaseModel):
    provider: str
    status: Literal["complete", "partial", "not_scored"]
    value: float | None = Field(default=None, ge=0, le=100)
    lower_bound: float = Field(ge=0, le=100)
    upper_bound: float = Field(ge=0, le=100)
    coverage: float = Field(ge=0, le=1)
    eligible: bool
    blockers: list[str] = Field(default_factory=list)
    axes: list[ScoreAxis]


class WorkloadStrategy(BaseModel):
    workload_id: str
    workload: str
    provider: str
    target_service: str | None = None
    strategy: Strategy | None = None
    status: Literal["candidate", "declared", "undetermined"]
    rule: str
    rationale: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    requires_refactoring: bool | None = None
    vendor_lock_in_level: str | None = None
    # Catalog suitability is not a migration completion/safety guarantee.
    scope: Literal["billing_component"] = "billing_component"


class DecisionSupport(BaseModel):
    contract_version: Literal["oneshift-decision-v1"] = "oneshift-decision-v1"
    policy_version: Literal["score-7rs-v1"] = "score-7rs-v1"
    scale: Literal["0-100"] = "0-100"
    source: Literal["backend_rules"] = "backend_rules"
    input_fingerprint: str
    selected_provider: str | None = None
    selection_reason: str
    score: ScenarioScore | None = None
    provider_scores: list[ScenarioScore] = Field(default_factory=list)
    workload_strategies: list[WorkloadStrategy] = Field(default_factory=list)
    strategy_summary: str
    confidence: str
    limitations: list[str] = Field(default_factory=list)
    legacy_score_semantics: str = "lower_bound_when_partial; zero_when_not_scored"
