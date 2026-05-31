from decimal import Decimal
from typing import Protocol
from uuid import UUID


class IBillingDashboardRepository(Protocol):
    """Aggregations for billing dashboard metrics."""

    async def sum_by_provider(
        self,
        user_id: UUID,
        from_period: str | None,
        to_period: str | None,
    ) -> list[tuple[str, Decimal]]: ...

    async def sum_by_service(
        self,
        user_id: UUID,
        from_period: str | None,
        to_period: str | None,
        *,
        limit: int | None = None,
    ) -> list[tuple[str, Decimal]]: ...

    async def sum_by_period(
        self,
        user_id: UUID,
        from_period: str | None,
        to_period: str | None,
    ) -> list[tuple[str, Decimal]]: ...
