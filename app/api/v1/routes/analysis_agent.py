"""Superfície de agente — `/v1/agent/*`.

Prefixo separado de propósito: quem consome `/v1/analyses` não vê diferença
nenhuma, nem no OpenAPI. O legado (`Frontend → API → LLM`) segue intocado, e a
suíte de caracterização em `tests/integration/test_legado_caracterizacao.py`
prova isso a cada execução.

Atrás da flag `AGENT_FLOW_ENABLED` (default `false`): com ela desligada as rotas
respondem 404, como se não existissem. Ligar **não desliga o legado** — os dois
convivem, e a virada real é o consumidor parar de chamar o caminho antigo.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.schemas.agent import AgentAnalysisIn, AgentAnalysisOut
from app.use_cases.analysis.agent_result import receber_analise_do_agente

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/analyses", tags=["agent"])


def _erro(status_code: int, codigo: str, mensagem: str, *, retryable: bool, **extra):
    """Mesmo envelope da superfície de agente em `/v1/analyses/{id}/request`.

    Um modelo não consegue decidir entre insistir e desistir a partir de texto
    livre — `retryable` é o que torna a decisão possível.
    """
    corpo: dict = {"codigo": codigo, "mensagem": mensagem, "retryable": retryable}
    corpo.update(extra)
    return HTTPException(status_code=status_code, detail=corpo)


def _exigir_flag() -> None:
    if not get_settings().agent_flow_enabled:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "FLUXO_AGENTE_DESLIGADO",
            "o fluxo de agente não está habilitado neste ambiente",
            retryable=False,
        )


async def _job_com_payload(report_id: UUID, user_id: UUID, session):
    """Job mais recente do report que tenha o payload determinístico gravado.

    É o payload que serve de verdade para a autoridade — sem ele não há com o
    que comparar a resposta do agente, e sobrescrever às cegas seria pior que
    não sobrescrever.
    """
    return (
        await session.execute(
            select(AnalysisJobModel)
            .where(
                AnalysisJobModel.report_id == report_id,
                AnalysisJobModel.user_id == user_id,
                AnalysisJobModel.llm_input_payload.isnot(None),
            )
            .order_by(AnalysisJobModel.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


@router.post(
    "/{report_id}/result",
    response_model=AgentAnalysisOut,
    status_code=status.HTTP_200_OK,
    deprecated=True,
    summary="DEPRECADO — use POST /v1/agent/snapshots/{snapshot_id}/submissions",
    description=(
        "Grava a análise do agente por cima da última análise do relatório, "
        "inclusive a feita pela tela, e só funciona se o relatório já tiver uma "
        "análise gravada. O substituto corrige contra o snapshot lido e não toca "
        "nas análises da tela (PLANO_SNAPSHOT_AGENTE). Use só com relatório de "
        "teste; a remoção será combinada com o time do MCP."
    ),
)
async def submeter_analise(
    report_id: UUID,
    entrada: AgentAnalysisIn,
    user: CurrentUser,
    session: DbSession,
) -> AgentAnalysisOut:
    """A resposta é a análise **corrigida** — é ela que deve ser narrada ao
    usuário, não o que foi enviado."""
    _exigir_flag()
    user_uuid = UUID(user.sub)

    job = await _job_com_payload(report_id, user_uuid, session)
    if job is None:
        raise _erro(
            status.HTTP_409_CONFLICT,
            "PAYLOAD_AUSENTE",
            "não há análise com payload determinístico para este report; "
            "chame GET /v1/analyses/{report_id}/request antes",
            retryable=True,
        )

    bruto = entrada.model_dump(mode="json")
    resultado = receber_analise_do_agente(
        payload_backend=dict(job.llm_input_payload or {}),
        resposta_agente=bruto,
        sugestoes=[s.model_dump(mode="json") for s in entrada.sugestoes],
    )

    # Bruto e corrigido lado a lado: sem os dois não há como medir o quanto o
    # modelo erra, nem justificar depois por que um número mudou na tela.
    job.agent_raw_output = bruto
    job.ai_structured_output = resultado.resposta
    job.sugestoes = resultado.sugestoes
    await session.commit()

    if resultado.autoridade.substituicoes:
        logger.info(
            "autoridade_aplicada job_id=%s campos=%s",
            job.job_id,
            len(resultado.autoridade.substituicoes),
        )

    return AgentAnalysisOut(
        **resultado.resposta,
        job_id=job.job_id,
        report_id=report_id,
        campos_sobrescritos=len(resultado.autoridade.substituicoes),
        created_at=job.created_at,
    )


@router.get(
    "/{report_id}/result",
    response_model=AgentAnalysisOut,
    status_code=status.HTTP_200_OK,
    summary="Análise corrigida, com as sugestões para exibir em tela",
)
async def obter_analise(
    report_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> AgentAnalysisOut:
    _exigir_flag()
    user_uuid = UUID(user.sub)

    job = (
        await session.execute(
            select(AnalysisJobModel)
            .where(
                AnalysisJobModel.report_id == report_id,
                AnalysisJobModel.user_id == user_uuid,
                AnalysisJobModel.agent_raw_output.isnot(None),
            )
            .order_by(AnalysisJobModel.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if job is None:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "ANALISE_NAO_ENCONTRADA",
            "nenhuma análise de agente registrada para este report",
            retryable=False,
        )

    corrigida = dict(job.ai_structured_output or {})
    return AgentAnalysisOut(
        **corrigida,
        job_id=job.job_id,
        report_id=report_id,
        campos_sobrescritos=0,
        created_at=job.created_at,
    )
