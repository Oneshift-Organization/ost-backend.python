"""Nome que o usuário dá ao billing (M9). A tela mostra o alias no lugar do arquivo."""

from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.billing_report import BillingReport
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository


@dataclass(frozen=True)
class RenameBillingReportInput:
    user_id: UUID
    report_id: UUID
    # None ou vazio tira o alias
    alias: str | None


class RenameBillingReportUseCase:
    def __init__(self, report_repo: IBillingReportRepository) -> None:
        self._repo = report_repo

    async def execute(self, payload: RenameBillingReportInput) -> BillingReport:
        report = await self._repo.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"billing job {payload.report_id} not found")
        report.renomear(payload.alias)
        await self._repo.rename(report.id, payload.user_id, report.alias)
        return report
