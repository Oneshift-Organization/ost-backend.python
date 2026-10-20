"""Consulta dos envios do agente: recuperar um resultado e ver as revisões."""

from __future__ import annotations

from uuid import UUID

from app.domain.entities.agent_snapshot import AgentSnapshot, AgentSubmission
from app.domain.repositories.i_agent_snapshot_repo import (
    IAgentSnapshotRepository,
    IAgentSubmissionRepository,
)

EnvioComSnapshot = tuple[AgentSubmission, AgentSnapshot]


class ConsultarEnviosUseCase:
    def __init__(
        self, *, snapshots: IAgentSnapshotRepository, envios: IAgentSubmissionRepository
    ) -> None:
        self._snapshots = snapshots
        self._envios = envios

    async def obter(self, user_id: UUID, submission_id: UUID) -> EnvioComSnapshot | None:
        envio = await self._envios.obter(submission_id, user_id)
        if envio is None:
            return None
        snapshot = await self._snapshots.obter(envio.snapshot_id, user_id)
        return (envio, snapshot) if snapshot is not None else None

    async def obter_snapshot(self, user_id: UUID, snapshot_id: UUID) -> AgentSnapshot | None:
        """Leitura isolada por usuário; não renova nem reinterpreta o snapshot."""
        return await self._snapshots.obter(snapshot_id, user_id)

    async def listar(
        self,
        user_id: UUID,
        *,
        report_id: UUID | None,
        snapshot_id: UUID | None,
        limite: int,
    ) -> list[EnvioComSnapshot]:
        envios = await self._envios.listar(
            user_id, report_id=report_id, snapshot_id=snapshot_id, limite=limite
        )
        snapshots: dict[UUID, AgentSnapshot | None] = {}
        for envio in envios:
            if envio.snapshot_id not in snapshots:
                snapshots[envio.snapshot_id] = await self._snapshots.obter(
                    envio.snapshot_id, user_id
                )
        return [
            (envio, snapshot)
            for envio in envios
            if (snapshot := snapshots[envio.snapshot_id]) is not None
        ]
