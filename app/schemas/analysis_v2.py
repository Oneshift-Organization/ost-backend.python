"""Analysis V2 schema — richer response + legacy direct-input request.

``AnalysisV2Response.resilient_parse(raw)`` tolerates field-name variants from
the LLM (aliases, camelCase, partial responses) without raising. See
``docs/api-conventions.md``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.decision_support import DecisionContext, DecisionSupport

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
    target_service: str | None = None
    equivalence_type: str = "unknown"
    estimated_monthly_cost: float | None = None
    cost_delta: float | None = None
    portability: str = "unknown"
    migration_risk: str = "unknown"
    requires_refactoring: bool | None = None
    vendor_lock_in_level: str | None = None
    workload_id: str | None = None
    mapping_basis: str | None = None
    pricing_confidence: str | None = None
    target_sku: str | None = None
    source_monthly_cost: float | None = None
    pricing_method: str | None = None
    pricing_reason: str | None = None
    pricing_evidence: dict[str, Any] = Field(default_factory=dict)
    # Exactly one alternative per priced component enters the provider scenario.
    included_in_estimate: bool = False


# ── Texto dos agentes ─────────────────────────────────────────────────────────
# O texto vem do modelo; o formato não. Os validadores abaixo normalizam tudo o que
# a tela lê (tema, título curto, etapas) e nunca levantam erro: um campo estranho
# vira o valor neutro, não derruba o `resilient_parse` da análise inteira. Rodam
# também na leitura, então análises antigas saem no mesmo contrato.

AgentTheme = Literal[
    "custo", "banco", "computacao", "seguranca", "precisao", "portabilidade", "operacao", "outro"
]
# Primeiro tema cuja palavra aparece no código, na área ou no texto do alerta. A ordem
# decide o empate ("compute_cost_accuracy" é precisão, "arm_compatibility" é computação).
# Sem nenhuma, "outro".
_THEMES: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (theme, tuple(keys.split()))
    for theme, keys in (
        (
            "precisao",
            "coverage accuracy quality reliab complet classif cobertura confiab precis "
            "uncertain missing metadata insufficient ausente indetermin",
        ),
        ("banco", "database banco rds data_migration cache dados"),
        ("seguranca", "security seguran waf kms iam complian conformidade"),
        ("computacao", "compute arm sizing kubernetes container instanc"),
        ("portabilidade", "lock_in lock-in refactor equivalen compatib portab"),
        ("custo", "cost financ commit saving pricing custo finops budget"),
        (
            "operacao",
            "validation operational organizational sequence business planning eligib risk",
        ),
    )
)
_THEME_NAMES = set(get_args(AgentTheme))
_DASH = re.compile(r"\s*[\u2014\u2013]\s*")  # travessão e meia-risca
_STEP_PREFIX = re.compile(r"^(wave|onda|fase|etapa|step)\s*\d+\s*[,:.\-]?\s*", re.IGNORECASE)
_DURATION = re.compile(r"\(([^()]*\d[^()]*)\)\s*$")
_TITLE_MAX = 90
_HEAD_MAX = 120  # cabeçalho maior que isso não é "nome: detalhe", é frase com dois-pontos


def _clean(value: Any) -> str:
    return _DASH.sub(", ", str(value or "")).strip()


def _short(text: str, limit: int = _TITLE_MAX) -> str:
    """Primeira frase, cortada na palavra. ". " não pega "$10.082"."""
    first = text.split(". ", 1)[0].rstrip(".")
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _theme_of(*texts: str) -> str:
    joined = " ".join(texts).lower()
    return next((theme for theme, keys in _THEMES if any(k in joined for k in keys)), "outro")


def _clean_list(value: Any) -> list[str]:
    return [_clean(item) for item in value] if isinstance(value, list) else []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class AgentFlag(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str = ""
    message: str = ""
    impact_area: str = ""
    title: str = Field(
        default="", description="Uma frase curta, até 90 caracteres, com o essencial do alerta."
    )
    theme: AgentTheme = Field(
        default="outro",
        description="Tema do alerta, um dos valores da lista.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = {"message": value}
        if not isinstance(value, dict):
            return {"message": _clean(value)}
        message = _clean(value.get("message"))
        code, area = str(value.get("code") or ""), _clean(value.get("impact_area"))
        title = _clean(value.get("title")) or message
        theme = value.get("theme")
        return {
            "code": code,
            "message": message,
            "impact_area": area,
            "title": _short(title),
            "theme": theme if theme in _THEME_NAMES else _theme_of(code, area, message),
        }


class MigrationStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(
        default="", description="Nome curto da etapa, sem número (ex.: Networking e identidade)."
    )
    duration: str | None = Field(default=None, description="Duração estimada (ex.: 2 a 3 semanas).")
    detail: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                "name": _short(_clean(value.get("name"))),
                "duration": _clean(value.get("duration")) or None,
                "detail": _clean(value.get("detail")),
            }
        text = _clean(value)
        head, sep, detail = text.partition(": ")
        if not sep or len(head) > _HEAD_MAX:
            return {"name": _short(text), "detail": text if len(text) > _TITLE_MAX else ""}
        duration = _DURATION.search(head)
        name = _DURATION.sub("", head).strip() if duration else head
        name = _STEP_PREFIX.sub("", name).strip() or name
        return {
            "name": _short(name),
            "duration": re.sub(r"(\d+)\s*-\s*(\d+)", r"\1 a \2", duration.group(1))
            if duration
            else None,
            "detail": detail.strip(),
        }


class FinOpsView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    confidence_level: str = "low"
    key_findings: list[str] = Field(default_factory=list)
    financial_flags: list[AgentFlag] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str:
        return _clean(value)

    @field_validator("key_findings", mode="before")
    @classmethod
    def clean_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("financial_flags", mode="before")
    @classmethod
    def as_list(cls, value: Any) -> list[Any]:
        return _as_list(value)


class CloudEngineeringView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    confidence_level: str = "low"
    migration_complexity: str = "unknown"
    technical_flags: list[AgentFlag] = Field(default_factory=list)
    technical_recommendations: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str:
        return _clean(value)

    @field_validator("technical_recommendations", mode="before")
    @classmethod
    def clean_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("technical_flags", mode="before")
    @classmethod
    def as_list(cls, value: Any) -> list[Any]:
        return _as_list(value)


class RiskMigrationView(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    confidence_level: str = "low"
    risk_flags: list[AgentFlag] = Field(default_factory=list)
    blocked_or_uncertain_items: list[str] = Field(default_factory=list)
    recommended_migration_sequence: list[MigrationStep] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str:
        return _clean(value)

    @field_validator("blocked_or_uncertain_items", mode="before")
    @classmethod
    def clean_lists(cls, value: Any) -> list[str]:
        return _clean_list(value)

    @field_validator("risk_flags", "recommended_migration_sequence", mode="before")
    @classmethod
    def as_list(cls, value: Any) -> list[Any]:
        return _as_list(value)


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

    viability_score: int = Field(default=0, ge=0, le=1000)
    oneshift_scores: OneshiftScores = Field(default_factory=OneshiftScores)
    provider_comparison: list[ProviderComparison] = Field(default_factory=list)
    cross_cloud_equivalents: list[CrossCloudEquivalent] = Field(default_factory=list)
    pricing_reconciliation: list[dict[str, Any]] = Field(default_factory=list)
    recommended_provider: str = ""
    recommendation: str = ""
    recommendation_rationale: str = ""
    analysis_confidence: str = "low"
    prompt_contract_version: str = "oneshift-operator-v2.1"
    agent_views: AgentViews = Field(default_factory=AgentViews)
    savings_summary: SavingsSummaryV2 = Field(default_factory=SavingsSummaryV2)
    flags: dict[str, list[Any]] = Field(default_factory=dict)
    strategic_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    decision_support: DecisionSupport | None = None

    @field_validator("flags", mode="before")
    @classmethod
    def normalize_flag_indicators(cls, value: Any) -> Any:
        """Accept boolean indicators without losing them or inventing evidence."""
        if not isinstance(value, dict):
            return value
        return {
            key: (["Sinalizado pela IA; sem detalhes adicionais."] if flag else [])
            if isinstance(flag, bool) else flag
            for key, flag in value.items()
        }

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
            return cls.model_validate(normalized)
        except Exception:
            logger.warning("analysis_v2_parse_fallback", exc_info=True)
            try:
                return cls.model_validate({})
            except Exception:
                return cls()


class AnalysisNarrativeDraft(AnalysisV2Response):
    """Internal transport only, when deterministic backend authority is available.

    Discard model-authored decisions BEFORE client validation. Inherited defaults
    are temporary placeholders, never published: the orchestrator imposes the
    trusted snapshot then validates the public response. Unknown workload prices
    remain null; this does not relax the legacy/public numeric score contract.
    """

    @model_validator(mode="before")
    @classmethod
    def discard_model_decisions(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        value = _normalize_top(value)
        for key in (
            "viability_score", "oneshift_scores", "decision_support",
            "recommended_provider", "analysis_confidence",
            "cross_cloud_equivalents",
            "pricing_reconciliation",
        ):
            value.pop(key, None)
        savings = value.get("savings_summary")
        if savings is None:
            value.pop("savings_summary", None)
        elif isinstance(savings, dict):
            value["savings_summary"] = {
                k: v for k, v in savings.items()
                if k not in {"total_monthly_savings_usd", "percentage_reduction"}
            }
        comparisons = value.get("provider_comparison")
        if isinstance(comparisons, list):
            owned = {
                "financial_score", "technical_score", "enterprise_score", "overall_score",
                "estimated_monthly_cost", "estimated_monthly_savings",
            }
            value["provider_comparison"] = [
                {k: v for k, v in item.items() if k not in owned}
                if isinstance(item, dict) else item
                for item in comparisons
            ]
        return value


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
    decision_context: DecisionContext | None = None
