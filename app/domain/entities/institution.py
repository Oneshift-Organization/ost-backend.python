from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.exceptions import ConflictError, ForbiddenError
from app.domain.entities.institution_dashboard import dashboard_snapshot


class InstitutionRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class InstitutionProfile(StrEnum):
    ADMINISTRATIVE = "administrative"
    FINANCIAL = "financial"
    STRATEGIC = "strategic"
    ECONOMIST = "economist"
    TECHNICAL = "technical"


class InstitutionView(StrEnum):
    """O que um membro vê de uma análise compartilhada; combináveis entre si."""

    FINANCIAL = "financial"
    TECHNICAL = "technical"
    STRATEGIC = "strategic"


ALL_VIEWS = frozenset(InstitutionView)

# O perfil único de antes vira as visões que ele mostrava
_VIEWS_OF_PROFILE = {
    InstitutionProfile.ADMINISTRATIVE: ALL_VIEWS,
    InstitutionProfile.FINANCIAL: frozenset({InstitutionView.FINANCIAL}),
    InstitutionProfile.ECONOMIST: frozenset({InstitutionView.FINANCIAL}),
    InstitutionProfile.TECHNICAL: frozenset({InstitutionView.TECHNICAL}),
    InstitutionProfile.STRATEGIC: frozenset({InstitutionView.STRATEGIC}),
}


def views_of_profile(profile: InstitutionProfile) -> frozenset[InstitutionView]:
    return _VIEWS_OF_PROFILE[profile]


def profile_of_views(views: frozenset[InstitutionView]) -> InstitutionProfile:
    """`profile` segue gravado para quem ainda o lê: uma visão é ela mesma; mais de uma, a geral."""
    if len(views) == 1:
        return InstitutionProfile(next(iter(views)).value)
    return InstitutionProfile.ADMINISTRATIVE


@dataclass(frozen=True)
class Membership:
    institution_id: UUID
    user_id: UUID
    display_name: str
    role: InstitutionRole
    profile: InstitutionProfile
    can_publish: bool
    joined_at: datetime
    views: frozenset[InstitutionView] = frozenset()

    def visible_views(self) -> frozenset[InstitutionView]:
        """Administrador vê a análise inteira; membro, as visões que um administrador marcou."""
        if self.role == InstitutionRole.ADMIN:
            return ALL_VIEWS
        return self.views or views_of_profile(self.profile)

    def require_admin(self) -> None:
        if self.role != InstitutionRole.ADMIN:
            raise ForbiddenError("Somente administradores podem realizar esta ação.")

    def require_publisher(self) -> None:
        if self.role != InstitutionRole.ADMIN and not self.can_publish:
            raise ForbiddenError("Seu acesso permite apenas consultar as análises compartilhadas.")


def protect_last_admin(member: Membership, admin_count: int) -> None:
    if member.role == InstitutionRole.ADMIN and admin_count <= 1:
        raise ConflictError(
            "Transfira a administração para outro membro ou exclua a instituição antes de sair."
        )


@dataclass(frozen=True)
class SharedAnalysisAccess:
    """A análise por trás de uma publicação, com o que quem a abre pode ver."""

    owner_id: UUID
    job_id: UUID
    views: frozenset[InstitutionView]

    def require(self, *any_of: InstitutionView) -> None:
        if self.views.isdisjoint(any_of):
            raise ForbiddenError("Esta parte da análise não faz parte da sua visão.")


@dataclass(frozen=True)
class PublicationSource:
    job_id: UUID
    report_id: UUID
    title: str
    provider: str
    analyzed_at: datetime
    result: dict[str, Any]


# Explicit allowlist: no raw billing, inventory, prompts or agent payloads.
SHARED_RESULT_FIELDS = (
    "viability_score",
    "oneshift_scores",
    "provider_comparison",
    "recommended_provider",
    "recommendation",
    "recommendation_rationale",
    "analysis_confidence",
    "savings_summary",
    "strategic_notes",
    "action_plan",
)


def publication_snapshot(source: PublicationSource) -> dict[str, Any]:
    decision = source.result.get("decision_support")
    summary = None
    if isinstance(decision, dict):
        summary = {
            key: deepcopy(decision.get(key))
            for key in (
                "policy_version",
                "outcome",
                "selected_provider",
                "selection_reason",
                "comparison_basis",
                "limitations",
                "confidence",
            )
        }
        score = decision.get("score")
        if isinstance(score, dict):
            summary["score"] = {
                key: deepcopy(score.get(key))
                for key in (
                    "value",
                    "status",
                    "assessment_method",
                    "missing_information",
                )
            }
            summary["axes"] = [
                {key: axis.get(key) for key in ("id", "label", "value", "weight")}
                for axis in score.get("axes", [])
                if isinstance(axis, dict)
            ]
    return {
        "version": "institution-dashboard-v2",
        "dashboard": dashboard_snapshot(source.result),
        "provider": source.provider,
        "analyzed_at": source.analyzed_at.replace(tzinfo=UTC).isoformat()
        if source.analyzed_at.tzinfo is None
        else source.analyzed_at.isoformat(),
        "decision": summary,
        "result": {
            key: deepcopy(source.result[key])
            for key in SHARED_RESULT_FIELDS
            if key in source.result
        },
    }
