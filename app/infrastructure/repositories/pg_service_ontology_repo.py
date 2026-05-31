from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.service_ontology import ServiceOntology
from app.infrastructure.database.models.service_ontology import ServiceOntologyModel


class PgServiceOntologyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_provider_and_usage_type(
        self, provider: str, usage_type: str
    ) -> ServiceOntology | None:
        stmt = (
            select(ServiceOntologyModel)
            .where(
                ServiceOntologyModel.provider == provider,
                ServiceOntologyModel.usage_type == usage_type,
                ServiceOntologyModel.active.is_(True),
            )
            .order_by(ServiceOntologyModel.version.desc())
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return ServiceOntology(
            id=row.id,
            provider=row.provider,
            usage_type=row.usage_type,
            service_name=row.service_name,
            category=row.category,
            service_type=row.service_type,
            confidence=row.confidence,
            version=row.version,
            active=row.active,
            deprecated_at=row.deprecated_at,
            created_at=row.created_at,
        )
