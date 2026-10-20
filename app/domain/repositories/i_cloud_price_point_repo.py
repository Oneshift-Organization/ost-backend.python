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
    async def rates_for_category(self, category: str) -> list[CloudPricePoint]:
        """Taxa de uma categoria (storage_ssd, network_egress…) por provedor."""
        ...

    @abstractmethod
    async def regioes_com_preco(self, provider: str, service_type: str) -> list[str]:
        """Regiões que têm ao menos um preço para o provedor e o tipo, em ordem."""
        ...

    @abstractmethod
    async def bulk_upsert(self, points: list[CloudPricePoint]) -> tuple[int, int]:
        """Upsert por (provider, sku_key, region). Retorna (inseridos, atualizados)."""
        ...

    @abstractmethod
    async def substituir(self, points: list[CloudPricePoint]) -> tuple[int, int]:
        """Grava a coleta como o estado atual de cada conjunto que ela cobre.

        Conjunto = (provedor, região, tipo, fonte). Upsert do que veio e remoção,
        no mesmo conjunto, do que não veio: um tipo que o provedor deixou de
        vender, ou um nome que a coleta antiga gerava errado, não fica cotando.
        Retorna (gravados, removidos).
        """
        ...
