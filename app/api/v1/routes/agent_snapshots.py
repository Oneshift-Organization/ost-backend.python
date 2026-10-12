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

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, preparar_snapshot_uc
from app.api.v1.envelope import erro, erro_do_payload
from app.core.config import get_settings
from app.schemas.agent_snapshot import (
    PrepararSnapshotIn,
    SnapshotOut,
    SnapshotPreparadoOut,
)
from app.use_cases.analysis.payload_do_agente import PayloadDoAgenteError

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
