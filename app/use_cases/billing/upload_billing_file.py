from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository


@dataclass(frozen=True)
class UploadBillingFileInput:
    user_id: UUID
    file_path: Path
    source_filename: str
    external_id: UUID | None = None
    # Nome dado na importação (M9); None ou vazio fica com o nome do arquivo
    alias: str | None = None


class UploadBillingFileUseCase:
    """Persists the parent BillingReport row.

    Heavy parsing/normalization happens in `IngestBillingFileUseCase`, dispatched
    asynchronously by the HTTP route.
    """

    def __init__(self, report_repo: IBillingReportRepository) -> None:
        self._repo = report_repo

    async def execute(self, payload: UploadBillingFileInput) -> BillingReport:
        report = BillingReport(
            user_id=payload.user_id,
            source_filename=payload.source_filename,
            status=JobStatus.QUEUED,
            external_id=payload.external_id,
        )
        # Valida antes de gravar: nome inválido não deixa billing pela metade
        report.renomear(payload.alias)
        return await self._repo.save(report)
