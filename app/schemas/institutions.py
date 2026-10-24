from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.entities.institution import (
    InstitutionProfile,
    InstitutionRole,
    InstitutionView,
    views_of_profile,
)

InstitutionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)
]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]


class InstitutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: InstitutionName


class CreateInstitutionRequest(InstitutionRequest):
    display_name: DisplayName


class JoinInstitutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{32}$")]
    display_name: DisplayName


class UpdateMemberRequest(BaseModel):
    """`views` combina financeira, técnica e estratégica. `profile` (uma visão só) é o
    formato anterior, aceito enquanto houver cliente que o mande."""

    model_config = ConfigDict(extra="forbid")
    role: InstitutionRole
    views: list[InstitutionView] | None = Field(default=None, min_length=1, max_length=3)
    profile: InstitutionProfile | None = None
    can_publish: bool

    @model_validator(mode="after")
    def _views_or_profile(self) -> "UpdateMemberRequest":
        if self.views is None and self.profile is None:
            raise ValueError("Informe as visões do membro.")
        return self

    def chosen_views(self) -> frozenset[InstitutionView]:
        if self.views is not None:
            return frozenset(self.views)
        assert self.profile is not None
        return views_of_profile(self.profile)


class PublishAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID


class MemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    institution_id: UUID
    user_id: UUID
    display_name: str
    role: InstitutionRole
    profile: InstitutionProfile
    views: list[InstitutionView] = Field(
        description="o que o membro vê de uma análise de outro membro; administrador, todas"
    )
    can_publish: bool
    joined_at: datetime


class InstitutionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    created_at: datetime
    membership: MemberResponse


class InviteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


class CreateInviteResponse(InviteResponse):
    code: str


class PublicationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    institution_id: UUID
    published_by: UUID
    source_job_id: UUID
    title: str
    snapshot: dict[str, Any]
    created_at: datetime


class SharedAnalysisResponse(BaseModel):
    """A análise publicada no formato de `GET /analyses/{job_id}/result`, recortada pelas
    visões de quem abre. Sem fatura (`report_id` nulo) e sem o payload do agente: de
    `llm_input_payload` sai só o que a tela lê (a origem e, com a visão financeira, o gasto
    e o câmbio)."""

    model_config = ConfigDict(frozen=True)
    job_id: UUID
    status: str
    report_id: UUID | None = None
    llm_output: str | None = None
    error: str | None = None
    result: dict[str, Any] | None
    llm_input_payload: dict[str, Any]
    diagnostico_de_entrada: dict[str, Any] | None = None
    created_at: datetime
    views: list[InstitutionView]
