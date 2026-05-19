from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.billing_item import BillingItem
from app.infrastructure.database.models.billing_items import BillingItemModel

_BATCH_SIZE = 1000


class PgBillingItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, items: list[BillingItem]) -> int:
        if not items:
            return 0
        rows = [self._to_row(it) for it in items]

        total = 0
        for i in range(0, len(rows), _BATCH_SIZE):
            chunk = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(BillingItemModel).values(chunk)
            # Idempotency: same line_hash within the same report = no-op.
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            result = await self._session.execute(stmt)
            total += result.rowcount or len(chunk)
        await self._session.flush()
        return total

    async def delete_by_report(self, report_id: UUID, user_id: UUID) -> int:
        stmt = delete(BillingItemModel).where(
            BillingItemModel.report_id == report_id,
            BillingItemModel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount or 0

    async def count_by_report(self, report_id: UUID, user_id: UUID) -> int:
        stmt = select(func.count(BillingItemModel.id)).where(
            BillingItemModel.report_id == report_id,
            BillingItemModel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    @staticmethod
    def _to_row(item: BillingItem) -> dict:
        return {
            "id": item.id,
            "report_id": item.report_id,
            "user_id": item.user_id,
            "provider": item.provider.value,
            "account_id": item.account_id,
            "service": item.service,
            "resource_id": item.resource_id,
            "region": item.region,
            "cost": item.cost,
            "cost_discounted": item.cost_discounted,
            "currency": item.currency,
            "usage_amount": item.usage_amount,
            "usage_unit": item.usage_unit,
            "usage_start": item.usage_start,
            "usage_end": item.usage_end,
            "billing_period": item.billing_period,
            "tags": item.tags,
            "is_correction": item.is_correction,
            "is_estimate": item.is_estimate,
            "line_hash": item.line_hash,
        }
