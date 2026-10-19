from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.analysis_job import (
    ACTIVE_STATUSES,
    AnalysisJob,
    AnalysisStatus,
    agora_utc,
)
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
            llm_input_payload=job.llm_input_payload,
            disp_status=job.disp_status,
            created_at=job.created_at,
            started_at=job.started_at,
            updated_at=job.updated_at or job.created_at,
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
        model.llm_input_payload = job.llm_input_payload
        model.disp_status = job.disp_status
        model.disp_status_updated_at = job.disp_status_updated_at
        model.started_at = job.started_at
        model.updated_at = job.updated_at

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

    async def marcar_processando(self, job_id: UUID) -> None:
        """Commita a transição para PROCESSING em transação PRÓPRIA e curta.

        Antes, `mark_processing()` só mutava o objeto ORM e o único commit vinha
        DEPOIS da chamada ao LLM inteira — o estado intermediário simplesmente
        não existia no banco. Quem fazia polling via PENDING por 30-60s e depois
        COMPLETED; um processo morto no meio deixava o job em PENDING para
        sempre, indistinguível de um recém-criado.
        """
        agora = agora_utc()
        await self._session.execute(
            update(AnalysisJobModel)
            .where(AnalysisJobModel.job_id == job_id)
            .values(status=AnalysisStatus.PROCESSING, started_at=agora, updated_at=agora)
        )
        await self._session.commit()

    async def ultima_concluida_por_report(
        self, report_id: UUID, user_id: UUID, *, exceto: UUID
    ) -> AnalysisJob | None:
        """A análise concluída mais recente deste report, fora a do job atual (M3)."""
        stmt = (
            select(AnalysisJobModel)
            .where(
                AnalysisJobModel.report_id == report_id,
                AnalysisJobModel.user_id == user_id,
                AnalysisJobModel.job_id != exceto,
                AnalysisJobModel.status == AnalysisStatus.COMPLETED.value,
            )
            .order_by(AnalysisJobModel.created_at.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return self._to_entity(model) if model else None

    async def buscar_ativo_por_report(
        self, report_id: UUID, user_id: UUID
    ) -> AnalysisJob | None:
        """Job ainda em andamento para este report, se houver (get-or-create)."""
        stmt = (
            select(AnalysisJobModel)
            .where(
                AnalysisJobModel.report_id == report_id,
                AnalysisJobModel.user_id == user_id,
                AnalysisJobModel.status.in_([s.value for s in ACTIVE_STATUSES]),
            )
            .order_by(AnalysisJobModel.created_at.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return self._to_entity(model) if model else None

    async def expirar_travados(self, limite: timedelta) -> int:
        """Marca FAILED todo job ativo parado além do limite. Devolve quantos.

        É a válvula que torna a idempotência segura: sem ela, um job travado
        bloquearia o report para sempre (o índice único parcial impediria criar
        outro). O relógio é `updated_at`, com fallback em `created_at` para
        linhas anteriores à migration.
        """
        corte = agora_utc() - limite
        agora = agora_utc()
        result = await self._session.execute(
            update(AnalysisJobModel)
            .where(
                AnalysisJobModel.status.in_([s.value for s in ACTIVE_STATUSES]),
                func.coalesce(AnalysisJobModel.updated_at, AnalysisJobModel.created_at)
                < corte,
            )
            .values(
                status=AnalysisStatus.FAILED,
                error="TIMEOUT: execucao nao concluiu dentro do limite",
                updated_at=agora,
            )
        )
        await self._session.commit()
        return int(result.rowcount or 0)

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
            disp_status=model.disp_status,
            disp_status_updated_at=model.disp_status_updated_at,
            llm_input_payload=(
                dict(model.llm_input_payload) if model.llm_input_payload else None
            ),
            created_at=model.created_at,
            started_at=model.started_at,
            updated_at=model.updated_at,
        )
