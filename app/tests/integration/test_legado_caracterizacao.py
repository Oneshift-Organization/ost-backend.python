"""TRAVA DO LEGADO — ponto de fixação do fluxo `Frontend → API → LLM`.

Estes testes **não julgam se o comportamento é bom. Registram qual é.**

É um *characterization test*: uma fotografia do caminho legado, tirada antes de
construirmos o fluxo MCP+Agente ao lado. Se qualquer bloco novo esbarrar aqui, a
suíte fica vermelha em vez de o problema aparecer em produção.

Por que isto existe agora: são ~1.444 linhas entre `api/v1/routes/analysis.py` e
`use_cases/analysis/run_analysis_pipeline.py`, e nenhum teste exercitava
`POST /analyses/start` nem `agents-payload`. A regra "não tocar no que funciona"
era fé, não garantia.

**Se um teste daqui quebrar**, a pergunta certa não é "como faço passar" — é
"eu queria mesmo mudar o contrato do legado?". Se a resposta for não, o defeito
está na mudança nova. Se for sim, a mudança é deliberada e o teste se atualiza
junto, no mesmo commit, com o porquê na mensagem.

**Comportamento estranho é gravado como está.** `viability_score` em escala
0..1000 enquanto os demais são 0..100 é esquisito, e continua aqui exatamente
assim. Consertar seria tocar no que funciona.

O LLM roda como `mock` (`LLM_PROVIDER=mock`) — determinístico, sem gastar token.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.core import config
from app.domain.entities.analysis_job import AnalysisJob
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.main import create_app

from .conftest import FIXTURES, _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)


@pytest.fixture(scope="module")
def _app():
    return create_app()


@pytest.fixture
def _llm_mock(monkeypatch: pytest.MonkeyPatch):
    """Força o provider `mock`: o pipeline roda inteiro, sem rede e sem custo."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


async def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ── POST /v1/analyses/start/{report_id} ─────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_start_devolve_202_com_job(_app, _db_ready, _llm_mock) -> None:
    """Contrato de disparo: 202 + job identificável, para o front poder pollar."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        async with await _cliente(_app) as c:
            r = await c.post(f"/v1/analyses/start/{rid}")

        assert r.status_code == 202
        corpo = r.json()
        # As chaves que o frontend lê hoje. Remover qualquer uma quebra a tela.
        assert {"job_id", "report_id", "status", "created_at"} <= set(corpo)
        assert corpo["report_id"] == str(rid)
        UUID(corpo["job_id"])  # é UUID válido
    finally:
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_start_aceita_external_id(_app, _db_ready, _llm_mock) -> None:
    """`external_id` é como um cliente externo correlaciona a análise dele."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    ext = uuid4()
    try:
        async with await _cliente(_app) as c:
            r = await c.post(f"/v1/analyses/start/{rid}?external_id={ext}")
        assert r.status_code == 202
        assert r.json()["external_id"] == str(ext)
    finally:
        await _cleanup(rid)


# ── GET /v1/analyses/{job_id}/result ────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_result_de_job_inexistente_e_404(_app, _db_ready) -> None:
    """404 com `detail` em TEXTO LIVRE.

    Diferente das rotas novas, que usam envelope {codigo, mensagem, retryable}.
    Gravado como está: o legado nunca teve o envelope, e mudá-lo agora quebraria
    quem já trata a string.
    """
    async with await _cliente(_app) as c:
        r = await c.get(f"/v1/analyses/{uuid4()}/result")
    assert r.status_code == 404
    assert r.json()["detail"] == "analysis job not found"


@pytest.mark.asyncio(loop_scope="session")
async def test_result_traz_o_shape_que_o_front_consome(_app, _db_ready) -> None:
    """Job recém-criado, ainda sem resultado: os campos existem e vêm nulos.

    O front distingue "processando" de "pronto" por `status` + `result is None`.
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()

        async with await _cliente(_app) as c:
            r = await c.get(f"/v1/analyses/{job.job_id}/result")

        assert r.status_code == 200
        corpo = r.json()
        assert set(corpo) == {
            "job_id",
            "report_id",
            "external_id",
            "status",
            "llm_output",
            "error",
            "result",
            "llm_input_payload",
            "created_at",
            # Aditivo (PLANO_CENARIOS_DE_ENTRADA): o que os arquivos permitem
            "diagnostico_de_entrada",
        }
        assert corpo["status"] == "PENDING"
        assert corpo["result"] is None
        assert corpo["llm_output"] is None
    finally:
        await _cleanup(rid)


# ── pipeline completo com LLM mockado ───────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_pipeline_completo_chega_em_completed(_app, _db_ready, _llm_mock) -> None:
    """O fluxo legado inteiro, ponta a ponta.

    Sob `ASGITransport` a BackgroundTask roda até o fim antes de o cliente
    receber a resposta — então ao voltar do POST a análise já terminou. É o que
    permite verificar o resultado sem polling.
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        async with await _cliente(_app) as c:
            job_id = (await c.post(f"/v1/analyses/start/{rid}")).json()["job_id"]
            r = await c.get(f"/v1/analyses/{job_id}/result")

        corpo = r.json()
        assert corpo["status"] == "COMPLETED", corpo.get("error")
        assert corpo["result"] is not None
        assert corpo["llm_input_payload"] is not None, (
            "o payload enviado ao LLM é persistido — é o que /request devolve depois"
        )
    finally:
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_autoridade_do_backend_sobrescreve_os_3_campos(
    _app, _db_ready, _llm_mock
) -> None:
    """FIXA O ESCOPO ATUAL da autoridade: 3 campos, nem mais nem menos.

    Este é o teste mais importante do arquivo. O plano da API v2 vai estender a
    autoridade para ~22 campos, mas em módulo NOVO — o legado tem que continuar
    com exatamente estes três. Se esta lista mudar sem intenção, a estratégia de
    'construir ao lado' vazou para dentro do legado.
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        async with await _cliente(_app) as c:
            job_id = (await c.post(f"/v1/analyses/start/{rid}")).json()["job_id"]
            res = (await c.get(f"/v1/analyses/{job_id}/result")).json()

        resultado = res["result"]
        payload = res["llm_input_payload"]
        resumo = payload["resumo_executivo"]

        # 1. provedor recomendado vem do backend
        assert resultado["recommended_provider"].lower() == str(resumo["recomendado"]).lower()

        # 2. custo e economia por provedor batem com o cálculo determinístico
        do_backend = {p["provedor"].lower(): p for p in resumo["por_provedor"]}
        for pc in resultado["provider_comparison"]:
            verdade = do_backend[pc["provider"].lower()]
            assert pc["estimated_monthly_cost"] == pytest.approx(
                float(verdade["custo_estimado_total"])
            )
            assert pc["estimated_monthly_savings"] == pytest.approx(
                float(verdade["economia_mensal"])
            )

        # 3. economia total é a do provedor recomendado
        recomendado = do_backend[str(resumo["recomendado"]).lower()]
        assert resultado["savings_summary"]["total_monthly_savings_usd"] == pytest.approx(
            float(recomendado["economia_mensal"])
        )
    finally:
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_campos_hoje_sem_autoridade_seguem_livres(
    _app, _db_ready, _llm_mock
) -> None:
    """GRAVA O BURACO, não o conserta.

    `viability_score` (escala 0..1000, inconsistente com os demais 0..100) e
    `payback_period_months` (sem entrada que permita calcular) passam sem
    nenhuma conferência do backend. É exatamente o que o plano v2 corrige — em
    módulo novo. Aqui, apenas registramos que hoje é assim.
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        async with await _cliente(_app) as c:
            job_id = (await c.post(f"/v1/analyses/start/{rid}")).json()["job_id"]
            resultado = (await c.get(f"/v1/analyses/{job_id}/result")).json()["result"]

        # A escala continua 0..1000 no legado.
        assert 0 <= resultado["viability_score"] <= 1000
        # O campo existe e ninguém o confere.
        assert "payback_period_months" in resultado["savings_summary"]
    finally:
        await _cleanup(rid)


# ── GET /v1/analyses/{job_id}/agents-payload ────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_agents_payload_mantem_o_contrato(_app, _db_ready) -> None:
    """Endpoint de inspeção que o front usa.

    Recebe **report_id** no lugar chamado `job_id` — o mesmo tropeço de
    nomenclatura documentado para o time do MCP. Gravado como está.
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        async with await _cliente(_app) as c:
            r = await c.get(f"/v1/analyses/{rid}/agents-payload")

        assert r.status_code == 200
        assert "workloads" in r.json()
    finally:
        await _cleanup(rid)


# ── isolamento por usuário ──────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_job_de_outro_usuario_some(_app, _db_ready) -> None:
    """404 esconde existência — invariante de segurança, não detalhe.

    Vale o mesmo para o fluxo novo: o agente não pode enxergar report alheio.
    """
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    outro = uuid4()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            alheio = AnalysisJob(job_id=uuid4(), user_id=outro, report_id=rid)
            await PgAnalysisJobRepository(s).create(alheio)
            await s.commit()

        async with await _cliente(_app) as c:
            r = await c.get(f"/v1/analyses/{alheio.job_id}/result")
        assert r.status_code == 404

        sm = get_sessionmaker()
        async with sm() as s:
            await s.execute(
                text("delete from analysis_jobs where job_id=:j"), {"j": alheio.job_id}
            )
            await s.commit()
    finally:
        await _cleanup(rid)
