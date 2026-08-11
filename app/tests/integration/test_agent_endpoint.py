"""Superfície `/v1/agent/*` — o interruptor e o ciclo completo.

O que mais importa aqui é o **desligado**: com a flag `false`, as rotas
respondem 404 como se não existissem. É o que sustenta a promessa de que nada
muda em produção enquanto o fluxo novo não for ligado.
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


@pytest.fixture
def _app_ligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_FLOW_ENABLED", "true")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


@pytest.fixture
def _app_desligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_FLOW_ENABLED", "false")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


_ANALISE = {
    "recommendation": "migrar para Azure",
    "recommendation_rationale": "melhor rede",
    "provider_comparison": [
        {"provider": "oci", "estimated_monthly_cost": 1.0, "strengths": ["preço"]}
    ],
    "sugestoes": [
        {
            "tipo": "contestacao_preco",
            "severidade": "alta",
            "titulo": "preço de GCP parece defasado",
            "detalhe": "cerca de 40% abaixo do que conheço de tabela",
            "referencia": "GCP/n2-standard-8/us-central1",
        }
    ],
    "payback": {
        "meses": 7,
        "premissa_custo_migracao_usd": 18000,
        "base": "esforço estimado para 12 workloads",
    },
}


# ── o interruptor ───────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_desligado_a_rota_nao_existe(_app_desligado, _db_ready) -> None:
    """Com a flag `false` — o default — a superfície inteira responde 404.

    É o que sustenta 'nada muda em produção': o código pode estar deployado e
    continuar invisível.
    """
    async with _cliente(_app_desligado) as c:
        r = await c.post(f"/v1/agent/analyses/{uuid4()}/result", json=_ANALISE)
        g = await c.get(f"/v1/agent/analyses/{uuid4()}/result")

    for resp in (r, g):
        assert resp.status_code == 404
        assert resp.json()["detail"]["codigo"] == "FLUXO_AGENTE_DESLIGADO"
        assert resp.json()["detail"]["retryable"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_o_legado_nao_muda_com_a_flag_ligada(_app_ligado, _db_ready) -> None:
    """Ligar o fluxo novo NÃO desliga o antigo — os dois convivem."""
    async with _cliente(_app_ligado) as c:
        r = await c.get(f"/v1/analyses/{uuid4()}/result")
    assert r.status_code == 404
    assert r.json()["detail"] == "analysis job not found", "contrato legado intacto"


# ── ciclo completo ──────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_sem_payload_pede_para_re_tentar(_app_ligado, _db_ready) -> None:
    """409 `retryable: true` — o agente precisa buscar a request antes."""
    async with _cliente(_app_ligado) as c:
        r = await c.post(f"/v1/agent/analyses/{uuid4()}/result", json=_ANALISE)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["codigo"] == "PAYLOAD_AUSENTE"
    assert d["retryable"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_recebe_corrige_e_devolve(_app_ligado, _db_ready) -> None:
    """O ciclo inteiro: o backend corrige os números, aceita o julgamento, e
    a contestação rebaixa a confiança sem trocar valor."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        # A request precisa existir — é ela que serve de verdade.
        async with _cliente(_app_ligado) as c:
            req = await c.get(f"/v1/analyses/{rid}/request?recalcular=true")
            assert req.status_code == 200, req.text
            payload = {k: v for k, v in req.json().items() if k != "_meta"}

        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(
                job_id=uuid4(),
                user_id=HTTP_USER,
                report_id=rid,
                llm_input_payload=payload,
            )
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()

        async with _cliente(_app_ligado) as c:
            r = await c.post(f"/v1/agent/analyses/{rid}/result", json=_ANALISE)

        assert r.status_code == 200, r.text
        corpo = r.json()

        # julgamento preservado
        assert corpo["recommendation"] == "migrar para Azure"
        # contestação processada, com efeito do BACKEND
        assert corpo["sugestoes"][0]["efeito_backend"] == "confianca_rebaixada"
        # e o número contestado não mudou
        assert corpo["campos_sobrescritos"] >= 1

        # persistiu bruto e corrigido lado a lado
        async with sm() as s:
            row = (
                await s.execute(
                    text(
                        "select agent_raw_output is not null, ai_structured_output is not null,"
                        " sugestoes is not null from analysis_jobs where job_id=:j"
                    ),
                    {"j": job.job_id},
                )
            ).first()
        assert row == (True, True, True), "bruto, corrigido e sugestões persistidos"

        # e o GET devolve o corrigido
        async with _cliente(_app_ligado) as c:
            g = await c.get(f"/v1/agent/analyses/{rid}/result")
        assert g.status_code == 200
        assert g.json()["recommendation"] == "migrar para Azure"
    finally:
        sm = get_sessionmaker()
        async with sm() as s:
            await s.execute(
                text("delete from analysis_jobs where report_id=:r"), {"r": rid}
            )
            await s.commit()
        await _cleanup(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_payback_sem_premissa_e_recusado(_app_ligado, _db_ready) -> None:
    """A premissa é obrigatória no schema: número sem base declarada não entra.

    É o padrão para tudo que o backend não sabe calcular — não proibir, exigir a
    premissa e marcar o tipo.
    """
    invalido = {**_ANALISE, "payback": {"meses": 7, "base": "chute"}}
    async with _cliente(_app_ligado) as c:
        r = await c.post(f"/v1/agent/analyses/{uuid4()}/result", json=invalido)
    assert r.status_code == 422, "premissa_custo_migracao_usd faltando"
