"""Sync de preços oficiais para cloud_price_points (Precision Engine v3 — Wave 3).

Orquestra: um *fetcher* (infra, busca da API do provedor) → repo de price points.
Roda offline (script/cron), nunca no caminho crítico da análise. Provider-agnóstico:
o mesmo use case serve Azure, OCI etc. — só muda o fetcher injetado.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository

logger = logging.getLogger(__name__)

PriceFetcher = Callable[[], Sequence[CloudPricePoint]]


@dataclass(frozen=True)
class SyncCloudPricesResult:
    provider: str
    fetched: int
    upserted: int


class SyncCloudPricesUseCase:
    def __init__(self, price_repo: ICloudPricePointRepository) -> None:
        self._repo = price_repo

    async def execute(self, provider: str, fetcher: PriceFetcher) -> SyncCloudPricesResult:
        points = list(fetcher())
        if not points:
            logger.warning("price_sync_empty", extra={"provider": provider})
            return SyncCloudPricesResult(provider=provider, fetched=0, upserted=0)
        upserted, _ = await self._repo.bulk_upsert(points)
        logger.info(
            "price_sync_done",
            extra={"provider": provider, "fetched": len(points), "upserted": upserted},
        )
        return SyncCloudPricesResult(
            provider=provider, fetched=len(points), upserted=upserted
        )
