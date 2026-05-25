from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.analysis import ServiceComparison, ServiceMetadata


class IServiceMetadataRepository(ABC):
    @abstractmethod
    async def list_by_provider_and_category(
        self, provider: str, category: str | None = None
    ) -> list[ServiceMetadata]:
        ...

    @abstractmethod
    async def list_by_providers(
        self, providers: list[str]
    ) -> list[ServiceMetadata]:
        ...


class IServiceComparisonRepository(ABC):
    @abstractmethod
    async def find_equivalents(
        self, category: str, exclude_provider: str
    ) -> list[ServiceComparison]:
        ...

    @abstractmethod
    async def find_by_provider_and_service(
        self, provider: str, service_name: str
    ) -> ServiceComparison | None:
        ...

    @abstractmethod
    async def list_by_provider(
        self, provider: str
    ) -> list[ServiceComparison]:
        ...

    @abstractmethod
    async def list_all(self) -> list[ServiceComparison]:
        ...
