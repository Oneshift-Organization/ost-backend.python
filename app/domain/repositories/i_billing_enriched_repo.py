from typing import Protocol
from uuid import UUID

from app.domain.entities.billing_enriched import BillingEnriched


class IBillingEnrichedRepository(Protocol):
    async def bulk_insert(self, items: list[BillingEnriched]) -> int: ...

    async def delete_by_report(self, report_id: UUID, user_id: UUID) -> int: ...

    async def list_by_report(
        self,
        report_id: UUID,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BillingEnriched]:
        ...
