"""Nome do billing dado pelo usuário (M9): `PUT /v1/billing-files/{job_id}/alias`.

A tela mostra `nome_exibicao`: o alias, ou o nome do arquivo sem ele. O alias vale
para a linha do billing na lista de análises, e a ingestão não o apaga.
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
from app.infrastructure.repositories.pg_billing_report_repo import PgBillingReportRepository
from app.main import create_app

from .conftest import FIXTURES, _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)
_ARQUIVO = "aws_cost_explorer_full.csv"


@pytest.fixture
def _app():
    return create_app()


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _limpar(report_id) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(text("delete from analysis_jobs where report_id=:r"), {"r": report_id})
        await s.commit()
    await _cleanup(report_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_renomear_e_voltar_ao_nome_do_arquivo(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / _ARQUIVO, user_id=HTTP_USER)
    try:
        async with _cliente(_app) as c:
            antes = (await c.get(f"/v1/billing-files/{rid}")).json()
            r = await c.put(f"/v1/billing-files/{rid}/alias", json={"alias": "  Fatura   março "})
            status_ = (await c.get(f"/v1/billing-files/{rid}")).json()
            lista = (await c.get("/v1/billing-files")).json()
            volta = await c.put(f"/v1/billing-files/{rid}/alias", json={"alias": None})
        assert antes["alias"] is None
        assert antes["nome_exibicao"] == antes["source_filename"]
        assert r.status_code == 200
        assert (r.json()["alias"], r.json()["nome_exibicao"]) == ("Fatura março", "Fatura março")
        assert status_["nome_exibicao"] == "Fatura março"
        [item] = [b for b in lista if b["job_id"] == str(rid)]
        assert item["nome_exibicao"] == "Fatura março"
        assert item["source_filename"] == antes["source_filename"]
        assert volta.json()["alias"] is None
        assert volta.json()["nome_exibicao"] == antes["source_filename"]
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_billing_de_outro_usuario_e_404_e_nome_invalido_e_422(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / _ARQUIVO, user_id=uuid4())
    meu = await _ingest(FIXTURES / _ARQUIVO, user_id=HTTP_USER)
    try:
        async with _cliente(_app) as c:
            alheio = await c.put(f"/v1/billing-files/{rid}/alias", json={"alias": "meu"})
            inexistente = await c.put(f"/v1/billing-files/{uuid4()}/alias", json={"alias": "x"})
            longo = await c.put(f"/v1/billing-files/{meu}/alias", json={"alias": "x" * 121})
            sem_corpo = await c.put(f"/v1/billing-files/{meu}/alias", json={})
        assert (alheio.status_code, inexistente.status_code) == (404, 404)
        assert (longo.status_code, sem_corpo.status_code) == (422, 422)
        sm = get_sessionmaker()
        async with sm() as s:
            alvo = await PgBillingReportRepository(s).get_by_id(rid, uuid4())
            assert alvo is None
    finally:
        await _limpar(rid)
        await _limpar(meu)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_lista_de_analises_mostra_o_alias_e_a_ingestao_nao_o_apaga(_app, _db_ready) -> None:
    rid = await _ingest(FIXTURES / _ARQUIVO, user_id=HTTP_USER)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(
                job_id=uuid4(),
                user_id=HTTP_USER,
                report_id=rid,
                llm_input_payload={"provedor_origem": "aws"},
            )
            job.status = AnalysisStatus.COMPLETED
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()
        async with sm() as s:
            # A ingestão leu o billing antes da renomeação e grava o estado dela depois
            repo = PgBillingReportRepository(s)
            lido = await repo.get_by_id(rid, HTTP_USER)
            assert lido is not None
            await repo.rename(rid, HTTP_USER, "Produção AWS")
            await repo.update(lido)
            await s.commit()
        async with _cliente(_app) as c:
            analises = (await c.get("/v1/analyses")).json()
        [linha] = [a for a in analises if a["report_id"] == str(rid)]
        assert (linha["alias"], linha["nome_exibicao"]) == ("Produção AWS", "Produção AWS")
    finally:
        await _limpar(rid)
