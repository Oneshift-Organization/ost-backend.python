"""Repositórios do snapshot do agente no Postgres (PLANO_SNAPSHOT_AGENTE).

As garantias de concorrência ficam no banco, não em consulta prévia:

- o reaproveitamento é um `INSERT … ON CONFLICT` sobre o índice único de
  conteúdo, então duas preparações simultâneas iguais dão um snapshot só;
- o envio trava a linha do snapshot (`SELECT … FOR UPDATE`) e as constraints
  únicas de conteúdo e de revisão são a segunda barreira.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.agent_snapshot import AgentSnapshot, AgentSubmission
from app.infrastructure.database.models.agent_snapshot import (
    AgentSnapshotModel,
    AgentSubmissionModel,
)

_CHAVE_DE_CONTEUDO = ["user_id", "report_id", "inventory_id", "fingerprint"]


class PgAgentSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def gravar_ou_reaproveitar(self, snapshot: AgentSnapshot) -> tuple[AgentSnapshot, bool]:
        novo = insert(AgentSnapshotModel).values(
            id=snapshot.id,
            user_id=snapshot.user_id,
            report_id=snapshot.report_id,
            inventory_id=snapshot.inventory_id,
            inventory_marker=snapshot.inventory_marker,
            fingerprint=snapshot.fingerprint,
            payload_fingerprint=snapshot.payload_fingerprint,
            versions=snapshot.versions,
            payload=snapshot.payload,
            pools_sem_equivalente=snapshot.pools_sem_equivalente,
            created_at=snapshot.created_at,
            expires_at=snapshot.expires_at,
        )
        # Conteúdo igual: renova a validade e o marcador do inventário; mantém o
        # resto (inclusive o id). O marcador precisa acompanhar: declarações
        # reenviadas iguais mudam a data mas não o conteúdo, e o marcador antigo
        # faria o envio recusar como desatualizado um snapshot que acabou de ser
        # confirmado.
        stmt = novo.on_conflict_do_update(
            index_elements=_CHAVE_DE_CONTEUDO,
            set_={
                "expires_at": novo.excluded.expires_at,
                "inventory_marker": novo.excluded.inventory_marker,
            },
        ).returning(AgentSnapshotModel)
        # `populate_existing`: se o snapshot já estiver na sessão, a validade
        # renovada tem de aparecer no objeto devolvido.
        resultado = await self._s.execute(stmt, execution_options={"populate_existing": True})
        modelo = resultado.scalar_one()
        await self._s.flush()
        return _snapshot(modelo), modelo.id != snapshot.id

    async def travar(self, snapshot_id: UUID, user_id: UUID) -> AgentSnapshot | None:
        stmt = (
            select(AgentSnapshotModel)
            .where(AgentSnapshotModel.id == snapshot_id, AgentSnapshotModel.user_id == user_id)
            .with_for_update()
        )
        modelo = (await self._s.execute(stmt)).scalar_one_or_none()
        return _snapshot(modelo) if modelo else None

    async def expurgar(self, vencidos_antes_de: datetime) -> int:
        com_envio = exists().where(AgentSubmissionModel.snapshot_id == AgentSnapshotModel.id)
        resultado = await self._s.execute(
            delete(AgentSnapshotModel).where(
                AgentSnapshotModel.expires_at < vencidos_antes_de, ~com_envio
            )
        )
        return int(getattr(resultado, "rowcount", 0) or 0)


class PgAgentSubmissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def por_conteudo(self, snapshot_id: UUID, request_hash: str) -> AgentSubmission | None:
        stmt = select(AgentSubmissionModel).where(
            AgentSubmissionModel.snapshot_id == snapshot_id,
            AgentSubmissionModel.request_hash == request_hash,
        )
        modelo = (await self._s.execute(stmt)).scalar_one_or_none()
        return _envio(modelo) if modelo else None

    async def proxima_revisao(self, snapshot_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(AgentSubmissionModel.revision), 0)).where(
            AgentSubmissionModel.snapshot_id == snapshot_id
        )
        return int((await self._s.execute(stmt)).scalar_one()) + 1

    async def gravar(self, envio: AgentSubmission) -> None:
        self._s.add(
            AgentSubmissionModel(
                id=envio.id,
                snapshot_id=envio.snapshot_id,
                user_id=envio.user_id,
                report_id=envio.report_id,
                request_hash=envio.request_hash,
                revision=envio.revision,
                agent_raw=envio.agent_raw,
                analysis=envio.analysis,
                campos_sobrescritos=envio.campos_sobrescritos,
                created_at=envio.created_at,
            )
        )
        await self._s.flush()

    async def obter(self, submission_id: UUID, user_id: UUID) -> AgentSubmission | None:
        stmt = select(AgentSubmissionModel).where(
            AgentSubmissionModel.id == submission_id, AgentSubmissionModel.user_id == user_id
        )
        modelo = (await self._s.execute(stmt)).scalar_one_or_none()
        return _envio(modelo) if modelo else None

    async def listar(
        self,
        user_id: UUID,
        *,
        report_id: UUID | None = None,
        snapshot_id: UUID | None = None,
        limite: int = 20,
    ) -> list[AgentSubmission]:
        stmt = select(AgentSubmissionModel).where(AgentSubmissionModel.user_id == user_id)
        if report_id is not None:
            stmt = stmt.where(AgentSubmissionModel.report_id == report_id)
        if snapshot_id is not None:
            stmt = stmt.where(AgentSubmissionModel.snapshot_id == snapshot_id)
        stmt = stmt.order_by(
            AgentSubmissionModel.created_at.desc(), AgentSubmissionModel.revision.desc()
        ).limit(limite)
        return [_envio(m) for m in (await self._s.execute(stmt)).scalars()]


def _snapshot(m: AgentSnapshotModel) -> AgentSnapshot:
    return AgentSnapshot(
        id=m.id,
        user_id=m.user_id,
        report_id=m.report_id,
        inventory_id=m.inventory_id,
        inventory_marker=m.inventory_marker,
        fingerprint=m.fingerprint,
        payload_fingerprint=m.payload_fingerprint,
        versions=dict(m.versions or {}),
        payload=dict(m.payload or {}),
        pools_sem_equivalente=list(m.pools_sem_equivalente or []),
        created_at=m.created_at,
        expires_at=m.expires_at,
    )


def _envio(m: AgentSubmissionModel) -> AgentSubmission:
    return AgentSubmission(
        id=m.id,
        snapshot_id=m.snapshot_id,
        user_id=m.user_id,
        report_id=m.report_id,
        request_hash=m.request_hash,
        revision=m.revision,
        agent_raw=dict(m.agent_raw or {}),
        analysis=dict(m.analysis or {}),
        campos_sobrescritos=m.campos_sobrescritos,
        created_at=m.created_at,
    )
