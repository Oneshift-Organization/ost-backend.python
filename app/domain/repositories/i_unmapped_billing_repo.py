from typing import Protocol
from uuid import UUID

from app.domain.entities.unmapped_billing_line import UnmappedBillingLine


class IUnmappedBillingRepository(Protocol):
    async def bulk_insert(self, items: list[UnmappedBillingLine]) -> int: ...

    async def delete_by_report(self, report_id: UUID, user_id: UUID) -> int: ...
