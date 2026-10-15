import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    DbSession,
    construtor_de_contexto,
    consultar_acoplamento_uc,
    payload_do_agente_uc,
)
from app.api.v1.envelope import erro, erro_do_payload
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.domain.analysis import MigrationContext
from app.domain.entities.analysis_job import AnalysisJob, DispStatus, agora_utc
from app.domain.services.diagnostico_de_entrada import diagnosticar_payload
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.llm.claude_client import ClaudeLLMClient
from app.infrastructure.llm.gemini_client import GeminiLLMClient
from app.infrastructure.llm.mock_client import MockLLMClient
from app.infrastructure.llm.nvidia_client import NvidiaLLMClient
from app.infrastructure.llm.openrouter_client import OpenRouterLLMClient
from app.infrastructure.llm.orchestrator import LLMOrchestrator, log_llm_input_payload
from app.infrastructure.mcp.prompts_client import (
    get_mcp_prompt_client,
)
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.pg_exchange_rate_repo import (
    PgExchangeRateRepository,
)
from app.infrastructure.repositories.pg_inventory_repo import PgInventoryRepository
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.schemas.analysis import (
    AnalysisJobResponse,
    AnalysisJobSummary,
    AnalysisRequest,
    DispStatusIn,
    DispStatusOut,
    EquivalentServiceItem,
    FinancialEstimateItem,
    MigrationContextResponse,
    ServiceCategoryGroupItem,
    ServiceSummaryItem,
    SourceUsageItem,
    TechnicalMatchItem,
    WorkloadSummaryItem,
)
from app.schemas.analysis_v2 import AnalysisV2Response, LegacyAnalysisRequest
from app.schemas.decision_support import AnalysisStartOptions
from app.schemas.diagnostico_de_entrada import DiagnosticoDeEntradaOut
from app.schemas.service_couplings import ServiceCouplingsResponse
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)
from app.use_cases.analysis.consultar_acoplamento import (
    AnaliseFalhouError,
    AnaliseNaoConcluidaError,
    AnaliseSemServicosError,
)
from app.use_cases.analysis.payload_do_agente import PayloadDoAgenteError
from app.use_cases.analysis.run_analysis_pipeline import (
    RunAnalysisPipelineUseCase,
    build_pipeline_llm_input_payload,
    serialize_as_legacy,
)
from app.use_cases.analysis.topologia_no_payload import (
    MontarTopologiaUseCase,
    compactar_topologia,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analyses")

_MAX_ANALYSES_PER_PAGE = 100


def _texto(valor: object) -> str | None:
    """Campo de `ai_structured_output` que o schema declara como texto.

    O LLM devolve JSON livre: `recommendation` já veio como string, mas nada
    impede que venha como objeto ou lista. O schema declara `str | None` e o
    Pydantic recusa qualquer outra coisa — o que derrubava a requisição inteira.
    """
    if valor is None or isinstance(valor, str):
        return valor
    return str(valor)


def _inteiro(valor: object) -> int | None:
    """Idem para os numéricos. Float com parte fracionária não converte para
    `int` no Pydantic; aqui vira `None` em vez de erro."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    try:
        return int(float(valor))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _build_response(ctx: MigrationContext) -> MigrationContextResponse:
    return MigrationContextResponse(
        source_provider=ctx.source_provider,
        billing_period=ctx.billing_period,
        total_spend=ctx.total_spend,
        currency=ctx.currency,
        categories=[
            ServiceCategoryGroupItem(
                category=c.category,
                billing_total=c.billing_total,
                services=[
                    ServiceSummaryItem(
                        service_name=s.service_name,
                        service_type=s.service_type,
                        total_cost=s.total_cost,
                        line_count=s.line_count,
                        equivalents=[
                            EquivalentServiceItem(
                                provider=e.provider,
                                service_name=e.service_name,
                                category=e.category,
                                service_type=e.service_type,
                                metadata=e.metadata,
                                docs_costs=e.docs_costs,
                            )
                            for e in s.equivalents
                        ],
                    )
                    for s in c.services
                ],
            )
            for c in ctx.categories
        ],
        workloads=[
            WorkloadSummaryItem(
                canonical_service=w.canonical_service,
                source_usage=SourceUsageItem(
                    sku_id=w.source_usage.sku_id,
                    usage_quantity=w.source_usage.usage_quantity,
                    billed_cost=w.source_usage.billed_cost,
                    currency=w.source_usage.currency,
                ),
                equivalents=[
                    EquivalentServiceItem(
                        provider=e.provider,
                        service_name=e.service_name,
                        category=e.category,
                        service_type=e.service_type,
                        metadata=e.metadata,
                        docs_costs=e.docs_costs,
                        technical_match=TechnicalMatchItem(
                            vcpus=e.technical_match.vcpus,
                            ram_gb=e.technical_match.ram_gb,
                        )
                        if e.technical_match
                        else None,
                        financials=FinancialEstimateItem(
                            unit_price=e.financials.unit_price,
                            estimated_monthly_cost=e.financials.estimated_monthly_cost,
                            cost_delta=e.financials.cost_delta,
                            pricing_confidence=e.financials.pricing_confidence,
                        )
                        if e.financials
                        else None,
                    )
                    for e in w.equivalents
                ],
            )
            for w in ctx.workloads
        ],
    )


@router.put(
    "/{job_id}/disp-status",
    response_model=DispStatusOut,
    status_code=status.HTTP_200_OK,
    summary="Ativa ou depreciada — é o que o front chama no lugar de excluir",
)
async def definir_disp_status(
    job_id: UUID,
    corpo: DispStatusIn,
    user: CurrentUser,
    session: DbSession,
) -> DispStatusOut:
    """Depreciar não apaga.

    Um DELETE levaria junto o `llm_input_payload`, que é a fonte do Terraform e
    de qualquer reanálise — quem excluísse por engano perderia o pacote que já
    baixou, sem volta. Aqui o registro fica, some da listagem, e o expurgo leva
    as depreciadas há mais de um mês.

    Idempotente: mandar o mesmo valor duas vezes devolve 200 e só recarimba a
    data, o que importa porque o front pode reenviar num retry.
    """
    user_uuid = UUID(user.sub)
    job = (
        (
            await session.execute(
                select(AnalysisJobModel).where(
                    AnalysisJobModel.job_id == job_id,
                    AnalysisJobModel.user_id == user_uuid,
                )
            )
        )
        .scalars()
        .first()
    )

    # 404 e não 403: dizer "existe mas não é sua" já entrega a existência.
    if job is None:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "ANALISE_NAO_ENCONTRADA",
            f"análise {job_id} não existe para este usuário",
            retryable=False,
        )

    job.disp_status = int(corpo.disp_status)
    # Carimbar é o que ancora a carência do expurgo. Sem data, o primeiro
    # expurgo levaria tudo que já estivesse depreciado.
    job.disp_status_updated_at = agora_utc()
    await session.commit()

    logger.info("disp_status_alterado job_id=%s valor=%s", job_id, corpo.disp_status)
    return DispStatusOut(
        job_id=job.job_id,
        disp_status=job.disp_status,
        disp_status_updated_at=job.disp_status_updated_at,
    )


@router.get("", response_model=list[AnalysisJobSummary])
async def list_analysis_jobs(
    user: CurrentUser,
    session: DbSession,
    limit: int = 50,
    incluir_depreciadas: bool = False,
) -> list[AnalysisJobSummary]:
    """Depreciadas ficam de fora por default.

    É o que faz o `PUT .../disp-status` parecer exclusão para o usuário sem ser
    uma. `incluir_depreciadas=true` existe para uma tela de lixeira, se houver.
    """
    if limit < 1 or limit > _MAX_ANALYSES_PER_PAGE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"limit must be 1..{_MAX_ANALYSES_PER_PAGE}",
        )

    user_uuid = UUID(user.sub)
    stmt = (
        select(AnalysisJobModel, BillingReportModel)
        .join(BillingReportModel, AnalysisJobModel.report_id == BillingReportModel.id)
        .where(
            AnalysisJobModel.user_id == user_uuid,
            BillingReportModel.user_id == user_uuid,
            *(() if incluir_depreciadas else (AnalysisJobModel.disp_status == DispStatus.ATIVA,)),
        )
        .order_by(AnalysisJobModel.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)

    summaries: list[AnalysisJobSummary] = []
    for job, report in result.all():
        structured = job.ai_structured_output if isinstance(job.ai_structured_output, dict) else {}
        decision = structured.get("decision_support") or {}
        decision_score = decision.get("score") or {}
        try:
            summaries.append(
                AnalysisJobSummary(
                    job_id=job.job_id,
                    report_id=job.report_id,
                    external_id=job.external_id,
                    status=job.status,
                    provider=report.provider,
                    source_filename=report.source_filename,
                    row_count=report.row_count or 0,
                    recommended_provider=_texto(structured.get("recommended_provider")),
                    recommendation=_texto(structured.get("recommendation")),
                    viability_score=_inteiro(structured.get("viability_score")),
                    analysis_confidence=_texto(structured.get("analysis_confidence")),
                    decision_score_status=(
                        decision_score.get("status", "not_scored") if decision else None
                    ),
                    decision_score_lower_bound=decision_score.get("lower_bound"),
                    decision_score_upper_bound=decision_score.get("upper_bound"),
                    error=job.error,
                    created_at=job.created_at,
                    diagnostico_de_entrada=_diagnostico_de_entrada(
                        report.schema_version, report.provider, job.llm_input_payload
                    ),
                )
            )
        except ValidationError:
            logger.exception(
                "analysis_summary_invalido job_id=%s",
                job.job_id,
            )
            summaries.append(
                AnalysisJobSummary(
                    job_id=job.job_id,
                    report_id=job.report_id,
                    status=str(job.status),
                    created_at=job.created_at,
                    error=("resumo indisponível: dados da análise em formato inesperado"),
                )
            )

    return summaries


@router.post(
    "/migration-context",
    response_model=MigrationContextResponse,
    status_code=status.HTTP_200_OK,
)
async def build_migration_context(
    user: CurrentUser,
    session: DbSession,
    body: AnalysisRequest,
) -> MigrationContextResponse:
    uc = BuildMigrationContextUseCase(
        PgBillingReportRepository(session),
        PgBillingEnrichedRepository(session),
        PGServiceMetadataRepository(session),
        PGServiceComparisonRepository(session),
        sku_repo=PgCloudSkuRepository(session),
        fx_repo=PgExchangeRateRepository(session),
        price_repo=PgCloudPricePointRepository(session),
    )
    try:
        ctx = await uc.execute(
            BuildMigrationContextInput(
                user_id=UUID(user.sub),
                report_id=body.report_id,
            )
        )
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    return _build_response(ctx)


@router.get(
    "/{job_id}/agents-payload",
    response_model=MigrationContextResponse,
    status_code=status.HTTP_200_OK,
)
async def get_agents_payload(
    job_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> MigrationContextResponse:
    uc = BuildMigrationContextUseCase(
        PgBillingReportRepository(session),
        PgBillingEnrichedRepository(session),
        PGServiceMetadataRepository(session),
        PGServiceComparisonRepository(session),
        sku_repo=PgCloudSkuRepository(session),
        fx_repo=PgExchangeRateRepository(session),
        price_repo=PgCloudPricePointRepository(session),
    )
    try:
        ctx = await uc.execute(
            BuildMigrationContextInput(
                user_id=UUID(user.sub),
                report_id=job_id,
            )
        )
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    return _build_response(ctx)


def _diagnostico_de_entrada(
    formato: str | None, provedor: str | None, payload: object
) -> DiagnosticoDeEntradaOut | None:
    """O que os arquivos permitem, lido do formato da fatura e do payload congelado.
    Nunca derruba a rota: um payload antigo em forma inesperada só fica sem aviso."""
    try:
        d = diagnosticar_payload(
            formato=formato,
            provedor_da_fatura=provedor,
            payload=payload if isinstance(payload, dict) else None,
        )
    except Exception:
        logger.exception("diagnostico_de_entrada_falhou formato=%s", formato)
        return None
    return DiagnosticoDeEntradaOut.de(d) if d.problemas else None


def _erro(
    status_code: int,
    codigo: str,
    mensagem: str,
    *,
    retryable: bool,
    **extra,
) -> HTTPException:
    corpo: dict = {
        "codigo": codigo,
        "mensagem": mensagem,
        "retryable": retryable,
    }
    corpo.update(extra)
    return HTTPException(status_code=status_code, detail=corpo)


async def _validar_inventario(
    session: AsyncSession, user_id: UUID, report_id: UUID, inventory_id: UUID
) -> None:
    """RI14: inventário do usuário, processado e da mesma nuvem do billing."""
    try:
        await payload_do_agente_uc(session).validar_inventario(user_id, report_id, inventory_id)
    except PayloadDoAgenteError as e:
        raise erro_do_payload(e) from e


@router.get(
    "/{report_id}/request",
    status_code=status.HTTP_200_OK,
    summary="Request de análise pronta para consumo por agente (não chama LLM)",
)
async def get_analysis_request(
    report_id: UUID,
    user: CurrentUser,
    session: DbSession,
    recalcular: bool = False,
    inventory_id: UUID | None = None,
) -> dict:
    user_uuid = UUID(user.sub)
    montador = payload_do_agente_uc(session)
    try:
        if inventory_id is not None:
            await montador.validar_inventario(user_uuid, report_id, inventory_id)
            recalcular = True
        await montador.exigir_pronto(user_uuid, report_id)
    except PayloadDoAgenteError as e:
        raise erro_do_payload(e) from e

    if not recalcular:
        row = (
            (
                await session.execute(
                    select(AnalysisJobModel)
                    .where(
                        AnalysisJobModel.report_id == report_id,
                        AnalysisJobModel.user_id == user_uuid,
                        AnalysisJobModel.llm_input_payload.isnot(None),
                    )
                    .order_by(AnalysisJobModel.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

        if row is not None and row.llm_input_payload:
            # A superfície do agente recebe a topologia compacta, como o LLM.
            return {
                **compactar_topologia(dict(row.llm_input_payload)),
                "_meta": {
                    "origem": "persistida",
                    "report_id": str(report_id),
                    "job_id": str(row.job_id),
                },
            }

    try:
        montado = await montador.montar(user_uuid, report_id, inventory_id)
    except PayloadDoAgenteError as e:
        raise erro_do_payload(e) from e
    return {
        **compactar_topologia(montado.payload),
        "_meta": {
            "origem": "recalculada",
            "report_id": str(report_id),
            "pools_sem_equivalente": montado.pools_sem_equivalente,
        },
    }


@router.get(
    "/{report_id}/llm-input-preview",
    status_code=status.HTTP_200_OK,
    deprecated=True,
    summary="DEPRECADO — use GET /{report_id}/request",
)
async def get_llm_input_preview(
    report_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    user_uuid = UUID(user.sub)
    stmt = (
        select(AnalysisJobModel)
        .where(
            AnalysisJobModel.report_id == report_id,
            AnalysisJobModel.user_id == user_uuid,
            AnalysisJobModel.llm_input_payload.isnot(None),
        )
        .order_by(AnalysisJobModel.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalars().first()

    if row is not None and row.llm_input_payload:
        return {
            "billing_data": dict(row.llm_input_payload),
            "missing_equivalent_pool_ids": [],
            "source": "persisted",
        }

    ctx = await _build_ctx(user_uuid, report_id, session)
    payload, missing_equiv_ids = build_pipeline_llm_input_payload(ctx)
    log_llm_input_payload("preview", payload)

    return {
        "billing_data": payload,
        "missing_equivalent_pool_ids": missing_equiv_ids,
        "source": "recomputed",
    }


async def _run_analysis_pipeline(
    user_id: UUID,
    report_id: UUID,
    job_id: UUID,
    sessionmaker,
    decision_context: dict | None = None,
    *,
    inventory_id: UUID | None = None,
) -> None:
    logger.info("pipeline_started job_id=%s", job_id)

    async with sessionmaker() as session:
        job_repo = PgAnalysisJobRepository(session)
        job = await job_repo.get_by_id(job_id, user_id)

        if job is None:
            logger.warning("pipeline_job_not_found job_id=%s", job_id)
            return

        logger.info(
            "pipeline_job_loaded job_id=%s status=%s",
            job_id,
            job.status,
        )

        orchestrator = _make_orchestrator()

        uc = RunAnalysisPipelineUseCase(
            analysis_job_repo=job_repo,
            report_repo=PgBillingReportRepository(session),
            item_repo=PgBillingEnrichedRepository(session),
            metadata_repo=PGServiceMetadataRepository(session),
            comparison_repo=PGServiceComparisonRepository(session),
            sku_repo=PgCloudSkuRepository(session),
            fx_repo=PgExchangeRateRepository(session),
            price_repo=PgCloudPricePointRepository(session),
            llm_orchestrator=orchestrator,
            topologia=MontarTopologiaUseCase(
                PgInventoryRepository(session), PgBillingItemRepository(session)
            ),
        )

        try:
            await uc.execute(job, decision_context=decision_context, inventario_id=inventory_id)
            await session.commit()
            logger.info("pipeline_completed job_id=%s", job_id)
        except Exception:
            await session.rollback()
            logger.exception("pipeline_failed job_id=%s", job_id)
            job.mark_failed("unexpected pipeline error")
            await job_repo.update(job)
            await session.commit()
            logger.info(
                "pipeline_failed_status_saved job_id=%s",
                job_id,
            )


@router.post(
    "/start/{report_id}",
    response_model=AnalysisJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_analysis(
    report_id: UUID,
    user: CurrentUser,
    session: DbSession,
    background: BackgroundTasks,
    *,
    external_id: UUID | None = None,
    options: AnalysisStartOptions | None = None,
) -> AnalysisJobResponse:
    user_uuid = UUID(user.sub)
    inventory_id = options.inventory_id if options else None
    if inventory_id is not None:
        # Antes da idempotência: um id inválido não é engolido por um job ativo.
        await _validar_inventario(session, user_uuid, report_id, inventory_id)
    repo = PgAnalysisJobRepository(session)

    existente = await repo.buscar_ativo_por_report(
        report_id,
        user_uuid,
    )

    if existente is not None:
        return AnalysisJobResponse(
            job_id=existente.job_id,
            report_id=existente.report_id,
            external_id=existente.external_id,
            status=existente.status,
            created_at=existente.created_at,
        )

    job = AnalysisJob(
        job_id=uuid4(),
        user_id=user_uuid,
        report_id=report_id,
        external_id=external_id,
    )

    try:
        await repo.create(job)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()

        concorrente = await repo.buscar_ativo_por_report(
            report_id,
            user_uuid,
        )

        if concorrente is None:
            raise _erro(
                status.HTTP_409_CONFLICT,
                "ANALISE_DUPLICADA",
                "já existe uma análise para este report",
                retryable=False,
            ) from e

        return AnalysisJobResponse(
            job_id=concorrente.job_id,
            report_id=concorrente.report_id,
            external_id=concorrente.external_id,
            status=concorrente.status,
            created_at=concorrente.created_at,
        )

    sm = get_sessionmaker()
    background.add_task(
        _run_analysis_pipeline,
        user_uuid,
        report_id,
        job.job_id,
        sm,
        options.decision_context.model_dump(exclude_none=True)
        if options and options.decision_context
        else None,
        inventory_id=inventory_id,
    )

    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        created_at=job.created_at,
    )


@router.get(
    "/by-external-id/{external_id}",
    response_model=AnalysisJobResponse,
    status_code=status.HTTP_200_OK,
)
async def get_analysis_by_external_id(
    external_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> AnalysisJobResponse:
    repo = PgAnalysisJobRepository(session)
    job = await repo.get_by_external_id(
        external_id,
        UUID(user.sub),
    )

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="analysis job not found",
        )

    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        llm_output=job.llm_output,
        error=job.error,
        result=(
            AnalysisV2Response.resilient_parse(
                job.ai_structured_output,
            )
            if job.ai_structured_output
            else None
        ),
        llm_input_payload=job.llm_input_payload,
        created_at=job.created_at,
    )


def _make_llm_client() -> (
    MockLLMClient | GeminiLLMClient | OpenRouterLLMClient | ClaudeLLMClient | NvidiaLLMClient
):
    settings = get_settings()

    match settings.llm_provider:
        case "openrouter":
            return OpenRouterLLMClient(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
            )

        case "gemini":
            return GeminiLLMClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
            )

        case "anthropic":
            return ClaudeLLMClient(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
            )

        case "nvidia":
            return _make_nvidia_client()

        case _:
            return MockLLMClient()


def _make_nvidia_client() -> NvidiaLLMClient:
    settings = get_settings()

    return NvidiaLLMClient(
        api_key=settings.nvidia_api_key,
        model=settings.nvidia_model,
        base_url=settings.nvidia_base_url,
        timeout_seconds=settings.nvidia_timeout_seconds,
    )


def _make_orchestrator() -> LLMOrchestrator:
    settings = get_settings()

    primary_client = _make_llm_client()

    primary_uses_mcp = settings.llm_provider == "nvidia" and settings.mcp_prompts_enabled
    primary_mcp_client = get_mcp_prompt_client() if primary_uses_mcp else None

    fallback_enabled = (
        settings.llm_provider == "anthropic" and settings.llm_fallback_provider == "nvidia"
    )
    fallback_client = _make_nvidia_client() if fallback_enabled else None

    fallback_uses_mcp = fallback_enabled and settings.mcp_prompts_enabled
    fallback_mcp_client = get_mcp_prompt_client() if fallback_uses_mcp else None

    logger.info(
        "llm_runtime_selected primary=%s fallback=%s primary_mcp=%s fallback_mcp=%s",
        settings.llm_provider,
        settings.llm_fallback_provider if fallback_enabled else "none",
        primary_uses_mcp,
        fallback_uses_mcp,
    )

    return LLMOrchestrator(
        llm_client=primary_client,
        mcp_client=primary_mcp_client,
        fallback_client=fallback_client,
        fallback_mcp_client=fallback_mcp_client,
    )


@router.post(
    "/direct",
    response_model=AnalysisV2Response,
    status_code=status.HTTP_200_OK,
    summary="Direct analysis from pre-computed workload JSON (legacy format)",
)
async def direct_analysis(
    body: LegacyAnalysisRequest,
    user: CurrentUser,
) -> AnalysisV2Response:
    billing_data = {
        "source_provider": body.source_provider,
        "billing_period": body.billing_period,
        "currency": body.currency,
        "total_spend": body.total_spend,
        "company_context": (body.company_context.model_dump() if body.company_context else None),
        "workloads": [workload.model_dump() for workload in body.workloads],
    }
    if body.decision_context:
        billing_data["decision_context"] = body.decision_context.model_dump(exclude_none=True)

    orchestrator = _make_orchestrator()

    logger.info(
        "direct_analysis_started provider=%s period=%s",
        body.source_provider,
        body.billing_period,
    )

    try:
        result = await orchestrator.generate_from_legacy(
            billing_data,
        )
    except Exception as exc:
        logger.exception("direct_analysis_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM analysis failed: {exc}",
        ) from exc

    logger.info(
        "direct_analysis_completed viability=%s",
        result.viability_score,
    )
    return result


@router.get(
    "/{job_id}/result",
    response_model=AnalysisJobResponse,
    status_code=status.HTTP_200_OK,
)
async def get_analysis_result(
    job_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> AnalysisJobResponse:
    repo = PgAnalysisJobRepository(session)
    job = await repo.get_by_id(job_id, UUID(user.sub))

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="analysis job not found",
        )
    report = await PgBillingReportRepository(session).get_by_id(job.report_id, UUID(user.sub))

    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        llm_output=job.llm_output,
        error=job.error,
        result=(
            AnalysisV2Response.resilient_parse(
                job.ai_structured_output,
            )
            if job.ai_structured_output
            else None
        ),
        llm_input_payload=job.llm_input_payload,
        created_at=job.created_at,
        diagnostico_de_entrada=(
            _diagnostico_de_entrada(
                report.schema_version,
                report.provider.value if report.provider else None,
                job.llm_input_payload,
            )
            if report is not None
            else None
        ),
    )


@router.get(
    "/{job_id}/service-couplings",
    response_model=ServiceCouplingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Acoplamento por serviço da fatura, com o nível que o score usa",
)
async def get_service_couplings(
    job_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> ServiceCouplingsResponse:
    """Uma linha por serviço, calculada do payload salvo da análise.

    Substitui, na tela de acoplamento, a leitura de `cross_cloud_equivalents`
    (tabela de preço por destino). `409 ANALISE_NAO_CONCLUIDA` (repetir) ou
    `409 ANALISE_SEM_SERVICOS` (análise anterior à lista de serviços).
    Contrato: docs/endpoints/service-couplings.md.
    """
    try:
        visao = await consultar_acoplamento_uc(session).da_analise(UUID(user.sub), job_id)
    except NotFoundError as e:
        raise erro(
            status.HTTP_404_NOT_FOUND, "ANALISE_NAO_ENCONTRADA", str(e), retryable=False
        ) from e
    except AnaliseNaoConcluidaError as e:
        raise erro(status.HTTP_409_CONFLICT, "ANALISE_NAO_CONCLUIDA", str(e), retryable=True) from e
    except AnaliseFalhouError as e:
        raise erro(status.HTTP_409_CONFLICT, "ANALISE_FALHOU", str(e), retryable=False) from e
    except AnaliseSemServicosError as e:
        raise erro(status.HTTP_409_CONFLICT, "ANALISE_SEM_SERVICOS", str(e), retryable=False) from e
    return ServiceCouplingsResponse.de(visao)


async def _run_legacy_analysis_pipeline(
    user_id: UUID,
    report_id: UUID,
    job_id: UUID,
    sessionmaker,
) -> None:
    logger.info(
        "legacy_pipeline_started job_id=%s",
        job_id,
    )

    async with sessionmaker() as session:
        job_repo = PgAnalysisJobRepository(session)
        job = await job_repo.get_by_id(job_id, user_id)

        if job is None:
            logger.warning(
                "legacy_pipeline_job_not_found job_id=%s",
                job_id,
            )
            return

        job.mark_processing()
        await job_repo.update(job)

        try:
            ctx = await _build_ctx(
                user_id,
                report_id,
                session,
            )
            legacy_payload = serialize_as_legacy(ctx)

            orchestrator = _make_orchestrator()
            v2_result = await orchestrator.generate_from_legacy(
                legacy_payload,
            )

            if not isinstance(v2_result, AnalysisV2Response):
                raw_result = v2_result.model_dump() if hasattr(v2_result, "model_dump") else {}
                v2_result = AnalysisV2Response.resilient_parse(
                    raw_result,
                )

            llm_output = (
                f"Viability score: "
                f"{v2_result.viability_score}/1000. "
                f"Recommendation: "
                f"{v2_result.recommendation}. "
                f"Provider: "
                f"{v2_result.recommended_provider}. "
                f"Savings: "
                f"${v2_result.savings_summary.total_monthly_savings_usd:.2f}/mo."
            )

            job.mark_completed(
                output=llm_output,
                structured=v2_result.model_dump(),
                llm_input=legacy_payload,
            )
            await job_repo.update(job)
            await session.commit()

            logger.info(
                "legacy_pipeline_completed job_id=%s",
                job_id,
            )

        except Exception:
            await session.rollback()
            logger.exception(
                "legacy_pipeline_failed job_id=%s",
                job_id,
            )
            job.mark_failed("unexpected pipeline error")
            await job_repo.update(job)
            await session.commit()


async def _build_ctx(
    user_id: UUID,
    report_id: UUID,
    session,
) -> MigrationContext:
    try:
        return await construtor_de_contexto(session)(user_id, report_id)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e


@router.post(
    "/legacy/start/{report_id}",
    response_model=AnalysisJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start analysis using legacy workload JSON format sent to LLM",
)
async def legacy_start_analysis(
    report_id: UUID,
    user: CurrentUser,
    session: DbSession,
    background: BackgroundTasks,
    external_id: UUID | None = None,
) -> AnalysisJobResponse:
    job = AnalysisJob(
        job_id=uuid4(),
        user_id=UUID(user.sub),
        report_id=report_id,
        external_id=external_id,
    )

    repo = PgAnalysisJobRepository(session)
    await repo.create(job)
    await session.commit()

    sm = get_sessionmaker()
    background.add_task(
        _run_legacy_analysis_pipeline,
        UUID(user.sub),
        report_id,
        job.job_id,
        sm,
    )

    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        created_at=job.created_at,
    )


@router.get(
    "/legacy/{job_id}/agents-payload",
    summary=("Return the legacy workload JSON sent to the LLM (inspection endpoint)"),
)
async def get_legacy_agents_payload(
    job_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    ctx = await _build_ctx(
        UUID(user.sub),
        job_id,
        session,
    )
    return serialize_as_legacy(ctx)
