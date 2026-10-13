"""Consulta do acoplamento por serviço de uma análise gravada (PLANO_LOCKIN_INVENTARIO A4).

Calculado na leitura, do payload determinístico que já ficou salvo: corrige
também as análises feitas antes desta visão existir. Não chama LLM e não grava.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.core.exceptions import ConflictError, NotFoundError
from app.domain.entities.analysis_job import AnalysisStatus
from app.domain.repositories.i_analysis_job_repo import IAnalysisJobRepository
from app.domain.services.acoplamento_por_servico import (
    AcoplamentoPorServico,
    PayloadSemServicosError,
    montar_acoplamento_por_servico,
)
from app.use_cases.agent.consultar_envios import ConsultarEnviosUseCase


class ConsultarAcoplamentoUseCase:
    def __init__(self, jobs: IAnalysisJobRepository, envios: ConsultarEnviosUseCase) -> None:
        self._jobs = jobs
        self._envios = envios

    async def da_analise(self, user_id: UUID, job_id: UUID) -> AcoplamentoPorServico:
        job = await self._jobs.get_by_id(job_id, user_id)
        if job is None:
            raise NotFoundError(f"análise {job_id} não encontrada")
        if job.status != AnalysisStatus.COMPLETED or not job.llm_input_payload:
            raise ConflictError(f"a análise {job_id} ainda não foi concluída (status {job.status})")
        return _montar(job.llm_input_payload)

    async def do_envio(self, user_id: UUID, submission_id: UUID) -> AcoplamentoPorServico:
        encontrado = await self._envios.obter(user_id, submission_id)
        if encontrado is None:
            raise NotFoundError(f"envio {submission_id} não existe para este usuário")
        _, snapshot = encontrado
        return _montar(snapshot.payload)


def _montar(payload: Mapping[str, Any]) -> AcoplamentoPorServico:
    try:
        return montar_acoplamento_por_servico(payload)
    except PayloadSemServicosError as e:
        raise ConflictError(str(e)) from e
