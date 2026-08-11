"""Contrato HTTP de `GET /v1/analyses/{report_id}/request`.

É a superfície que um agente/MCP consome. Antes disto, `api/` tinha 0% de
cobertura: os gates exercitavam a pipeline por dentro, nenhum cruzava o HTTP.

O que cada teste trava:
- o payload sai no CORPO (não aninhado), com metadados em `_meta`;
- os erros distinguem RE-TENTE de DESISTA — um agente não consegue decidir isso
  a partir de um 422 com texto livre;
- o conteúdo é o mesmo que a pipeline manda ao modelo.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.domain.entities.billing_report import BillingReport
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.main import create_app
from app.use_cases.analysis.run_analysis_pipeline import (
    build_pipeline_llm_input_payload,
)

from .conftest import FIXTURES, _cleanup, _ingest

# Com AUTH_ENABLED=false a API atende como o usuário de dev fixo. Os dados destes
# testes precisam pertencer a ELE — o isolamento por user_id é justamente o que
# faria o report "sumir" se fosse criado sob outro dono.
HTTP_USER = UUID(_DEV_USER.sub)

# Todos os testes deste módulo compartilham o loop de sessão — é onde o pool de
# conexões do SQLAlchemy nasce (ver conftest). Rodar em loop de função faria a
# conexão ser usada por um loop diferente do que a criou.


@pytest.fixture(scope="module")
def _app():
    return create_app()


async def _get(app, url: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(url)


@pytest.mark.asyncio(loop_scope="session")
async def test_request_devolve_payload_no_corpo(_app, aws_context) -> None:
    """O payload é o corpo da resposta; metadados ficam em `_meta`."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        r = await _get(_app, f"/v1/analyses/{rid}/request?recalcular=true")
        assert r.status_code == 200, r.text
        body = r.json()
        # chaves de domínio no topo — não aninhadas sob "billing_data"
        assert {"pools_computacao", "servicos", "premissas", "resumo_executivo"} <= set(body)
        assert body["_meta"]["origem"] == "recalculada"
        assert body["_meta"]["report_id"] == str(rid)
    finally:
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_conteudo_bate_com_o_que_vai_ao_modelo(_app, aws_context) -> None:
    """A tool e a pipeline entregam a MESMA verdade — senão o agente veria uma
    coisa e o LLM outra."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        r = await _get(_app, f"/v1/analyses/{rid}/request?recalcular=true")
        body = {k: v for k, v in r.json().items() if k != "_meta"}
        esperado, _ = build_pipeline_llm_input_payload(aws_context)
        assert set(body) == set(esperado)
        assert body["gasto_total"] == esperado["gasto_total"]
        assert (
            body["resumo_executivo"]["recomendado"]
            == esperado["resumo_executivo"]["recomendado"]
        )
    finally:
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_report_inexistente_e_terminal(_app, _db_ready) -> None:
    """404 com `retryable: false` — o agente desiste em vez de ficar em loop."""
    r = await _get(_app, f"/v1/analyses/{uuid4()}/request")
    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["codigo"] == "REPORT_NAO_ENCONTRADO"
    assert d["retryable"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_ingestao_em_andamento_e_retryable(_app, _db_ready) -> None:
    """409 com `retryable: true` + status atual — o agente sabe que deve
    voltar a perguntar, em vez de tratar como falha."""
    sm = get_sessionmaker()
    async with sm() as s:
        rep = BillingReport(user_id=HTTP_USER, source_filename="em_andamento.csv")
        await PgBillingReportRepository(s).save(rep)  # nasce 'queued'
        await s.commit()
    try:
        r = await _get(_app, f"/v1/analyses/{rep.id}/request")
        assert r.status_code == 409
        d = r.json()["detail"]
        assert d["codigo"] == "REPORT_NAO_PRONTO"
        assert d["retryable"] is True
        assert d["status_report"] == "queued"
    finally:
        async with sm() as s:
            await s.execute(
                text("delete from billing_reports where id = :r"), {"r": rep.id}
            )
            await s.commit()


# ── report_id aditivo ───────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_billing_files_expoe_report_id(_app, _db_ready) -> None:
    """`job_id` e `report_id` são o mesmo valor — e é o `report_id` que o
    endpoint de análise espera.

    O nome `job_id` é herança do schema: quem integra lê `job_id`, procura onde
    passá-lo, e `/analyses/{report_id}/request` pede outro nome. Se tentar o
    `job_id` de uma *análise* leva 404, e nosso 404 é ambíguo de propósito —
    o cliente conclui "não existe" em vez de "chamei errado".
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        r = await _get(_app, "/v1/billing-files?limit=50")
        assert r.status_code == 200, r.text
        item = next(i for i in r.json() if i["job_id"] == str(rid))

        assert item["report_id"] == item["job_id"], "os dois têm que ser o mesmo id"
        assert item["report_id"] == str(rid)

        # E o id realmente serve no endpoint de análise — é o ponto todo.
        req = await _get(_app, f"/v1/analyses/{item['report_id']}/request?recalcular=true")
        assert req.status_code == 200, req.text
    finally:
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_job_id_continua_existindo(_app, _db_ready) -> None:
    """ADITIVO, não renomeação: quem lê `job_id` hoje não pode quebrar."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        detalhe = await _get(_app, f"/v1/billing-files/{rid}")
        assert detalhe.status_code == 200
        corpo = detalhe.json()
        assert corpo["job_id"] == str(rid)      # campo antigo, intacto
        assert corpo["report_id"] == str(rid)   # campo novo, mesmo valor
    finally:
        await _cleanup(rid)
