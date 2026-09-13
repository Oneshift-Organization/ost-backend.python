"""`PUT /v1/analyses/{job_id}/disp-status` — a exclusão que não apaga.

O front pediu um DELETE. Um DELETE levaria junto o `llm_input_payload`, que é a
fonte do Terraform e de qualquer reanálise: quem excluísse por engano perderia o
pacote que já baixou, sem volta.

O que estes testes protegem: a análise some da listagem (é o que o usuário vê),
o registro continua no banco (é o que permite desfazer), e ninguém mexe na
análise de outro usuário.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.domain.entities.analysis_job import AnalysisJob, AnalysisStatus
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


async def _analise(report_id, user_id=HTTP_USER) -> AnalysisJob:
    sm = get_sessionmaker()
    async with sm() as s:
        job = AnalysisJob(
            job_id=uuid4(),
            user_id=user_id,
            report_id=report_id,
            llm_input_payload={"provedor_origem": "aws"},
        )
        job.status = AnalysisStatus.COMPLETED
        await PgAnalysisJobRepository(s).create(job)
        await s.commit()
    return job


async def _limpar(report_id) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text("delete from analysis_jobs where report_id=:r"), {"r": report_id}
        )
        await s.commit()
    await _cleanup(report_id)


# ── o ciclo ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_depreciar_some_da_listagem_mas_nao_do_banco(_app, _db_ready) -> None:
    """As duas metades do contrato, no mesmo teste: some da lista (o usuário vê
    exclusão) e continua na tabela (dá para desfazer)."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise(rid)

        async with _cliente(_app) as c:
            antes = await c.get("/v1/analyses")
            assert any(a["job_id"] == str(job.job_id) for a in antes.json())

            r = await c.put(
                f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 0}
            )
            assert r.status_code == 200, r.text
            assert r.json()["disp_status"] == 0
            assert r.json()["disp_status_updated_at"] is not None

            depois = await c.get("/v1/analyses")
            assert not any(a["job_id"] == str(job.job_id) for a in depois.json())

        sm = get_sessionmaker()
        async with sm() as s:
            linha = (
                await s.execute(
                    text(
                        "select disp_status, llm_input_payload is not null "
                        "from analysis_jobs where job_id=:j"
                    ),
                    {"j": job.job_id},
                )
            ).first()
        assert linha == (0, True), "o payload determinístico não pode ser perdido"
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_reativar_traz_de_volta(_app, _db_ready) -> None:
    """É isto que um DELETE não permitiria."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise(rid)
        async with _cliente(_app) as c:
            await c.put(f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 0})
            await c.put(f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 1})
            listagem = await c.get("/v1/analyses")
        assert any(a["job_id"] == str(job.job_id) for a in listagem.json())
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_lixeira_lista_as_depreciadas(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise(rid)
        async with _cliente(_app) as c:
            await c.put(f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 0})
            todas = await c.get("/v1/analyses?incluir_depreciadas=true")
        assert any(a["job_id"] == str(job.job_id) for a in todas.json())
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_repetir_o_mesmo_valor_e_idempotente(_app, _db_ready) -> None:
    """O front pode reenviar num retry — não pode virar erro."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise(rid)
        async with _cliente(_app) as c:
            a = await c.put(f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 0})
            b = await c.put(f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 0})
        assert a.status_code == b.status_code == 200
    finally:
        await _limpar(rid)


# ── isolamento e validação ──────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_analise_de_outro_usuario_e_404(_app, _db_ready) -> None:
    """403 confirmaria que a análise existe."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise(rid, user_id=uuid4())
        async with _cliente(_app) as c:
            r = await c.put(
                f"/v1/analyses/{job.job_id}/disp-status", json={"disp_status": 0}
            )
        assert r.status_code == 404
        assert r.json()["detail"]["codigo"] == "ANALISE_NAO_ENCONTRADA"
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_valor_fora_do_dominio_e_recusado(_app, _db_ready) -> None:
    async with _cliente(_app) as c:
        for corpo in ({"disp_status": 2}, {"disp_status": "ativa"}, {}):
            r = await c.put(f"/v1/analyses/{uuid4()}/disp-status", json=corpo)
            assert r.status_code == 422, corpo


@pytest.mark.asyncio(loop_scope="session")
async def test_campo_desconhecido_e_recusado(_app, _db_ready) -> None:
    """`extra="forbid"`: esta rota muda visibilidade e nada mais. Aceitar campo
    extra abriria caminho para o front editar resultado de análise."""
    async with _cliente(_app) as c:
        r = await c.put(
            f"/v1/analyses/{uuid4()}/disp-status",
            json={"disp_status": 0, "recommendation": "migrar para a lua"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_analises_existentes_nascem_ativas(_app, _db_ready) -> None:
    """A migration usa `server_default`, não default de aplicação: sem isso as
    análises já gravadas ficariam NULL e sumiriam todas da listagem."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise(rid)
        async with _cliente(_app) as c:
            listagem = await c.get("/v1/analyses")
        assert any(a["job_id"] == str(job.job_id) for a in listagem.json())
    finally:
        await _limpar(rid)
