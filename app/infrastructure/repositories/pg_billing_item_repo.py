from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.billing_item import BillingItem
from app.domain.value_objects.cloud_provider import CloudProvider
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

    async def list_by_report(
        self,
        report_id: UUID,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BillingItem]:
        stmt = (
            select(BillingItemModel)
            .where(
                BillingItemModel.report_id == report_id,
                BillingItemModel.user_id == user_id,
            )
            .order_by(BillingItemModel.cost.desc(), BillingItemModel.service)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    @staticmethod
    def _to_entity(model: BillingItemModel) -> BillingItem:
        return BillingItem(
            id=model.id,
            report_id=model.report_id,
            user_id=model.user_id,
            provider=CloudProvider(model.provider),
            account_id=model.account_id,
            service=model.service,
            service_raw=model.service_raw,
            resource_id=model.resource_id,
            region=model.region,
            cost=model.cost,
            cost_discounted=model.cost_discounted,
            currency=model.currency,
            usage_amount=model.usage_amount,
            usage_unit=model.usage_unit,
            usage_start=model.usage_start,
            usage_end=model.usage_end,
            billing_period=model.billing_period,
            tags=model.tags or {},
            is_correction=model.is_correction,
            is_estimate=model.is_estimate,
            line_hash=model.line_hash,
        )

    @staticmethod
    def _to_row(item: BillingItem) -> dict:
        return {
            "id": item.id,
            "report_id": item.report_id,
            "user_id": item.user_id,
            "provider": item.provider.value,
            "account_id": item.account_id,
            "service": item.service,
            "service_raw": item.service_raw,
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
