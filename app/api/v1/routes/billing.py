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

from app.api.deps import CurrentUser, GetBillingJobStatusUC, UploadBillingFileUC
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.schemas.billing import BillingFileUploadResponse, BillingJobStatusResponse
from app.use_cases.billing.get_billing_job_status import GetBillingJobStatusInput
from app.use_cases.billing.ingest_billing_file import (
    IngestBillingFileInput,
    IngestBillingFileUseCase,
)
from app.use_cases.billing.upload_billing_file import UploadBillingFileInput

router = APIRouter(prefix="/billing-files")
_log = get_logger(__name__)

_ALLOWED_EXT = {".csv", ".gz", ".zip", ".json", ".ndjson", ".xlsx", ".xlsm"}


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

    try:
        user_uuid = UUID(user.sub)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user id in token") from e

    report = await upload_uc.execute(
        UploadBillingFileInput(
            user_id=user_uuid,
            file_path=Path(tmp_name),
            source_filename=file.filename,
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
        status=report.status,
        source_filename=report.source_filename or file.filename,
        created_at=report.created_at,
    )


@router.get("/{job_id}", response_model=BillingJobStatusResponse)
async def get_billing_job_status(
    job_id: UUID,
    user: CurrentUser,
    uc: GetBillingJobStatusUC,
) -> BillingJobStatusResponse:
    try:
        user_uuid = UUID(user.sub)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user id in token") from e

    try:
        report = await uc.execute(GetBillingJobStatusInput(user_id=user_uuid, report_id=job_id))
    except NotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    return BillingJobStatusResponse(
        job_id=report.id,
        status=report.status,
        provider=report.provider,
        schema_version=report.schema_version,
        row_count=report.row_count,
        error_message=report.error_message,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


async def _run_ingest_isolated(user_id: UUID, report_id: UUID, file_path: Path) -> None:
    """Background task uses its OWN session (request session is already closed)."""
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            try:
                uc = IngestBillingFileUseCase(
                    PgBillingReportRepository(session),
                    PgBillingItemRepository(session),
                )
                await uc.execute(
                    IngestBillingFileInput(
                        user_id=user_id, report_id=report_id, file_path=file_path
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()
                _log.exception("background_ingest_failed", report_id=str(report_id))
    finally:
        with contextlib.suppress(OSError):
            file_path.unlink(missing_ok=True)  # noqa: ASYNC240 - cleanup of small tmp file
