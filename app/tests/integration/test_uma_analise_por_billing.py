"""Uma análise por billing na lista (anexar inventário não duplica).

Anexar inventário, ou reanalisar, cria um job novo do mesmo billing. A lista
mostrava os dois, e o usuário não sabia qual valia. Agora o job novo é uma
revisão: a lista mostra a vigente, a reanálise em curso aparece como estado
dela, e excluir vale para o billing inteiro.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.domain.entities.analysis_job import AnalysisJob, AnalysisStatus, agora_utc
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.main import create_app

from .conftest import FIXTURES, _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)


@pytest.fixture
def _app():
    return create_app()


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _revisao(report_id, status: AnalysisStatus, minutos: int, erro: str | None = None):
    sm = get_sessionmaker()
    async with sm() as s:
        job = AnalysisJob(
            job_id=uuid4(),
            user_id=HTTP_USER,
            report_id=report_id,
            llm_input_payload={"provedor_origem": "aws"},
        )
        job.status = status
        job.error = erro
        job.created_at = agora_utc() + timedelta(minutes=minutos)
        await PgAnalysisJobRepository(s).create(job)
        await s.commit()
    return job


async def _limpar(report_id) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(text("delete from analysis_jobs where report_id=:r"), {"r": report_id})
        await s.commit()
    await _cleanup(report_id)


def _do_billing(listagem, report_id) -> list[dict]:
    return [a for a in listagem.json() if a["report_id"] == str(report_id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_anexar_inventario_nao_duplica_a_analise(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        await _revisao(rid, AnalysisStatus.COMPLETED, 0)
        com_inventario = await _revisao(rid, AnalysisStatus.COMPLETED, 5)
        async with _cliente(_app) as c:
            linhas = _do_billing(await c.get("/v1/analyses"), rid)
        assert [a["job_id"] for a in linhas] == [str(com_inventario.job_id)]
        assert linhas[0]["reanalise_status"] is None
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_reanalise_que_falhou_aparece_como_estado_da_vigente(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        vigente = await _revisao(rid, AnalysisStatus.COMPLETED, 0)
        falhou = await _revisao(rid, AnalysisStatus.FAILED, 5, erro="TIMEOUT")
        async with _cliente(_app) as c:
            linhas = _do_billing(await c.get("/v1/analyses"), rid)
        assert [a["job_id"] for a in linhas] == [str(vigente.job_id)]
        # O front acompanha a revisão nova por este id (vigia de avisos, adoção do fluxo)
        assert linhas[0]["reanalise_job_id"] == str(falhou.job_id)
        assert linhas[0]["reanalise_status"] == "FAILED"
        assert linhas[0]["reanalise_erro"] == "TIMEOUT"
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_excluir_vale_para_o_billing_inteiro(_app, _db_ready) -> None:
    """Senão, excluir a vigente faria a revisão anterior voltar à lista."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        await _revisao(rid, AnalysisStatus.COMPLETED, 0)
        vigente = await _revisao(rid, AnalysisStatus.COMPLETED, 5)
        async with _cliente(_app) as c:
            r = await c.put(f"/v1/analyses/{vigente.job_id}/disp-status", json={"disp_status": 0})
            assert r.status_code == 200, r.text
            assert r.json()["disp_status"] == 0
            assert _do_billing(await c.get("/v1/analyses"), rid) == []
            await c.put(f"/v1/analyses/{vigente.job_id}/disp-status", json={"disp_status": 1})
            linhas = _do_billing(await c.get("/v1/analyses"), rid)
        assert [a["job_id"] for a in linhas] == [str(vigente.job_id)]
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_link_da_revisao_antiga_continua_abrindo(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        antiga = await _revisao(rid, AnalysisStatus.COMPLETED, 0)
        await _revisao(rid, AnalysisStatus.COMPLETED, 5)
        async with _cliente(_app) as c:
            r = await c.get(f"/v1/analyses/{antiga.job_id}/result")
        assert r.status_code == 200, r.text
    finally:
        await _limpar(rid)
