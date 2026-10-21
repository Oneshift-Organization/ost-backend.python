from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.infrastructure.repositories.pg_institution_repo import PgInstitutionRepository
from app.schemas.institutions import (
    CreateInstitutionRequest,
    CreateInviteResponse,
    InstitutionRequest,
    InstitutionResponse,
    InviteResponse,
    JoinInstitutionRequest,
    MemberResponse,
    PublicationResponse,
    PublishAnalysisRequest,
    UpdateMemberRequest,
)
from app.use_cases.institutions import (
    CreateInstitutionInput,
    CreateInstitutionUseCase,
    JoinInstitutionInput,
    JoinInstitutionUseCase,
    PublishAnalysisInput,
    PublishAnalysisUseCase,
)


class InstitutionRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except (NotFoundError, ForbiddenError, ConflictError) as exc:
                codes = {NotFoundError: 404, ForbiddenError: 403, ConflictError: 409}
                raise HTTPException(status_code=codes[type(exc)], detail=str(exc)) from exc
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(prefix="/institutions", tags=["institutions"], route_class=InstitutionRoute)


def institution_repository(session: DbSession) -> PgInstitutionRepository:
    return PgInstitutionRepository(session)


Repository = Annotated[PgInstitutionRepository, Depends(institution_repository)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=list[InstitutionResponse])
async def list_institutions(
    user: CurrentUser, repo: Repository, limit: Limit = 50, offset: Offset = 0
) -> list[dict[str, Any]]:
    return await repo.list_for_user(UUID(user.sub), limit, offset)


@router.post("", response_model=InstitutionResponse, status_code=201)
async def create_institution(
    body: CreateInstitutionRequest, user: CurrentUser, repo: Repository
) -> dict[str, Any]:
    return await CreateInstitutionUseCase(repo).execute(
        CreateInstitutionInput(UUID(user.sub), body.name, body.display_name)
    )


@router.post("/memberships", response_model=InstitutionResponse, status_code=201)
async def join_institution(
    body: JoinInstitutionRequest, user: CurrentUser, repo: Repository
) -> dict[str, Any]:
    return await JoinInstitutionUseCase(repo).execute(
        JoinInstitutionInput(UUID(user.sub), body.code, body.display_name)
    )


@router.get("/{institution_id}", response_model=InstitutionResponse)
async def get_institution(
    institution_id: UUID, user: CurrentUser, repo: Repository
) -> dict[str, Any]:
    return await repo.detail(UUID(user.sub), institution_id)


@router.patch("/{institution_id}", status_code=204)
async def rename_institution(
    institution_id: UUID, body: InstitutionRequest, user: CurrentUser, repo: Repository
) -> None:
    await repo.rename(UUID(user.sub), institution_id, body.name)


@router.delete("/{institution_id}", status_code=204)
async def delete_institution(institution_id: UUID, user: CurrentUser, repo: Repository) -> None:
    await repo.remove(UUID(user.sub), institution_id)


@router.get("/{institution_id}/members", response_model=list[MemberResponse])
async def list_members(
    institution_id: UUID, user: CurrentUser, repo: Repository, limit: Limit = 50, offset: Offset = 0
) -> list[dict[str, Any]]:
    return await repo.members(UUID(user.sub), institution_id, limit, offset)


@router.patch("/{institution_id}/members/{member_id}", status_code=204)
async def update_member(
    institution_id: UUID,
    member_id: UUID,
    body: UpdateMemberRequest,
    user: CurrentUser,
    repo: Repository,
) -> None:
    await repo.update_member(
        UUID(user.sub),
        institution_id,
        member_id,
        role=body.role,
        profile=body.profile,
        can_publish=body.can_publish,
    )


@router.delete("/{institution_id}/members/{member_id}", status_code=204)
async def remove_member(
    institution_id: UUID, member_id: UUID, user: CurrentUser, repo: Repository
) -> None:
    await repo.remove_member(UUID(user.sub), institution_id, member_id)


@router.post("/{institution_id}/invites", response_model=CreateInviteResponse, status_code=201)
async def create_invite(
    institution_id: UUID, user: CurrentUser, repo: Repository
) -> dict[str, Any]:
    return await repo.create_invite(UUID(user.sub), institution_id)


@router.get("/{institution_id}/invites", response_model=list[InviteResponse])
async def list_invites(
    institution_id: UUID, user: CurrentUser, repo: Repository, limit: Limit = 50, offset: Offset = 0
) -> list[dict[str, Any]]:
    return await repo.invites(UUID(user.sub), institution_id, limit, offset)


@router.delete("/{institution_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    institution_id: UUID, invite_id: UUID, user: CurrentUser, repo: Repository
) -> None:
    await repo.revoke_invite(UUID(user.sub), institution_id, invite_id)


@router.post("/{institution_id}/publications", response_model=PublicationResponse, status_code=201)
async def publish_analysis(
    institution_id: UUID, body: PublishAnalysisRequest, user: CurrentUser, repo: Repository
) -> dict[str, Any]:
    return await PublishAnalysisUseCase(repo).execute(
        PublishAnalysisInput(UUID(user.sub), institution_id, body.job_id)
    )


@router.get("/{institution_id}/publications", response_model=list[PublicationResponse])
async def list_publications(
    institution_id: UUID, user: CurrentUser, repo: Repository, limit: Limit = 50, offset: Offset = 0
) -> list[dict[str, Any]]:
    return await repo.publications(UUID(user.sub), institution_id, limit, offset)


@router.get("/{institution_id}/publications/{publication_id}", response_model=PublicationResponse)
async def get_publication(
    institution_id: UUID, publication_id: UUID, user: CurrentUser, repo: Repository
) -> dict[str, Any]:
    return await repo.publication(UUID(user.sub), institution_id, publication_id)


@router.delete("/{institution_id}/publications/{publication_id}", status_code=204)
async def unpublish_analysis(
    institution_id: UUID, publication_id: UUID, user: CurrentUser, repo: Repository
) -> None:
    await repo.unpublish(UUID(user.sub), institution_id, publication_id)
