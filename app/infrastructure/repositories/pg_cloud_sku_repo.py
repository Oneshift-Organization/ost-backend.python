from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Result, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.infrastructure.database.models.cloud_sku_catalog import CloudSkuCatalogModel


def _to_entity(model: CloudSkuCatalogModel) -> CloudSku:
    return CloudSku(
        sku_id=model.sku_id,
        provider=model.provider,
        service_name=model.service_name,
        service_type=model.service_type,
        category=model.category,
        region=model.region,
        usage_unit=model.usage_unit,
        price_per_unit=model.price_per_unit,
        currency=model.currency,
        vcpus=model.vcpus,
        ram_gb=model.ram_gb,
        attributes=model.attributes,
        effective_date=model.effective_date,
        collected_at=model.collected_at,
    )


class PgCloudSkuRepository(ICloudSkuRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, provider: str, sku_id: str) -> CloudSku | None:
        stmt = select(CloudSkuCatalogModel).where(
            CloudSkuCatalogModel.provider.ilike(provider),
            CloudSkuCatalogModel.sku_id == sku_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return _to_entity(row)

    async def find_equivalents(
        self,
        *,
        provider: str,
        service_type: str,
        min_vcpus: Decimal | None = None,
        min_ram_gb: Decimal | None = None,
        limit: int = 10,
    ) -> list[CloudSku]:
        stmt = select(CloudSkuCatalogModel).where(
            CloudSkuCatalogModel.provider.ilike(provider),
            CloudSkuCatalogModel.service_type == service_type,
        )

        if min_vcpus is not None:
            stmt = stmt.where(CloudSkuCatalogModel.vcpus >= min_vcpus)
        if min_ram_gb is not None:
            stmt = stmt.where(CloudSkuCatalogModel.ram_gb >= min_ram_gb)

        stmt = stmt.order_by(
            CloudSkuCatalogModel.price_per_unit, CloudSkuCatalogModel.vcpus
        ).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def list_by_provider(self, provider: str) -> list[CloudSku]:
        stmt = select(CloudSkuCatalogModel).where(
            CloudSkuCatalogModel.provider.ilike(provider),
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def list_providers(self) -> list[str]:
        stmt = select(CloudSkuCatalogModel.provider).distinct()
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def bulk_upsert(self, skus: list[CloudSku]) -> tuple[int, int]:
        inserted = 0
        updated = 0
        now = datetime.now(timezone.utc)

        for sku in skus:
            values = {
                "sku_id": sku.sku_id,
                "provider": sku.provider,
                "service_name": sku.service_name,
                "service_type": sku.service_type or "unknown",
                "category": sku.category or "unknown",
                "region": sku.region,
                "usage_unit": sku.usage_unit,
                "price_per_unit": sku.price_per_unit,
                "currency": sku.currency,
                "vcpus": sku.vcpus,
                "ram_gb": sku.ram_gb,
                "attributes": sku.attributes or {},
                "effective_date": sku.effective_date or now,
                "collected_at": now,
            }

            stmt = pg_insert(CloudSkuCatalogModel).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[CloudSkuCatalogModel.sku_id],
                set_={
                    "provider": stmt.excluded.provider,
                    "service_name": stmt.excluded.service_name,
                    "service_type": stmt.excluded.service_type,
                    "category": stmt.excluded.category,
                    "region": stmt.excluded.region,
                    "usage_unit": stmt.excluded.usage_unit,
                    "price_per_unit": stmt.excluded.price_per_unit,
                    "currency": stmt.excluded.currency,
                    "vcpus": stmt.excluded.vcpus,
                    "ram_gb": stmt.excluded.ram_gb,
                    "attributes": stmt.excluded.attributes,
                    "effective_date": stmt.excluded.effective_date,
                    "collected_at": stmt.excluded.collected_at,
                },
            )
            result = await self._session.execute(stmt)
            if result.rowcount == 1:  # type: ignore[attr-defined]
                inserted += 1
            else:
                updated += 1

        await self._session.commit()
        return inserted, updated
