from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from app.domain.entities.cloud_sku import CloudSku


class ICloudSkuRepository(ABC):
    @abstractmethod
    async def get_by_id(self, provider: str, sku_id: str) -> CloudSku | None:
        ...

    @abstractmethod
    async def find_equivalents(
        self,
        *,
        provider: str,
        service_type: str,
        min_vcpus: Decimal | None = None,
        min_ram_gb: Decimal | None = None,
        limit: int = 10,
    ) -> list[CloudSku]:
        ...

    @abstractmethod
    async def list_by_provider(self, provider: str) -> list[CloudSku]:
        ...

    @abstractmethod
    async def list_providers(self) -> list[str]:
        ...

    @abstractmethod
    async def bulk_upsert(self, skus: list[CloudSku]) -> tuple[int, int]:
        """Bulk upsert SKUs. Returns (inserted, updated)."""
