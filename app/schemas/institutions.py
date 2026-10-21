from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.domain.entities.institution import InstitutionProfile, InstitutionRole

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
    model_config = ConfigDict(extra="forbid")
    role: InstitutionRole
    profile: InstitutionProfile
    can_publish: bool


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
