import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analysis import ServiceComparison, ServiceMetadata
from app.domain.interfaces.i_service_repositories import (
    IServiceComparisonRepository,
    IServiceMetadataRepository,
)
from app.infrastructure.database.models.cloud_services_comparation import (
    CloudServiceComparisonModel,
)
from app.infrastructure.database.models.cloud_services_metadata import (
    CloudServiceMetadataModel,
)
from app.infrastructure.database.models.service_name_suggestion import (
    ServiceNameSuggestionModel,
)


class PGServiceMetadataRepository(IServiceMetadataRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_provider_and_category(
        self, provider: str, category: str | None = None
    ) -> list[ServiceMetadata]:
        stmt = select(CloudServiceMetadataModel).where(
            CloudServiceMetadataModel.provider == provider
        )
        if category:
            stmt = stmt.where(CloudServiceMetadataModel.category == category)
        result = await self._session.execute(stmt)
        rows: Sequence[CloudServiceMetadataModel] = result.scalars().all()
        return [
            ServiceMetadata(
                id=row.id,
                provider=row.provider,
                category=row.category,
                service_name=row.service_name,
                metadata=row.service_metadata,
                docs_costs=row.docs_costs,
            )
            for row in rows
        ]

    async def list_by_providers(
        self, providers: list[str]
    ) -> list[ServiceMetadata]:
        # Ordem explícita: o resultado alimenta dicts indexados por
        # (provider, service_name) — sem ORDER BY, qual linha "vence" numa
        # duplicata dependeria da ordem que o Postgres devolveu, que não é
        # garantida. Isso vazava para o payload e o tornava não-reproduzível.
        stmt = (
            select(CloudServiceMetadataModel)
            .where(CloudServiceMetadataModel.provider.in_(providers))
            .order_by(
                CloudServiceMetadataModel.provider,
                CloudServiceMetadataModel.service_name,
            )
        )
        result = await self._session.execute(stmt)
        rows: Sequence[CloudServiceMetadataModel] = result.scalars().all()
        return [
            ServiceMetadata(
                id=row.id,
                provider=row.provider,
                category=row.category,
                service_name=row.service_name,
                metadata=row.service_metadata,
                docs_costs=row.docs_costs,
            )
            for row in rows
        ]


logger = logging.getLogger(__name__)


class PGServiceComparisonRepository(IServiceComparisonRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def apelidos_aprovados(self, provider: str) -> dict[str, str]:
        # Savepoint: código novo antes da migration não pode derrubar a análise
        try:
            async with self._session.begin_nested():
                linhas = await self._session.execute(
                    select(ServiceNameSuggestionModel.chave, ServiceNameSuggestionModel.alvo).where(
                        ServiceNameSuggestionModel.provider == provider.lower(),
                        ServiceNameSuggestionModel.status == "aprovada",
                        ServiceNameSuggestionModel.alvo.is_not(None),
                    )
                )
                return {chave: alvo for chave, alvo in linhas.all() if alvo}
        except SQLAlchemyError:
            logger.warning("apelidos_aprovados_indisponiveis provider=%s", provider)
            return {}

    async def find_equivalents(
        self, category: str, exclude_provider: str
    ) -> list[ServiceComparison]:
        stmt = (
            select(CloudServiceComparisonModel)
            .where(
                CloudServiceComparisonModel.category == category,
                CloudServiceComparisonModel.provider != exclude_provider,
            )
            .order_by(CloudServiceComparisonModel.service_type)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            ServiceComparison(
                id=row.id,
                category=row.category,
                service_type=row.service_type,
                provider=row.provider,
                service_name=row.service_name,
            )
            for row in rows
        ]

    async def find_by_provider_and_service(
        self, provider: str, service_name: str
    ) -> ServiceComparison | None:
        stmt = select(CloudServiceComparisonModel).where(
            CloudServiceComparisonModel.provider == provider,
            CloudServiceComparisonModel.service_name == service_name,
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return ServiceComparison(
            id=row.id,
            category=row.category,
            service_type=row.service_type,
            provider=row.provider,
            service_name=row.service_name,
        )

    async def list_by_provider(
        self, provider: str
    ) -> list[ServiceComparison]:
        stmt = select(CloudServiceComparisonModel).where(
            CloudServiceComparisonModel.provider == provider
        ).order_by(CloudServiceComparisonModel.service_name)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            ServiceComparison(
                id=row.id,
                category=row.category,
                service_type=row.service_type,
                provider=row.provider,
                service_name=row.service_name,
            )
            for row in rows
        ]

    async def list_all(self) -> list[ServiceComparison]:
        stmt = select(CloudServiceComparisonModel).order_by(
            CloudServiceComparisonModel.provider, CloudServiceComparisonModel.service_name
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            ServiceComparison(
                id=row.id,
                category=row.category,
                service_type=row.service_type,
                provider=row.provider,
                service_name=row.service_name,
            )
            for row in rows
        ]
