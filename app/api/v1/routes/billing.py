import contextlib
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    UploadFile,
    status,
)

from app.api.deps import (
    CurrentUser,
    DbSession,
    GetBillingJobStatusUC,
    ListBillingItemsUC,
    ListBillingReportsUC,
    UploadBillingFileUC,
)
from app.api.v1.routes.billing_checks import verificar_arquivo
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.pg_sku_classification_rule_repo import (
    PgSkuClassificationRuleRepository,
)
from app.infrastructure.repositories.pg_unmapped_billing_repo import (
    PgUnmappedBillingRepository,
)
from app.schemas.billing import (
    BillingFileUploadResponse,
    BillingItemResponse,
    BillingItemsPage,
    BillingJobStatusResponse,
    BillingReportSummary,
)
from app.schemas.diagnostico_de_entrada import BillingFileCheckResponse
from app.use_cases.analysis.build_billing_enriched import BuildBillingEnrichedUseCase
from app.use_cases.billing.get_billing_job_status import GetBillingJobStatusInput
from app.use_cases.billing.ingest_billing_file import (
    IngestBillingFileInput,
    IngestBillingFileUseCase,
)
from app.use_cases.billing.list_billing_items import ListBillingItemsInput
from app.use_cases.billing.list_billing_reports import ListBillingReportsInput
from app.use_cases.billing.upload_billing_file import UploadBillingFileInput

router = APIRouter(prefix="/billing-files")


def _verificar_sem_falhar(caminho: Path) -> BillingFileCheckResponse | None:
    """O formato não reconhecido segue o caminho de sempre (falha na leitura, com o motivo)."""
    try:
        return verificar_arquivo(caminho)
    except Exception:
        _log.warning("verificacao_do_arquivo_falhou", arquivo=caminho.name)
        return None


_log = get_logger(__name__)

# `.parquet`: um dos três formatos oficiais do CUR 2.0. `.jsonl`: NDJSON com
# a outra extensão usual (o `bq extract` e ferramentas de ETL usam as duas).
_ALLOWED_EXT = {
    ".csv",
    ".tsv",
    ".txt",
    ".gz",
    ".zip",
    ".json",
    ".ndjson",
    ".jsonl",
    ".xlsx",
    ".xlsm",
    ".parquet",
}

_MAX_REPORTS_PER_PAGE = 500
_MAX_ITEMS_PER_PAGE = 1000


@router.post(
    "",
    response_model=BillingFileUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_billing_file(
    user: CurrentUser,
    upload_uc: UploadBillingFileUC,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(...)],
    external_id: UUID | None = None,
) -> BillingFileUploadResponse:
    settings = get_settings()
    if file.filename is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "filename required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported file extension {suffix}. allowed: {sorted(_ALLOWED_EXT)}",
        )

    contents = await file.read()
    if len(contents) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file exceeds {settings.max_upload_size_mb} MB",
        )

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_name = tmp.name

    # O que a verificação recusa (Cost Explorer por serviço, relatório de uso da OCI,
    # arquivo que não é fatura) não vira análise: recusa já, com o caminho certo, em
    # vez de falhar depois em segundo plano (PLANO_CENARIOS_DE_ENTRADA)
    verificacao = _verificar_sem_falhar(Path(tmp_name))
    if verificacao is not None and not verificacao.aceita:
        Path(tmp_name).unlink(missing_ok=True)  # noqa: ASYNC240 - arquivo temporário pequeno
        problema = verificacao.problemas[0]
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "codigo": "FORMATO_NAO_SUPORTADO",
                "mensagem": f"{problema.titulo}. {problema.texto}",
                "formato": verificacao.formato,
                "retryable": False,
            },
        )

    try:
        user_uuid = UUID(user.sub)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user id in token") from e

    report = await upload_uc.execute(
        UploadBillingFileInput(
            user_id=user_uuid,
            file_path=Path(tmp_name),
            source_filename=file.filename,
            external_id=external_id,
        )
    )

    background.add_task(
        _run_ingest_isolated,
        user_uuid,
        report.id,
        Path(tmp_name),
    )

    return BillingFileUploadResponse(
        job_id=report.id,
        report_id=report.id,
        external_id=report.external_id,
        status=report.status,
        source_filename=report.source_filename or file.filename,
        created_at=report.created_at,
    )


@router.get("", response_model=list[BillingReportSummary])
async def list_billing_reports(
    user: CurrentUser,
    uc: ListBillingReportsUC,
    limit: int = 50,
) -> list[BillingReportSummary]:
    """List all upload jobs owned by the caller, most recent first."""
    user_uuid = _resolve_user_uuid(user.sub)
    if limit < 1 or limit > _MAX_REPORTS_PER_PAGE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"limit must be 1..{_MAX_REPORTS_PER_PAGE}",
        )
    reports = await uc.execute(ListBillingReportsInput(user_id=user_uuid, limit=limit))
    return [BillingReportSummary.from_entity(r) for r in reports]


@router.get("/by-external-id/{external_id}", response_model=BillingJobStatusResponse)
async def get_billing_by_external_id(
    external_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> BillingJobStatusResponse:
    from app.infrastructure.repositories.pg_billing_report_repo import (
        PgBillingReportRepository,
    )

    repo = PgBillingReportRepository(session)
    report = await repo.get_by_external_id(external_id, UUID(user.sub))
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "billing report not found")
    return BillingJobStatusResponse(
        job_id=report.id,
        report_id=report.id,
        external_id=report.external_id,
        status=report.status,
        provider=report.provider,
        schema_version=report.schema_version,
        row_count=report.row_count,
        error_message=report.error_message,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@router.get("/{job_id}", response_model=BillingJobStatusResponse)
async def get_billing_job_status(
    job_id: UUID,
    user: CurrentUser,
    uc: GetBillingJobStatusUC,
) -> BillingJobStatusResponse:
    user_uuid = _resolve_user_uuid(user.sub)
    try:
        report = await uc.execute(GetBillingJobStatusInput(user_id=user_uuid, report_id=job_id))
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    return BillingJobStatusResponse(
        job_id=report.id,
        report_id=report.id,
        external_id=report.external_id,
        status=report.status,
        provider=report.provider,
        schema_version=report.schema_version,
        row_count=report.row_count,
        error_message=report.error_message,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@router.get("/{job_id}/items", response_model=BillingItemsPage)
async def list_billing_items(
    job_id: UUID,
    user: CurrentUser,
    uc: ListBillingItemsUC,
    limit: int = 100,
    offset: int = 0,
) -> BillingItemsPage:
    """Return the normalized line items of a billing job.

    Ordering: cost DESC, service ASC. Default page size 100, max 1000.
    """
    user_uuid = _resolve_user_uuid(user.sub)
    if limit < 1 or limit > _MAX_ITEMS_PER_PAGE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"limit must be 1..{_MAX_ITEMS_PER_PAGE}",
        )
    if offset < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "offset must be >= 0")

    try:
        result = await uc.execute(
            ListBillingItemsInput(user_id=user_uuid, report_id=job_id, limit=limit, offset=offset)
        )
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    return BillingItemsPage(
        report_id=job_id,
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        items=[BillingItemResponse.from_entity(i) for i in result.items],
    )


def _resolve_user_uuid(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user id in token") from e


async def _run_ingest_isolated(user_id: UUID, report_id: UUID, file_path: Path) -> None:
    """Background task uses its OWN session (request session is already closed).

    Failure handling: when the ingest pipeline raises, the use case's
    `mark_failed` write lives in the same session and would be lost on
    rollback. So we rollback the dirty session, then persist the FAILED
    status in a brand-new transaction.
    """
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            uc = IngestBillingFileUseCase(
                PgBillingReportRepository(session),
                PgBillingItemRepository(session),
                BuildBillingEnrichedUseCase(
                    PgBillingReportRepository(session),
                    PgBillingItemRepository(session),
                    PgBillingEnrichedRepository(session),
                    PgCloudSkuRepository(session),
                    PgSkuClassificationRuleRepository(session),
                    PgUnmappedBillingRepository(session),
                ),
            )
            try:
                await uc.execute(
                    IngestBillingFileInput(
                        user_id=user_id, report_id=report_id, file_path=file_path
                    )
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                _log.exception("background_ingest_failed", report_id=str(report_id))
                await _persist_failure(sessionmaker, user_id, report_id, exc)
    finally:
        with contextlib.suppress(OSError):
            file_path.unlink(missing_ok=True)  # noqa: ASYNC240 - cleanup of small tmp file


async def _persist_failure(
    sessionmaker, user_id: UUID, report_id: UUID, exc: BaseException
) -> None:
    """Mark a job as FAILED in a clean session. Best-effort: never re-raises."""
    try:
        async with sessionmaker() as fail_session:
            repo = PgBillingReportRepository(fail_session)
            report = await repo.get_by_id(report_id, user_id)
            if report is None:
                return
            report.mark_failed(f"{type(exc).__name__}: {exc}")
            await repo.update(report)
            await fail_session.commit()
    except Exception:
        _log.exception("failed_to_persist_failure_status", report_id=str(report_id))
