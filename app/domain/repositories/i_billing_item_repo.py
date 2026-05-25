from typing import Protocol
from uuid import UUID

from app.domain.entities.billing_item import BillingItem


class IBillingItemRepository(Protocol):
    """Repository interface for normalized billing line items."""

    async def bulk_insert(self, items: list[BillingItem]) -> int: ...

    async def delete_by_report(self, report_id: UUID, user_id: UUID) -> int:
        """Used for idempotent re-ingestion."""
        ...

    async def count_by_report(self, report_id: UUID, user_id: UUID) -> int: ...

    async def list_by_report(
        self,
        report_id: UUID,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BillingItem]:
        """Return items for a report. MUST filter by user_id for isolation."""
        ...
