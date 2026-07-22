from __future__ import annotations

from pydantic import BaseModel, Field


class Flag(BaseModel):
    code: str
    message: str
    impact_area: str = "operations"


class SavingsSummary(BaseModel):
    total_monthly_savings_usd: float
    percentage_reduction: float
    payback_period_months: int | None = None
    confidence_level: str = "low"


class AnalysisUIResponse(BaseModel):
    viability_score: int = Field(..., ge=0, le=100)
    recommendation: str
    recommendation_rationale: str = ""
    recommended_provider: str = ""
    savings_summary: SavingsSummary
    flags: dict[str, list[Flag]]
    strategic_notes: list[str]
    action_plan: list[str]
