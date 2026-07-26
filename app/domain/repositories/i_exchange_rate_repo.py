from __future__ import annotations

from typing import Protocol

from app.domain.services.currency import FxRate


class IExchangeRateRepository(Protocol):
    async def get_latest(self, par: str) -> FxRate | None:
        """Taxa mais recente para o par (ex.: 'BRL/USD'), ou None."""
        ...
