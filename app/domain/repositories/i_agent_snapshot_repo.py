"""Contratos dos repositórios do snapshot do agente.

Toda consulta filtra por ``user_id``: snapshot ou envio de outro usuário é
tratado como inexistente.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.entities.agent_snapshot import AgentSnapshot, AgentSubmission


class IAgentSnapshotRepository(Protocol):
    async def gravar_ou_reaproveitar(self, snapshot: AgentSnapshot) -> tuple[AgentSnapshot, bool]:
        """Grava, ou devolve o existente com o mesmo conteúdo com a validade
        renovada. O segundo valor diz se foi reaproveitado. Garantido no banco:
        duas preparações simultâneas iguais dão um snapshot só."""
        ...

    async def obter(self, snapshot_id: UUID, user_id: UUID) -> AgentSnapshot | None: ...

    async def travar(self, snapshot_id: UUID, user_id: UUID) -> AgentSnapshot | None:
        """Lê com lock de linha até o fim da transação (`SELECT … FOR UPDATE`)."""
        ...

    async def expurgar(self, vencidos_antes_de: datetime) -> int:
        """Apaga os vencidos antes do instante **que não têm envio**."""
        ...


class IAgentSubmissionRepository(Protocol):
    async def por_conteudo(
        self, snapshot_id: UUID, request_hash: str
    ) -> AgentSubmission | None: ...

    async def proxima_revisao(self, snapshot_id: UUID) -> int: ...

    async def gravar(self, envio: AgentSubmission) -> None: ...

    async def obter(self, submission_id: UUID, user_id: UUID) -> AgentSubmission | None: ...

    async def listar(
        self,
        user_id: UUID,
        *,
        report_id: UUID | None = None,
        snapshot_id: UUID | None = None,
        limite: int = 20,
    ) -> list[AgentSubmission]:
        """Do mais recente para o mais antigo."""
        ...
