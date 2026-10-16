from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.infrastructure.database.models.cloud_price_point import CloudPricePointModel


def _to_entity(row: CloudPricePointModel) -> CloudPricePoint:
    return CloudPricePoint(
        provider=row.provider,
        sku_key=row.sku_key,
        service_name=row.service_name,
        service_type=row.service_type,
        region=row.region,
        price=row.price,
        currency=row.currency,
        unit=row.unit,
        vcpus=row.vcpus,
        ram_gib=row.ram_gib,
        source=row.source,
        confidence=row.confidence,
        captured_at=row.captured_at,
        estado=row.estado,
        confianca_score=row.confianca_score,
        confirmado_em=row.confirmado_em,
    )


# Só o preço que a última coleta ainda trouxe entra na cotação
_VIGENTE = CloudPricePointModel.ausente_desde.is_(None)


class PgCloudPricePointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(
        self,
        *,
        provider: str,
        service_type: str,
        region: str | None = None,
    ) -> list[CloudPricePoint]:
        stmt = select(CloudPricePointModel).where(
            CloudPricePointModel.provider.ilike(provider),
            CloudPricePointModel.service_type == service_type,
            _VIGENTE,
        )
        if region is not None:
            stmt = stmt.where(CloudPricePointModel.region == region)
        # Ordem explícita: sem ORDER BY o Postgres não garante ordem estável, e
        # empates de score no packer resolveriam de forma diferente entre runs.
        stmt = stmt.order_by(CloudPricePointModel.price, CloudPricePointModel.sku_key)
        result = await self._session.execute(stmt)
        return [_to_entity(r) for r in result.scalars().all()]

    async def rates_for_category(self, category: str) -> list[CloudPricePoint]:
        stmt = (
            select(CloudPricePointModel)
            .where(CloudPricePointModel.service_type == category, _VIGENTE)
            .order_by(CloudPricePointModel.provider, CloudPricePointModel.sku_key)
        )
        result = await self._session.execute(stmt)
        return [_to_entity(r) for r in result.scalars().all()]

    async def providers_with_prices(self, service_type: str) -> list[str]:
        stmt = (
            select(CloudPricePointModel.provider)
            .where(CloudPricePointModel.service_type == service_type, _VIGENTE)
            .distinct()
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def regioes_com_preco(self, provider: str, service_type: str) -> list[str]:
        stmt = (
            select(CloudPricePointModel.region)
            .where(
                CloudPricePointModel.provider.ilike(provider),
                CloudPricePointModel.service_type == service_type,
                _VIGENTE,
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return sorted(row[0] for row in result.all())

    async def substituir(self, points: list[CloudPricePoint]) -> tuple[int, int]:
        if not points:
            return (0, 0)
        gravados, _ = await self.bulk_upsert(points)
        conjuntos: dict[tuple[str, str, str, str], set[str]] = {}
        for p in points:
            chave = (p.provider.lower(), p.region, p.service_type, p.source)
            conjuntos.setdefault(chave, set()).add(p.sku_key)
        # O que a coleta deixou de trazer fica, marcado como ausente: envelhece no
        # ciclo de vida até ADUBO e segue de referência, mas sai da cotação
        # (PLANO_CICLO_DE_VIDA_DO_PRECO §6, decisão 4)
        ausentes = 0
        agora = datetime.now(UTC)
        for (provider, region, service_type, source), chaves in conjuntos.items():
            result = await self._session.execute(
                update(CloudPricePointModel)
                .where(
                    CloudPricePointModel.provider == provider,
                    CloudPricePointModel.region == region,
                    CloudPricePointModel.service_type == service_type,
                    CloudPricePointModel.source == source,
                    CloudPricePointModel.sku_key.not_in(chaves),
                    _VIGENTE,
                )
                .values(ausente_desde=agora)
            )
            ausentes += int(getattr(result, "rowcount", 0) or 0)
        return (gravados, ausentes)

    async def bulk_upsert(self, points: list[CloudPricePoint]) -> tuple[int, int]:
        if not points:
            return (0, 0)
        now = datetime.now(UTC)
        seen = 0
        for p in points:
            values = {
                "provider": p.provider.lower(),
                "sku_key": p.sku_key,
                "region": p.region,
                "service_name": p.service_name,
                "service_type": p.service_type,
                "price": p.price,
                "currency": p.currency,
                "unit": p.unit,
                "vcpus": p.vcpus,
                "ram_gib": p.ram_gib,
                "source": p.source,
                "confidence": p.confidence,
                "captured_at": p.captured_at or now,
                # A coleta trouxe: deixa de estar ausente
                "ausente_desde": None,
            }
            stmt = pg_insert(CloudPricePointModel).values(**values)
            update_cols = {
                c: stmt.excluded[c]
                for c in (
                    "service_name",
                    "service_type",
                    "price",
                    "currency",
                    "unit",
                    "vcpus",
                    "ram_gib",
                    "source",
                    "confidence",
                    "captured_at",
                    "ausente_desde",
                )
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["provider", "sku_key", "region"], set_=update_cols
            )
            await self._session.execute(stmt)
            seen += 1
        # asyncpg upsert não distingue insert de update de forma barata; reportamos
        # o total tocado como "processados". O sync loga o antes/depois via count.
        return (seen, 0)
