from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.services.currency import FxRate
from app.infrastructure.database.models.exchange_rate import ExchangeRateModel


class PgExchangeRateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest(self, par: str) -> FxRate | None:
        stmt = (
            select(ExchangeRateModel)
            .where(ExchangeRateModel.par == par)
            .order_by(ExchangeRateModel.data.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return FxRate(par=row.par, taxa=row.taxa, data=row.data, fonte=row.fonte)
