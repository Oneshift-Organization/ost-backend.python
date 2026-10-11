"""`PgAccountRepository.delete_user_data` contra Postgres real.

O que protege: tudo do usuário some, inclusive o que cai por cascata, e os dados
de outro usuário ficam intactos.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.domain.entities.analysis_job import AnalysisJob
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_account_repo import PgAccountRepository
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository

from .conftest import FIXTURES, _cleanup, _ingest

_TABELAS = ("billing_reports", "billing_items", "billing_enriched", "analysis_jobs")


async def _criar_dados(user_id: UUID) -> UUID:
    report_id = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=user_id)
    async with get_sessionmaker()() as s:
        await PgAnalysisJobRepository(s).create(
            AnalysisJob(job_id=uuid4(), user_id=user_id, report_id=report_id)
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
