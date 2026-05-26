from typing import Protocol

from app.domain.entities.unmapped_billing_line import UnmappedBillingLine


class IUnmappedBillingRepository(Protocol):
    async def bulk_insert(self, items: list[UnmappedBillingLine]) -> int: ...
