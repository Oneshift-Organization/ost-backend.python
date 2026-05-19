from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.billing_report import BillingReport
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository


@dataclass(frozen=True)
class GetBillingJobStatusInput:
    user_id: UUID
    report_id: UUID


class GetBillingJobStatusUseCase:
    def __init__(self, report_repo: IBillingReportRepository) -> None:
        self._repo = report_repo

    async def execute(self, payload: GetBillingJobStatusInput) -> BillingReport:
        report = await self._repo.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"billing job {payload.report_id} not found")
        return report
