"""Snapshot de leitura e envio idempotente do agente — `/v1/agent/snapshots`.

Contrato: `MCP.PLAN/09-CONTRATO-SNAPSHOT.md` (v2). Plano:
`PLANO_SNAPSHOT_AGENTE.md`.

O agente lê um snapshot gravado e envia a análise apontando para ele. O envio
é corrigido contra **exatamente** o payload lido, nunca contra "a última
análise" do relatório, e não toca nas análises feitas pela tela.

Atrás de `AGENT_SNAPSHOT_ENABLED` (padrão `false`) e do interruptor geral do
agente (`AGENT_FLOW_ENABLED`): com qualquer um desligado, as rotas respondem
`404 SNAPSHOT_DESLIGADO`.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    CurrentUser,
    DbSession,
    consultar_acoplamento_uc,
    consultar_envios_uc,
    preparar_snapshot_uc,
    submeter_analise_uc,
)
from app.api.v1.envelope import erro, erro_do_payload
from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.domain.entities.agent_snapshot import AgentSnapshot, AgentSubmission
from app.schemas.agent_snapshot import (
    EnvioDoAgenteIn,
    EnvioOut,
    EnvioRegistradoOut,
    EnvioResumidoOut,
    PrepararSnapshotIn,
    ResumoDaAnaliseOut,
    SnapshotOut,
    SnapshotPreparadoOut,
)
from app.schemas.service_couplings import ServiceCouplingsResponse
from app.use_cases.agent.submeter_analise import EnvioRegistrado
from app.use_cases.analysis.payload_do_agente import PayloadDoAgenteError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _exigir_flags() -> None:
    settings = get_settings()
    if not (settings.agent_snapshot_enabled and settings.agent_flow_enabled):
        raise erro(
            status.HTTP_404_NOT_FOUND,
            "SNAPSHOT_DESLIGADO",
            "o snapshot do agente não está habilitado neste ambiente",
            retryable=False,
        )


@router.post(
    "/snapshots",
    response_model=SnapshotPreparadoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Prepara e grava o snapshot que o agente lê (não chama LLM)",
    responses={200: {"description": "Snapshot com o mesmo conteúdo reaproveitado"}},
)
async def preparar_snapshot(
    entrada: PrepararSnapshotIn,
    user: CurrentUser,
    session: DbSession,
    response: Response,
) -> SnapshotPreparadoOut:
    """Grava: não é uma leitura. Os mesmos parâmetros devolvem o mesmo snapshot
    enquanto o conteúdo calculado não muda (`reused: true`, status 200)."""
    _exigir_flags()
    try:
        preparado = await preparar_snapshot_uc(session).execute(
            UUID(user.sub), entrada.report_id, entrada.inventory_id
        )
    except PayloadDoAgenteError as e:
        raise erro_do_payload(e) from e
    await session.commit()

    if preparado.reaproveitado:
        response.status_code = status.HTTP_200_OK
    s = preparado.snapshot
    return SnapshotPreparadoOut(
        snapshot=SnapshotOut(
            snapshot_id=s.id,
            report_id=s.report_id,
            inventory_id=s.inventory_id,
            fingerprint=s.fingerprint,
            payload_fingerprint=s.payload_fingerprint,
            versions=s.versions,
            created_at=s.created_at,
            expires_at=s.expires_at,
            reused=preparado.reaproveitado,
        ),
        payload=preparado.payload,
        pools_sem_equivalente=s.pools_sem_equivalente,
    )


@router.post(
    "/snapshots/{snapshot_id}/submissions",
    response_model=EnvioRegistradoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Recebe a análise do agente e a corrige contra o snapshot lido",
    responses={
        200: {
            "description": "Mesmo corpo já enviado para este snapshot: devolve o envio "
            "gravado (cabeçalho `Idempotent-Replayed: true`)"
        }
    },
)
async def enviar_analise(
    snapshot_id: UUID,
    entrada: EnvioDoAgenteIn,
    user: CurrentUser,
    session: DbSession,
    response: Response,
) -> EnvioRegistradoOut:
    """A resposta é a análise **corrigida**: é ela que o agente deve narrar, não
    o que enviou. Repetir o mesmo corpo é seguro e nunca duplica o envio."""
    _exigir_flags()
    corpo = entrada.model_dump(mode="json")
    try:
        registrado = await _registrar(session, UUID(user.sub), snapshot_id, corpo)
    except PayloadDoAgenteError as e:
        raise erro_do_payload(e) from e

    if registrado.repetido:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    elif registrado.envio.campos_sobrescritos:
        logger.info(
            "autoridade_aplicada snapshot_id=%s revisao=%s campos=%s",
            snapshot_id,
            registrado.envio.revision,
            registrado.envio.campos_sobrescritos,
        )
    return _envio_out(registrado.envio, registrado.snapshot)


async def _registrar(
    session: DbSession, user_id: UUID, snapshot_id: UUID, corpo: dict[str, Any]
) -> EnvioRegistrado:
    """O lock de linha serializa os envios do mesmo snapshot, então a constraint
    única só dispara numa corrida que escapou dele. Nesse caso a segunda
    tentativa encontra o envio gravado e devolve como repetição."""
    try:
        registrado = await submeter_analise_uc(session).execute(user_id, snapshot_id, corpo)
        await session.commit()
        return registrado
    except IntegrityError:
        await session.rollback()
        registrado = await submeter_analise_uc(session).execute(user_id, snapshot_id, corpo)
        await session.commit()
        return registrado


def _envio_out(envio: AgentSubmission, snapshot: AgentSnapshot) -> EnvioRegistradoOut:
    return EnvioRegistradoOut(
        submission=EnvioOut(
            submission_id=envio.id,
            snapshot_id=envio.snapshot_id,
            report_id=envio.report_id,
            revision=envio.revision,
            fingerprint=snapshot.fingerprint,
            request_hash=envio.request_hash,
            versions=snapshot.versions,
            campos_sobrescritos=envio.campos_sobrescritos,
            created_at=envio.created_at,
        ),
        analysis=envio.analysis,
    )


@router.get(
    "/submissions/{submission_id}",
    response_model=EnvioRegistradoOut,
    summary="Um envio do agente, com a análise corrigida",
)
async def obter_envio(
    submission_id: UUID, user: CurrentUser, session: DbSession
) -> EnvioRegistradoOut:
    """O mesmo corpo da resposta do envio. É o caminho para recuperar um
    resultado pelo id."""
    _exigir_flags()
    encontrado = await consultar_envios_uc(session).obter(UUID(user.sub), submission_id)
    if encontrado is None:
        raise erro(
            status.HTTP_404_NOT_FOUND,
            "ENVIO_NAO_ENCONTRADO",
            f"envio {submission_id} não existe para este usuário",
            retryable=False,
        )
    return _envio_out(*encontrado)


@router.get(
    "/submissions/{submission_id}/service-couplings",
    response_model=ServiceCouplingsResponse,
    summary="Acoplamento por serviço do payload que o agente leu",
)
async def obter_acoplamento_do_envio(
    submission_id: UUID, user: CurrentUser, session: DbSession
) -> ServiceCouplingsResponse:
    """A mesma visão de `GET /v1/analyses/{job_id}/service-couplings`, calculada
    do snapshot do envio. PLANO_LOCKIN_INVENTARIO §4."""
    _exigir_flags()
    try:
        visao = await consultar_acoplamento_uc(session).do_envio(UUID(user.sub), submission_id)
    except NotFoundError as e:
        raise erro(
            status.HTTP_404_NOT_FOUND, "ENVIO_NAO_ENCONTRADO", str(e), retryable=False
        ) from e
    except ConflictError as e:
        raise erro(
            status.HTTP_409_CONFLICT, "ANALISE_SEM_SERVICOS", str(e), retryable=False
        ) from e
    return ServiceCouplingsResponse.de(visao)


@router.get(
    "/submissions",
    response_model=list[EnvioResumidoOut],
    summary="Envios do agente, do mais recente para o mais antigo, com as revisões",
)
async def listar_envios(
    user: CurrentUser,
    session: DbSession,
    report_id: UUID | None = None,
    snapshot_id: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[EnvioResumidoOut]:
    """Filtra por relatório ou por snapshot. Cada item traz um resumo; a análise
    completa vem pelo id."""
    _exigir_flags()
    encontrados = await consultar_envios_uc(session).listar(
        UUID(user.sub), report_id=report_id, snapshot_id=snapshot_id, limite=limit
    )
    return [
        EnvioResumidoOut(
            submission=_envio_out(envio, snapshot).submission,
            analysis_summary=_resumo(envio.analysis),
        )
        for envio, snapshot in encontrados
    ]


def _resumo(analise: dict[str, Any]) -> ResumoDaAnaliseOut:
    def texto(chave: str) -> str | None:
        valor = analise.get(chave)
        return str(valor) if valor not in (None, "") else None

    return ResumoDaAnaliseOut(
        recommended_provider=texto("recommended_provider"),
        recommendation=texto("recommendation"),
        analysis_confidence=texto("analysis_confidence"),
        sugestoes=len(analise.get("sugestoes") or []),
    )
