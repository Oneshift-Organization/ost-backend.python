"""`PgAccountRepository.delete_user_data` contra Postgres real.

O que protege: tudo do usuário some, inclusive o que cai por cascata, e os dados
de outro usuário ficam intactos.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.domain.entities.agent_snapshot import AgentSnapshot, AgentSubmission, agora
from app.domain.entities.analysis_job import AnalysisJob
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_account_repo import PgAccountRepository
from app.infrastructure.repositories.pg_agent_snapshot_repo import (
    PgAgentSnapshotRepository,
    PgAgentSubmissionRepository,
)
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository

from .conftest import FIXTURES, _cleanup, _ingest

_TABELAS = (
    "billing_reports",
    "billing_items",
    "billing_enriched",
    "analysis_jobs",
    "agent_snapshots",
    "agent_submissions",
)


async def _criar_dados(user_id: UUID) -> UUID:
    report_id = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=user_id)
    async with get_sessionmaker()() as s:
        await PgAnalysisJobRepository(s).create(
            AnalysisJob(job_id=uuid4(), user_id=user_id, report_id=report_id)
        )
        # Snapshot e envio do agente caem por cascata do billing_reports.
        snapshot, _ = await PgAgentSnapshotRepository(s).gravar_ou_reaproveitar(
            AgentSnapshot(
                user_id=user_id,
                report_id=report_id,
                inventory_id=None,
                inventory_marker=None,
                fingerprint="a" * 64,
                payload_fingerprint="b" * 64,
                versions={},
                payload={},
                expires_at=agora() + timedelta(hours=1),
            )
        )
        await PgAgentSubmissionRepository(s).gravar(
            AgentSubmission(
                snapshot_id=snapshot.id,
                user_id=user_id,
                report_id=report_id,
                request_hash="c" * 64,
                revision=1,
                agent_raw={},
                analysis={},
                campos_sobrescritos=0,
            )
        )
        await s.commit()
    return report_id


async def _contar(user_id: UUID) -> dict[str, int]:
    async with get_sessionmaker()() as s:
        return {
            tabela: (
                await s.execute(
                    text(f"select count(*) from {tabela} where user_id = :u"), {"u": user_id}
                )
            ).scalar_one()
            for tabela in _TABELAS
        }


async def _limpar(user_id: UUID, report_id: UUID) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("delete from analysis_jobs where user_id = :u"), {"u": user_id})
        await s.commit()
    await _cleanup(report_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_apaga_tudo_do_usuario_e_so_dele(_db_ready) -> None:
    alvo, vizinho = uuid4(), uuid4()
    report_alvo = await _criar_dados(alvo)
    report_vizinho = await _criar_dados(vizinho)
    try:
        antes_vizinho = await _contar(vizinho)
        assert all((await _contar(alvo)).values())

        async with get_sessionmaker()() as s:
            await PgAccountRepository(s).delete_user_data(alvo)
            await s.commit()

        assert await _contar(alvo) == dict.fromkeys(_TABELAS, 0)
        assert await _contar(vizinho) == antes_vizinho
    finally:
        await _limpar(alvo, report_alvo)
        await _limpar(vizinho, report_vizinho)
