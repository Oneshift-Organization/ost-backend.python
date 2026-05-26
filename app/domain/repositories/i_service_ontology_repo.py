from typing import Protocol

from app.domain.entities.service_ontology import ServiceOntology


class IServiceOntologyRepository(Protocol):
    async def find_by_provider_and_usage_type(
        self, provider: str, usage_type: str
    ) -> ServiceOntology | None:
        ...
