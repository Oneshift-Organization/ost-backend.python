from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.analysis_job import AnalysisJob


class IAnalysisJobRepository(ABC):
    @abstractmethod
    async def create(self, job: AnalysisJob) -> None:
        ...

    @abstractmethod
    async def update(self, job: AnalysisJob) -> None:
        ...

    @abstractmethod
    async def get_by_id(self, job_id: UUID, user_id: UUID) -> AnalysisJob | None:
        ...

    @abstractmethod
    async def get_by_external_id(self, external_id: UUID, user_id: UUID) -> AnalysisJob | None:
        ...
