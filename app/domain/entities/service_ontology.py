from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class ServiceOntology:
    id: UUID
    provider: str
    usage_type: str
    service_name: str
    category: str
    service_type: str
    confidence: str | None = None
    version: int = 1
    active: bool = True
    deprecated_at: datetime | None = None
    created_at: datetime | None = None
