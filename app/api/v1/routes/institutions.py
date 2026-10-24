from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.routing import APIRoute

from app.api.deps import (
    CurrentUser,
    DbSession,
    consultar_acoplamento_uc,
    escopo_de_migracao_uc,
)
from app.api.v1.envelope import erro
from app.api.v1.routes.analysis import diagnostico_da_analise
from app.api.v1.routes.migration_scope import ERROS_DO_ESCOPO, traduzir_erro_do_escopo
from app.core.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.domain.entities.institution import InstitutionView, SharedAnalysisAccess
from app.domain.entities.institution_views import (
    parse_views,
    shared_detail,
    shared_payload,
    shared_result,
)
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.infrastructure.repositories.pg_billing_report_repo import PgBillingReportRepository
from app.infrastructure.repositories.pg_institution_repo import PgInstitutionRepository
from app.schemas.analysis_v2 import AnalysisV2Response
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
    SharedAnalysisResponse,
    UpdateMemberRequest,
)
from app.schemas.migration_scope import ApplicationsResponse
from app.schemas.service_couplings import ServiceCouplingsResponse
from app.use_cases.analysis.consultar_acoplamento import AnaliseSemServicosError
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
Views = Annotated[
    str | None,
    Query(description="ver com menos visões que as próprias: financial,technical,strategic"),
]


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
        views=body.chosen_views(),
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


async def _shared(
    repo: PgInstitutionRepository,
    user: CurrentUser,
    institution_id: UUID,
    publication_id: UUID,
    views: str | None,
) -> SharedAnalysisAccess:
    return await repo.shared_analysis(
        UUID(user.sub), institution_id, publication_id, parse_views(views)
    )


@router.get(
    "/{institution_id}/publications/{publication_id}/analysis",
    response_model=SharedAnalysisResponse,
)
async def get_shared_analysis(
    institution_id: UUID,
    publication_id: UUID,
    user: CurrentUser,
    repo: Repository,
    *,
    session: DbSession,
    views: Views = None,
) -> dict[str, Any]:
    """A análise publicada, igual à que o autor vê, recortada pelas visões de quem abre.

    404 quando a análise original não existe mais: a tela mostra o resumo da publicação.
    """
    access = await _shared(repo, user, institution_id, publication_id, views)
    job = await PgAnalysisJobRepository(session).get_by_id(access.job_id, access.owner_id)
    if job is None or job.status != "COMPLETED" or not job.ai_structured_output:
        raise NotFoundError("A análise original não está mais disponível.")
    report = await PgBillingReportRepository(session).get_by_id(job.report_id, access.owner_id)
    diagnostico = (
        diagnostico_da_analise(
            report.schema_version,
            report.provider.value if report.provider else None,
            job.llm_input_payload,
        )
        if report is not None
        else None
    )
    result = AnalysisV2Response.resilient_parse(job.ai_structured_output)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "result": shared_result(result.model_dump(mode="json"), access.views),
        "llm_input_payload": shared_payload(job.llm_input_payload, access.views),
        "diagnostico_de_entrada": shared_detail(diagnostico.model_dump(mode="json"), access.views)
        if diagnostico
        else None,
        "created_at": job.created_at,
        "views": sorted(access.views),
    }


@router.get("/{institution_id}/publications/{publication_id}/service-couplings")
async def get_shared_couplings(
    institution_id: UUID,
    publication_id: UUID,
    user: CurrentUser,
    repo: Repository,
    *,
    session: DbSession,
    views: Views = None,
) -> dict[str, Any]:
    """Os serviços da fatura e o acoplamento de cada um, no formato de
    `GET /analyses/{job_id}/service-couplings`. Lê a aba Ambiente atual (visão financeira) e a
    Portabilidade (técnica); sem a visão financeira, sem valores."""
    access = await _shared(repo, user, institution_id, publication_id, views)
    access.require(InstitutionView.TECHNICAL, InstitutionView.FINANCIAL)
    try:
        visao = await consultar_acoplamento_uc(session).da_analise(access.owner_id, access.job_id)
    except AnaliseSemServicosError as e:
        raise erro(status.HTTP_409_CONFLICT, "ANALISE_SEM_SERVICOS", str(e), retryable=False) from e
    except ERROS_DO_ESCOPO as e:
        raise traduzir_erro_do_escopo(e) from e
    data = ServiceCouplingsResponse.de(visao).model_dump(mode="json")
    return dict(shared_detail(data, access.views))


@router.get("/{institution_id}/publications/{publication_id}/applications")
async def get_shared_applications(
    institution_id: UUID,
    publication_id: UUID,
    user: CurrentUser,
    repo: Repository,
    *,
    session: DbSession,
    views: Views = None,
) -> dict[str, Any]:
    """As aplicações e dependências da análise publicada (visão técnica), no formato de
    `GET /analyses/{job_id}/applications`. Só leitura: o Terraform continua com o autor."""
    access = await _shared(repo, user, institution_id, publication_id, views)
    access.require(InstitutionView.TECHNICAL)
    if not get_settings().inventory_enabled:
        raise erro(
            status.HTTP_404_NOT_FOUND,
            "INVENTARIO_DESLIGADO",
            "o inventário não está habilitado neste ambiente",
            retryable=False,
        )
    try:
        ctx = await escopo_de_migracao_uc(session).aplicacoes(access.owner_id, access.job_id)
    except ERROS_DO_ESCOPO as e:
        raise traduzir_erro_do_escopo(e) from e
    data = ApplicationsResponse.de(ctx).model_dump(mode="json")
    return dict(shared_detail(data, access.views))
