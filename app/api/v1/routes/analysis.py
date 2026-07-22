import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.domain.analysis import MigrationContext
from app.domain.entities.analysis_job import AnalysisJob
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.llm.agno_client import AgnoLLMClient
from app.infrastructure.llm.mock_client import MockLLMClient
from app.infrastructure.llm.orchestrator import LLMOrchestrator, log_llm_input_payload
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
    AnalysisJobSummary,
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
from app.schemas.analysis_v2 import AnalysisV2Response, LegacyAnalysisRequest
from app.use_cases.analysis.run_analysis_pipeline import (
    build_pipeline_llm_input_payload,
    serialize_as_legacy,
)
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)
from app.use_cases.analysis.financial_overlay import apply_financial_overlay
from app.use_cases.analysis.run_analysis_pipeline import RunAnalysisPipelineUseCase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analyses")

_MAX_ANALYSES_PER_PAGE = 100


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


@router.get("", response_model=list[AnalysisJobSummary])
async def list_analysis_jobs(
    user: CurrentUser,
    session: DbSession,
    limit: int = 50,
) -> list[AnalysisJobSummary]:
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
        )
        .order_by(AnalysisJobModel.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)

    summaries: list[AnalysisJobSummary] = []
    for job, report in result.all():
        structured = job.ai_structured_output if isinstance(job.ai_structured_output, dict) else {}
        summaries.append(
            AnalysisJobSummary(
                job_id=job.job_id,
                report_id=job.report_id,
                external_id=job.external_id,
                status=job.status,
                provider=report.provider,
                source_filename=report.source_filename,
                row_count=report.row_count,
                recommended_provider=structured.get("recommended_provider"),
                recommendation=structured.get("recommendation"),
                viability_score=structured.get("viability_score"),
                analysis_confidence=structured.get("analysis_confidence"),
                error=job.error,
                created_at=job.created_at,
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


@router.get(
    "/{report_id}/llm-input-preview",
    status_code=status.HTTP_200_OK,
)
async def get_llm_input_preview(
    report_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    # Prefer the persisted payload — exact record of what was sent to LLM.
    # Falls back to recomputing when the job hasn't run yet or pre-migration rows.
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

    # Fallback: recompute from current billing_enriched data
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
        llm_client = _make_llm_client()
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
        result=AnalysisV2Response.resilient_parse(job.ai_structured_output) if job.ai_structured_output else None,
        llm_input_payload=job.llm_input_payload,
        created_at=job.created_at,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_llm_client() -> MockLLMClient | AgnoLLMClient:
    settings = get_settings()
    match settings.llm_provider:
        case "openrouter":
            return AgnoLLMClient(
                "openrouter", settings.openrouter_api_key, settings.openrouter_model
            )
        case "gemini":
            return AgnoLLMClient("gemini", settings.gemini_api_key, settings.gemini_model)
        case "anthropic":
            return AgnoLLMClient("anthropic", settings.anthropic_api_key, settings.anthropic_model)
        case _:
            return MockLLMClient()


# ── Legacy direct endpoint ────────────────────────────────────────────────────

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
    """Accepts a pre-computed workload payload (no CSV upload required).

    Returns ``AnalysisV2Response`` synchronously. Use this when billing data
    and cross-cloud equivalents are already computed by the client.
    """
    billing_data = {
        "source_provider": body.source_provider,
        "billing_period": body.billing_period,
        "currency": body.currency,
        "total_spend": body.total_spend,
        "company_context": body.company_context.model_dump() if body.company_context else None,
        "workloads": [w.model_dump() for w in body.workloads],
    }

    orchestrator = LLMOrchestrator(llm_client=_make_llm_client())
    logger.info(
        "direct_analysis_started provider=%s period=%s",
        body.source_provider, body.billing_period,
    )

    try:
        result = await orchestrator.generate_from_legacy(billing_data)
    except Exception as exc:
        logger.exception("direct_analysis_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM analysis failed: {exc}",
        ) from exc

    result = apply_financial_overlay(result, billing_data)

    logger.info("direct_analysis_completed viability=%s", result.viability_score)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis job not found")
    return AnalysisJobResponse(
        job_id=job.job_id,
        report_id=job.report_id,
        external_id=job.external_id,
        status=job.status,
        llm_output=job.llm_output,
        error=job.error,
        result=AnalysisV2Response.resilient_parse(job.ai_structured_output) if job.ai_structured_output else None,
        llm_input_payload=job.llm_input_payload,
        created_at=job.created_at,
    )


# ── Legacy pipeline ───────────────────────────────────────────────────────────
# Same data as /start/{report_id} but serialized in the legacy workload format
# before being sent to the LLM. Existing endpoints above are NOT modified.

async def _run_legacy_analysis_pipeline(
    user_id: UUID,
    report_id: UUID,
    job_id: UUID,
    sessionmaker,
) -> None:
    """Legacy-format pipeline: builds context via the normal pipeline, then
    serializes it as workloads[] JSON before sending to the LLM."""
    logger.info("legacy_pipeline_started job_id=%s", job_id)
    async with sessionmaker() as session:
        job_repo = PgAnalysisJobRepository(session)
        job = await job_repo.get_by_id(job_id, user_id)
        if job is None:
            logger.warning("legacy_pipeline_job_not_found job_id=%s", job_id)
            return

        job.mark_processing()
        await job_repo.update(job)

        try:
            ctx = await _build_ctx(user_id, report_id, session)
            legacy_payload = serialize_as_legacy(ctx)
            orchestrator = LLMOrchestrator(llm_client=_make_llm_client())
            v2_result = await orchestrator.generate_from_legacy(legacy_payload)

            if not isinstance(v2_result, AnalysisV2Response):
                v2_result = AnalysisV2Response.resilient_parse(
                    v2_result.model_dump() if hasattr(v2_result, "model_dump") else {}
                )

            v2_result = apply_financial_overlay(v2_result, legacy_payload)

            llm_output = (
                f"Viability score: {v2_result.viability_score}/100. "
                f"Recommendation: {v2_result.recommendation}. "
                f"Provider: {v2_result.recommended_provider}. "
                f"Savings: ${v2_result.savings_summary.total_monthly_savings_usd:.2f}/mo."
            )
            job.mark_completed(
                output=llm_output,
                structured=v2_result.model_dump(),
                llm_input=legacy_payload,
            )
            await job_repo.update(job)
            await session.commit()
            logger.info("legacy_pipeline_completed job_id=%s", job_id)

        except Exception:
            await session.rollback()
            logger.exception("legacy_pipeline_failed job_id=%s", job_id)
            job.mark_failed("unexpected pipeline error")
            await job_repo.update(job)
            await session.commit()


async def _build_ctx(user_id: UUID, report_id: UUID, session) -> "MigrationContext":
    """Build MigrationContext — shared by both legacy endpoints."""
    from app.core.exceptions import NotFoundError
    uc = BuildMigrationContextUseCase(
        PgBillingReportRepository(session),
        PgBillingEnrichedRepository(session),
        PGServiceMetadataRepository(session),
        PGServiceComparisonRepository(session),
        sku_repo=PgCloudSkuRepository(session),
    )
    try:
        return await uc.execute(
            BuildMigrationContextInput(user_id=user_id, report_id=report_id)
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


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
    """Same pipeline as /start/{report_id} but sends the legacy workloads[]
    format to the LLM. Returns a job_id; poll GET /{job_id}/result for the
    AnalysisV2Response.
    """
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
        _run_legacy_analysis_pipeline, UUID(user.sub), report_id, job.job_id, sm,
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
    summary="Return the legacy workload JSON sent to the LLM (inspection endpoint)",
)
async def get_legacy_agents_payload(
    job_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    """Returns the legacy workload[] serialization of the migration context.

    Mirrors GET /{job_id}/agents-payload but in the legacy format so callers
    can inspect exactly what the LLM receives in the legacy pipeline.
    report_id == job_id (same as the existing agents-payload convention).
    """
    ctx = await _build_ctx(UUID(user.sub), job_id, session)
    return serialize_as_legacy(ctx)
