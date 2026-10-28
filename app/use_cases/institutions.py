from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.domain.repositories.i_institution_repo import IInstitutionRepository


@dataclass(frozen=True)
class CreateInstitutionInput:
    user_id: UUID
    name: str
    display_name: str


@dataclass(frozen=True)
class JoinInstitutionInput:
    user_id: UUID
    code: str
    display_name: str


@dataclass(frozen=True)
class PublishAnalysisInput:
    user_id: UUID
    institution_id: UUID
    job_id: UUID


class CreateInstitutionUseCase:
    def __init__(self, repository: IInstitutionRepository) -> None:
        self._repo = repository

    async def execute(self, data: CreateInstitutionInput) -> dict[str, Any]:
        return await self._repo.create(data.user_id, data.name, data.display_name)


class JoinInstitutionUseCase:
    def __init__(self, repository: IInstitutionRepository) -> None:
        self._repo = repository

    async def execute(self, data: JoinInstitutionInput) -> dict[str, Any]:
        return await self._repo.join(data.user_id, data.code, data.display_name)


class PublishAnalysisUseCase:
    def __init__(self, repository: IInstitutionRepository) -> None:
        self._repo = repository

    async def execute(self, data: PublishAnalysisInput) -> dict[str, Any]:
        return await self._repo.publish(data.user_id, data.institution_id, data.job_id)
