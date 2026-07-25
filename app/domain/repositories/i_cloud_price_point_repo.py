from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.cloud_price_point import CloudPricePoint


class ICloudPricePointRepository(ABC):
    @abstractmethod
    async def find(
        self,
        *,
        provider: str,
        service_type: str,
        region: str | None = None,
    ) -> list[CloudPricePoint]:
        """Pontos de preço reais para um provedor+tipo (região opcional)."""
        ...

    @abstractmethod
    async def providers_with_prices(self, service_type: str) -> list[str]:
        """Provedores que têm ao menos um preço real para o tipo de serviço."""
        ...

    @abstractmethod
    async def bulk_upsert(self, points: list[CloudPricePoint]) -> tuple[int, int]:
        """Upsert por (provider, sku_key, region). Retorna (inseridos, atualizados)."""
        ...
