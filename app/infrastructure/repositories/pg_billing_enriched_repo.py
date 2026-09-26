from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.billing_enriched import BillingEnriched
from app.infrastructure.database.models.billing_enriched import BillingEnrichedModel

_BATCH_SIZE = 1000


class PgBillingEnrichedRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, items: list[BillingEnriched]) -> int:
        if not items:
            return 0
        rows = [self._to_row(it) for it in items]
        total = 0
        for i in range(0, len(rows), _BATCH_SIZE):
            chunk = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(BillingEnrichedModel).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            result = await self._session.execute(stmt)
            total += result.rowcount or len(chunk)  # type: ignore[attr-defined]
        await self._session.flush()
        return total

    async def delete_by_report(self, report_id: UUID, user_id: UUID) -> int:
        stmt = delete(BillingEnrichedModel).where(
            BillingEnrichedModel.report_id == report_id,
            BillingEnrichedModel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def list_by_report(
        self,
        report_id: UUID,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BillingEnriched]:
        stmt = (
            select(BillingEnrichedModel)
            .where(
                BillingEnrichedModel.report_id == report_id,
                BillingEnrichedModel.user_id == user_id,
            )
            .order_by(
                BillingEnrichedModel.cost.desc(),
                BillingEnrichedModel.service_name,
                # Desempate final: (cost, service_name) NÃO é ordem total —
                # centenas de linhas empatam (ex.: várias com custo 0 do mesmo
                # serviço) e o desempate arbitrário do Postgres mudava a ordem
                # de inserção dos pools, vazando para o payload.
                BillingEnrichedModel.id,
            )
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    @staticmethod
    def _to_entity(model: BillingEnrichedModel) -> BillingEnriched:
        return BillingEnriched(
            id=model.id,
            report_id=model.report_id,
            user_id=model.user_id,
            provider=model.provider,
            billing_period=model.billing_period,
            service_name=model.service_name,
            service_type=model.service_type,
            category=model.category,
            usage_type=model.usage_type,
            region=model.region,
            project_id=model.project_id,
            usage_amount=model.usage_amount,
            usage_unit=model.usage_unit,
            cost=model.cost,
            currency=model.currency,
            sku_id=model.sku_id,
            sku_metadata=model.sku_metadata,
            component_type=model.component_type,
            mapping_confidence=model.mapping_confidence,
            match_layer=model.match_layer,
            pricing_source=model.pricing_source,
            pricing_confidence=model.pricing_confidence,
            scope=model.scope,
            source_line_hash=model.source_line_hash,
            resource_id=model.resource_id,
            attributes=model.attributes,
            created_at=model.created_at,
        )

    @staticmethod
    def _to_row(item: BillingEnriched) -> dict[str, Any]:
        return {
            "id": item.id,
            "report_id": item.report_id,
            "user_id": item.user_id,
            "provider": item.provider,
            "billing_period": item.billing_period,
            "service_name": item.service_name,
            "service_type": item.service_type,
            "category": item.category,
            "usage_type": item.usage_type,
            "region": item.region,
            "project_id": item.project_id,
            "usage_amount": item.usage_amount,
            "usage_unit": item.usage_unit,
            "cost": item.cost,
            "currency": item.currency,
            "sku_id": item.sku_id,
            "sku_metadata": item.sku_metadata,
            "component_type": item.component_type,
            "mapping_confidence": item.mapping_confidence,
            "match_layer": item.match_layer,
            "pricing_source": item.pricing_source,
            "pricing_confidence": item.pricing_confidence,
            "scope": item.scope,
            "source_line_hash": item.source_line_hash,
            "resource_id": item.resource_id,
            "attributes": item.attributes or None,
        }
