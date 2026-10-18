"""Consulta do acoplamento por serviço de uma análise gravada (PLANO_LOCKIN_INVENTARIO A4).

Calculado na leitura, do payload determinístico que já ficou salvo: corrige
também as análises feitas antes desta visão existir. Não chama LLM e não grava.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
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
from app.domain.services.decision_support import number
from app.use_cases.agent.consultar_envios import ConsultarEnviosUseCase
from app.use_cases.analysis.equivalence_pricing import _CATEGORIES
from app.use_cases.analysis.equivalence_projection import project_equivalences

# O nome de cada categoria cotada, o mesmo das linhas "(agregado)" da tabela
_ROTULOS = {chave: rotulo for chave, (rotulo, _tipos) in _CATEGORIES.items()}


class AnaliseNaoConcluidaError(ConflictError):
    """Ainda rodando: repetir depois."""


class AnaliseFalhouError(ConflictError):
    """Terminou com falha: repetir não muda nada, só uma análise nova."""


class AnaliseSemServicosError(ConflictError):
    """Payload anterior à lista de serviços: só uma análise nova resolve."""


class ConsultarAcoplamentoUseCase:
    def __init__(self, jobs: IAnalysisJobRepository, envios: ConsultarEnviosUseCase) -> None:
        self._jobs = jobs
        self._envios = envios

    async def da_analise(self, user_id: UUID, job_id: UUID) -> AcoplamentoPorServico:
        job = await self._jobs.get_by_id(job_id, user_id)
        if job is None:
            raise NotFoundError(f"análise {job_id} não encontrada")
        if job.status == AnalysisStatus.FAILED:
            raise AnaliseFalhouError(f"a análise {job_id} terminou com falha")
        if job.status != AnalysisStatus.COMPLETED or not job.llm_input_payload:
            raise AnaliseNaoConcluidaError(
                f"a análise {job_id} ainda não foi concluída (status {job.status})"
            )
        # O preço das opções é o da tabela de equivalentes gravada, a mesma que a tela mostra
        linhas = (job.ai_structured_output or {}).get("cross_cloud_equivalents")
        return _montar(job.llm_input_payload, linhas)

    async def do_envio(self, user_id: UUID, submission_id: UUID) -> AcoplamentoPorServico:
        encontrado = await self._envios.obter(user_id, submission_id)
        if encontrado is None:
            raise NotFoundError(f"envio {submission_id} não existe para este usuário")
        _, snapshot = encontrado
        return _montar(snapshot.payload)


def _montar(
    payload: Mapping[str, Any], linhas: Iterable[Mapping[str, Any]] | None = None
) -> AcoplamentoPorServico:
    try:
        if linhas is None:
            linhas = [r.model_dump() for r in project_equivalences(dict(payload))]
        return montar_acoplamento_por_servico(payload, precos_por_servico(linhas), _ROTULOS)
    except PayloadSemServicosError as e:
        raise AnaliseSemServicosError(str(e)) from e


def precos_por_servico(linhas: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Decimal]:
    """(serviço, destino) → o preço que entrou no total. As alternativas de um destino
    dividem o mesmo orçamento: só a linha marcada conta; tabela antiga, sem a marca, a
    primeira com preço."""
    precos: dict[tuple[str, str], Decimal] = {}
    for linha in linhas:
        custo = number(linha.get("estimated_monthly_cost"))
        if custo is None or linha.get("included_in_estimate") is False:
            continue
        chave = (str(linha.get("workload") or ""), str(linha.get("target_provider") or "").lower())
        precos.setdefault(chave, custo)
    return precos
