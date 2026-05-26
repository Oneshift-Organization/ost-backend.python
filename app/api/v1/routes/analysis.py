import logging
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.domain.analysis import MigrationContext
from app.domain.entities.analysis_job import AnalysisJob
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.llm.gemini_client import GeminiLLMClient
from app.infrastructure.llm.mock_client import MockLLMClient
from app.infrastructure.llm.openrouter_client import OpenRouterLLMClient
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.schemas.analysis import (
    AnalysisJobResponse,
    AnalysisRequest,
    EquivalentServiceItem,
    FinancialEstimateItem,
    MigrationContextResponse,
    ServiceCategoryGroupItem,
    ServiceSummaryItem,
    SourceUsageItem,
    TechnicalMatchItem,
    WorkloadSummaryItem,
)
from app.schemas.analysis_ui import AnalysisUIResponse
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)
from app.use_cases.analysis.run_analysis_pipeline import RunAnalysisPipelineUseCase

router = APIRouter(prefix="/analyses")


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
                        ) if e.technical_match else None,
                        financials=FinancialEstimateItem(
                            unit_price=e.financials.unit_price,
                            estimated_monthly_cost=e.financials.estimated_monthly_cost,
                            cost_delta=e.financials.cost_delta,
                            pricing_confidence=e.financials.pricing_confidence,
                        ) if e.financials else None,
                    )
                    for e in w.equivalents
                ],
            )
            for w in ctx.workloads
        ],
    )


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
    )
    try:
        ctx = await uc.execute(
            BuildMigrationContextInput(user_id=UUID(user.sub), report_id=body.report_id)
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
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
    )
    try:
        ctx = await uc.execute(
            BuildMigrationContextInput(user_id=UUID(user.sub), report_id=job_id)
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _build_response(ctx)


async def _run_analysis_pipeline(
    user_id: UUID,
    report_id: UUID,
    job_id: UUID,
    sessionmaker,
) -> None:
    """Executa a pipeline de análise em background com sessão isolada."""
    logger.info("pipeline_started job_id=%s", job_id)
    async with sessionmaker() as session:
        job_repo = PgAnalysisJobRepository(session)
        job = await job_repo.get_by_id(job_id, user_id)
        if job is None:
            logger.warning("pipeline_job_not_found job_id=%s", job_id)
            return

        logger.info("pipeline_job_loaded job_id=%s status=%s", job_id, job.status)

        settings = get_settings()
        llm_client: MockLLMClient | GeminiLLMClient | OpenRouterLLMClient
        match settings.llm_provider:
            case "openrouter":
                llm_client = OpenRouterLLMClient(
                    api_key=settings.openrouter_api_key,
                    model=settings.openrouter_model,
                )
            case "gemini":
                llm_client = GeminiLLMClient(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                )
            case _:
                llm_client = MockLLMClient()
        logger.info("llm_provider_selected provider=%s", settings.llm_provider)

        orchestrator = LLMOrchestrator(llm_client=llm_client)

        uc = RunAnalysisPipelineUseCase(
            analysis_job_repo=job_repo,
            report_repo=PgBillingReportRepository(session),
            item_repo=PgBillingEnrichedRepository(session),
            metadata_repo=PGServiceMetadataRepository(session),
            comparison_repo=PGServiceComparisonRepository(session),
            sku_repo=PgCloudSkuRepository(session),
            llm_orchestrator=orchestrator,
        )

        try:
            await uc.execute(job)
            await session.commit()
            logger.info("pipeline_completed job_id=%s", job_id)
        except Exception:
            await session.rollback()
            logger.exception("pipeline_failed job_id=%s", job_id)
            job.mark_failed("unexpected pipeline error")
            await job_repo.update(job)
            await session.commit()
            logger.info("pipeline_failed_status_saved job_id=%s", job_id)


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
        _run_analysis_pipeline,
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
    job = await repo.get_by_external_id(external_id, UUID(user.sub))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis job not found")
    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        llm_output=job.llm_output,
        error=job.error,
        result=AnalysisUIResponse(**job.ai_structured_output) if job.ai_structured_output else None,
        created_at=job.created_at,
    )


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis job not found")
    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        llm_output=job.llm_output,
        error=job.error,
        result=AnalysisUIResponse(**job.ai_structured_output) if job.ai_structured_output else None,
        created_at=job.created_at,
    )
