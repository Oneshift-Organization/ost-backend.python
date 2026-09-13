from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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

    async def upsert_many(self, taxas: list[FxRate]) -> int:
        """Grava cotações por (par, data). Idempotente.

        Re-rodar o sync no mesmo dia atualiza a linha em vez de duplicar — a
        chave primária é (par, data), e o BCE republica a mesma data enquanto
        não abre o próximo dia útil.
        """
        gravadas = 0
        for t in taxas:
            if t.data is None:
                continue
            stmt = insert(ExchangeRateModel).values(
                par=t.par, data=t.data, taxa=t.taxa, fonte=t.fonte
            )
            await self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["par", "data"],
                    set_={"taxa": stmt.excluded.taxa, "fonte": stmt.excluded.fonte},
                )
            )
            gravadas += 1
        return gravadas
