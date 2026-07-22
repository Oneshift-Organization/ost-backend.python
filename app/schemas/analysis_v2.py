"""Analysis V2 schema — richer response + legacy direct-input request.

``AnalysisV2Response.resilient_parse(raw)`` tolerates field-name variants from
the LLM (aliases, camelCase, partial responses) without raising. See
``docs/refactor/analysis-v2-spec.md`` §3.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ── Output schema ─────────────────────────────────────────────────────────────


class OneshiftScores(BaseModel):
    model_config = ConfigDict(extra="ignore")
    financial_score: int = 0
    technical_score: int = 0
    enterprise_score: int = 0
    overall_score: int = 0


class ProviderComparison(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str = ""
    financial_score: int = 0
    technical_score: int = 0
    enterprise_score: int = 0
    overall_score: int = 0
    estimated_monthly_cost: float = 0.0
    estimated_monthly_savings: float = 0.0
    migration_complexity: str = "unknown"
    portability: str = "unknown"
    confidence_level: str = "low"
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class CrossCloudEquivalent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    workload: str = ""
    source_provider: str = ""
    source_service: str = ""
    target_provider: str = ""
    target_service: str = ""
    equivalence_type: str = "unknown"
    estimated_monthly_cost: float = 0.0
    cost_delta: float = 0.0
    portability: str = "unknown"
    migration_risk: str = "unknown"
    requires_refactoring: bool | None = None
    vendor_lock_in_level: str = "unknown"


class FinOpsView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    confidence_level: str = "low"
    key_findings: list[str] = Field(default_factory=list)
    financial_flags: list[str] = Field(default_factory=list)


class CloudEngineeringView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    confidence_level: str = "low"
    migration_complexity: str = "unknown"
    technical_flags: list[str] = Field(default_factory=list)
    technical_recommendations: list[str] = Field(default_factory=list)


class RiskMigrationView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    confidence_level: str = "low"
    risk_flags: list[str] = Field(default_factory=list)
    blocked_or_uncertain_items: list[str] = Field(default_factory=list)
    recommended_migration_sequence: list[str] = Field(default_factory=list)


class AgentViews(BaseModel):
    model_config = ConfigDict(extra="ignore")
    finops: FinOpsView = Field(default_factory=FinOpsView)
    cloud_engineering: CloudEngineeringView = Field(default_factory=CloudEngineeringView)
    risk_migration: RiskMigrationView = Field(default_factory=RiskMigrationView)


class SavingsSummaryV2(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_monthly_savings_usd: float = 0.0
    percentage_reduction: float = 0.0
    payback_period_months: int | None = None
    confidence_level: str = "low"


class AnalysisV2Response(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    viability_score: int = Field(default=0, ge=0, le=100)
    oneshift_scores: OneshiftScores = Field(default_factory=OneshiftScores)
    provider_comparison: list[ProviderComparison] = Field(default_factory=list)
    cross_cloud_equivalents: list[CrossCloudEquivalent] = Field(default_factory=list)
    recommended_provider: str = ""
    recommendation: str = ""
    recommendation_rationale: str = ""
    analysis_confidence: str = "low"
    prompt_contract_version: str = "oneshift-operator-v3.0"
    agent_views: AgentViews = Field(default_factory=AgentViews)
    savings_summary: SavingsSummaryV2 = Field(default_factory=SavingsSummaryV2)
    flags: dict[str, list[Any]] = Field(default_factory=dict)
    strategic_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)

    @classmethod
    def resilient_parse(cls, raw: dict[str, Any]) -> AnalysisV2Response:
        """Parse an LLM dict tolerating field-name variations (spec §3).

        Two passes:
        1. Normalize top-level and nested keys via alias table.
        2. Pydantic validation with ``extra=ignore`` + full defaults.
        Never raises — returns a degraded but valid object on any error.
        """
        try:
            normalized = _normalize_top(raw)
            _clamp_viability_score(normalized)
            return cls.model_validate(normalized)
        except Exception:
            logger.warning("analysis_v2_parse_fallback", exc_info=True)
            try:
                degraded = cls.model_validate({})
                degraded.flags["parsing"] = [{
                    "code": "PARSE_FALLBACK",
                    "message": "Resposta do LLM inválida — objeto degradado retornado",
                    "impact_area": "analysis",
                }]
                return degraded
            except Exception:
                degraded = cls()
                degraded.flags["parsing"] = [{
                    "code": "PARSE_FALLBACK",
                    "message": "Resposta do LLM inválida — objeto degradado retornado",
                    "impact_area": "analysis",
                }]
                return degraded


# ── Field-alias normalization ─────────────────────────────────────────────────

_TOP_ALIASES: dict[str, str] = {
    # provider_comparison
    "providers": "provider_comparison",
    "cloud_providers": "provider_comparison",
    "provider_analysis": "provider_comparison",
    "provider_scores": "provider_comparison",
    # cross_cloud_equivalents
    "equivalents": "cross_cloud_equivalents",
    "cross_cloud": "cross_cloud_equivalents",
    "cloud_equivalents": "cross_cloud_equivalents",
    "workload_equivalents": "cross_cloud_equivalents",
    # agent_views
    "agents": "agent_views",
    "agent_analysis": "agent_views",
    "views": "agent_views",
    "agent_perspectives": "agent_views",
    # viability_score
    "viability": "viability_score",
    "score": "viability_score",
    "overall_viability": "viability_score",
    # oneshift_scores
    "scores": "oneshift_scores",
    "oneshift": "oneshift_scores",
    # analysis_confidence
    "confidence": "analysis_confidence",
    "overall_confidence": "analysis_confidence",
    # savings_summary
    "savings": "savings_summary",
    "monthly_savings": "savings_summary",
    "cost_savings": "savings_summary",
    # misc
    "notes": "strategic_notes",
    "plan": "action_plan",
    "version": "prompt_contract_version",
}

_AGENT_VIEW_ALIASES: dict[str, str] = {
    # finops
    "finops_view": "finops",
    "economist": "finops",
    "financial": "finops",
    "finance": "finops",
    # cloud_engineering
    "engineering_view": "cloud_engineering",
    "architect": "cloud_engineering",
    "engineering": "cloud_engineering",
    "technical": "cloud_engineering",
    # risk_migration
    "risk_view": "risk_migration",
    "strategist": "risk_migration",
    "risk": "risk_migration",
    "migration": "risk_migration",
}


def _normalize_top(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in raw.items():
        canonical = _TOP_ALIASES.get(k, k)
        out[canonical] = v

    # normalize agent_views sub-keys
    if "agent_views" in out and isinstance(out["agent_views"], dict):
        av: dict[str, Any] = {}
        for k, v in out["agent_views"].items():
            av[_AGENT_VIEW_ALIASES.get(k, k)] = v
        out["agent_views"] = av

    return out


def _clamp_viability_score(normalized: dict[str, Any]) -> None:
    """Clamp ``viability_score`` into 0-100 before validation (spec §3).

    LLMs occasionally emit out-of-range scores (e.g. 720 on a 0-1000 scale).
    Without a pre-validation clamp, Pydantic's ``le=100`` rejects the whole
    dict and an otherwise-good response degrades to an empty PARSE_FALLBACK
    object. Non-numeric values are left untouched for Pydantic to reject via
    the normal fallback path. Sub-scores are not clamped here — divergences
    there are recorded by the financial overlay downstream.
    """
    if "viability_score" not in normalized:
        return
    value = normalized["viability_score"]
    try:
        value = int(value)
    except (TypeError, ValueError):
        return
    normalized["viability_score"] = max(0, min(100, value))


# ── Legacy input request ──────────────────────────────────────────────────────


class CompanyContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company_size: str = "unknown"
    industry: str = ""
    regulated_environment: bool = False
    critical_workloads: bool = False
    global_operations: bool = False
    high_availability_required: bool = False
    disaster_recovery_required: bool = False
    security_sensitive: bool = False
    cloud_maturity: str = "unknown"
    migration_goal: str = "unknown"


class LegacySourceUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sku_id: str = ""
    billed_cost: float = 0.0
    usage_quantity: float | None = None
    currency: str = "USD"


class LegacyFinancials(BaseModel):
    model_config = ConfigDict(extra="ignore")
    estimated_monthly_cost: float | None = None
    cost_delta: float | None = None
    pricing_confidence: str = "low"


class LegacyEquivalent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str = ""
    service_name: str = ""
    technical_match: dict[str, Any] | None = None
    financials: LegacyFinancials | None = None
    metadata: dict[str, Any] | None = None


class LegacyWorkload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    canonical_service: str = ""
    category: str = ""
    service_name: str = ""
    service_type: str = ""
    source_usage: LegacySourceUsage | None = None
    equivalents: list[LegacyEquivalent] = Field(default_factory=list)


class LegacyAnalysisRequest(BaseModel):
    """Pre-computed workload payload — no CSV upload required."""

    model_config = ConfigDict(extra="ignore")

    source_provider: str
    billing_period: str
    currency: str = "USD"
    total_spend: float
    company_context: CompanyContext | None = None
    workloads: list[LegacyWorkload] = Field(default_factory=list)
