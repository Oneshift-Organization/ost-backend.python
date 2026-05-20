from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.billing_item import BillingItem
from app.domain.repositories.i_billing_item_repo import IBillingItemRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository


@dataclass(frozen=True)
class ListBillingItemsInput:
    user_id: UUID
    report_id: UUID
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True)
class ListBillingItemsOutput:
    items: list[BillingItem]
    total: int
    limit: int
    offset: int


class ListBillingItemsUseCase:
    """Read normalized line items for a billing report owned by the caller."""

    def __init__(
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingItemRepository,
    ) -> None:
        self._reports = report_repo
        self._items = item_repo

    async def execute(self, payload: ListBillingItemsInput) -> ListBillingItemsOutput:
        # Authorize: 404 if the report doesn't exist for THIS user.
        report = await self._reports.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"billing job {payload.report_id} not found")

        total = await self._items.count_by_report(payload.report_id, payload.user_id)
        items = await self._items.list_by_report(
            payload.report_id,
            payload.user_id,
            limit=payload.limit,
            offset=payload.offset,
        )
        return ListBillingItemsOutput(
            items=items, total=total, limit=payload.limit, offset=payload.offset
        )
