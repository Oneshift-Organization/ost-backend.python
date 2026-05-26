from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.analysis_job import AnalysisJob
from app.domain.repositories.i_analysis_job_repo import IAnalysisJobRepository
from app.infrastructure.database.models.analysis_job import AnalysisJobModel


class PgAnalysisJobRepository(IAnalysisJobRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, job: AnalysisJob) -> None:
        model = AnalysisJobModel(
            job_id=job.job_id,
            user_id=job.user_id,
            report_id=job.report_id,
            external_id=job.external_id,
            status=job.status,
            llm_output=job.llm_output,
            error=job.error,
            ai_structured_output=job.ai_structured_output,
            created_at=job.created_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def update(self, job: AnalysisJob) -> None:
        stmt = select(AnalysisJobModel).where(AnalysisJobModel.job_id == job.job_id)
        result = await self._session.execute(stmt)
        model = result.scalars().first()
        if model is None:
            return
        model.status = job.status
        model.llm_output = job.llm_output
        model.error = job.error
        model.ai_structured_output = job.ai_structured_output

    async def get_by_id(self, job_id: UUID, user_id: UUID) -> AnalysisJob | None:
        stmt = select(AnalysisJobModel).where(
            AnalysisJobModel.job_id == job_id,
            AnalysisJobModel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalars().first()
        if model is None:
            return None
        return self._to_entity(model)

    async def get_by_external_id(self, external_id: UUID, user_id: UUID) -> AnalysisJob | None:
        stmt = (
            select(AnalysisJobModel)
            .where(
                AnalysisJobModel.external_id == external_id,
                AnalysisJobModel.user_id == user_id,
            )
            .order_by(AnalysisJobModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalars().first()
        if model is None:
            return None
        return self._to_entity(model)

    def _to_entity(self, model: AnalysisJobModel) -> AnalysisJob:
        return AnalysisJob(
            job_id=model.job_id,
            user_id=model.user_id,
            report_id=model.report_id,
            external_id=model.external_id,
            status=model.status,
            llm_output=model.llm_output,
            error=model.error,
            ai_structured_output=(
                dict(model.ai_structured_output) if model.ai_structured_output else None
            ),
            created_at=model.created_at,
        )
