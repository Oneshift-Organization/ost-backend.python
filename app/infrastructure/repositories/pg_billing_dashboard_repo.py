from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.billing_items import BillingItemModel


class PgBillingDashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sum_by_provider(
        self,
        user_id: UUID,
        from_period: str | None,
        to_period: str | None,
    ) -> list[tuple[str, Decimal]]:
        stmt = (
            select(
                BillingItemModel.provider,
                func.sum(BillingItemModel.cost).label("total"),
            )
            .where(BillingItemModel.user_id == user_id)
            .group_by(BillingItemModel.provider)
            .order_by(func.sum(BillingItemModel.cost).desc())
        )
        stmt = _apply_period_filter(stmt, from_period, to_period)
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def sum_by_service(
        self,
        user_id: UUID,
        from_period: str | None,
        to_period: str | None,
        *,
        limit: int | None = None,
    ) -> list[tuple[str, Decimal]]:
        stmt = (
            select(
                BillingItemModel.service,
                func.sum(BillingItemModel.cost).label("total"),
            )
            .where(BillingItemModel.user_id == user_id)
            .group_by(BillingItemModel.service)
            .order_by(func.sum(BillingItemModel.cost).desc())
        )
        stmt = _apply_period_filter(stmt, from_period, to_period)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def sum_by_period(
        self,
        user_id: UUID,
        from_period: str | None,
        to_period: str | None,
    ) -> list[tuple[str, Decimal]]:
        stmt = (
            select(
                BillingItemModel.billing_period,
                func.sum(BillingItemModel.cost).label("total"),
            )
            .where(BillingItemModel.user_id == user_id)
            .group_by(BillingItemModel.billing_period)
            .order_by(BillingItemModel.billing_period)
        )
        stmt = _apply_period_filter(stmt, from_period, to_period)
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]


def _apply_period_filter(stmt, from_period: str | None, to_period: str | None):
    if from_period:
        stmt = stmt.where(BillingItemModel.billing_period >= from_period)
    if to_period:
        stmt = stmt.where(BillingItemModel.billing_period <= to_period)
    return stmt
