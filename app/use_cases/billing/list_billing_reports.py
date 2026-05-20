from dataclasses import dataclass
from uuid import UUID

from app.domain.entities.billing_report import BillingReport
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository


@dataclass(frozen=True)
class ListBillingReportsInput:
    user_id: UUID
    limit: int = 50


class ListBillingReportsUseCase:
    def __init__(self, report_repo: IBillingReportRepository) -> None:
        self._repo = report_repo

    async def execute(self, payload: ListBillingReportsInput) -> list[BillingReport]:
        return await self._repo.list_by_user(payload.user_id, limit=payload.limit)
