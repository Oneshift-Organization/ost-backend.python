"""Inventário dentro da análise (I7 do PLANO_INVENTARIO_API, RI14).

Três garantias:

- **Neutralidade financeira:** inventário não altera custos nem economia.
  Na política v4, suas dependências alimentam o score técnico e a cobertura.
- **Validação antes de tudo:** inventário de outro usuário, não processado ou
  de outra nuvem é recusado antes de criar (ou reaproveitar) o job.
- **Congelamento:** a análise grava a topologia com o detalhe que o Terraform
  usa; mudar a declaração depois não muda a análise feita; e a sequência de
  migração da resposta vem das ondas, não do modelo.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.api.deps import _DEV_USER
from app.core import config
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.session import get_sessionmaker
from app.main import create_app

from .conftest import _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INVENTORY_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("MCP_PROMPTS_ENABLED", "false")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def cur(_db_ready, mocks_billing):
    rid = await _ingest(mocks_billing.cur, user_id=HTTP_USER)
    try:
        yield rid
    finally:
        await _apagar_analises(rid)
        await _cleanup(rid)


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _enviar(c: AsyncClient, arquivos) -> UUID:
    r = await c.post(
        "/v1/inventories",
        files=[("files", (a.name, a.read_bytes(), "application/octet-stream")) for a in arquivos],
    )
    iid = UUID(r.json()["inventory_id"])
    assert (await c.get(f"/v1/inventories/{iid}")).json()["status"] == "done"
    return iid


async def _apagar_inventario(iid: UUID) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("delete from inventories where id = :i"), {"i": iid})
        await s.commit()


async def _apagar_analises(rid: UUID) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("delete from analysis_jobs where report_id = :r"), {"r": rid})
        await s.commit()


def _sem_meta(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in ("_meta", "topologia", "decision_support")}


async def test_payload_e_neutro_e_o_agente_recebe_a_topologia_compacta(
    app, cur, mocks_inventario
) -> None:
    async with _cliente(app) as c:
        iid = await _enviar(c, mocks_inventario.aws)
        try:
            sem = (await c.get(f"/v1/analyses/{cur}/request?recalcular=true")).json()
            com = (await c.get(f"/v1/analyses/{cur}/request?inventory_id={iid}")).json()
        finally:
            await _apagar_inventario(iid)
    assert "topologia" not in sem
    assert _sem_meta(com) == _sem_meta(sem)
    antes, depois = sem["decision_support"], com["decision_support"]
    assert antes["policy_version"] == depois["policy_version"] == "score-7rs-v4"
    assert antes["input_fingerprint"] != depois["input_fingerprint"]
    assert antes["workload_strategies"] == depois["workload_strategies"]
    cenarios_antes = {c["provider"]: c for c in antes["provider_scores"]}
    assert set(cenarios_antes) == {c["provider"] for c in depois["provider_scores"]}
    for cenario in depois["provider_scores"]:
        eixos_antes = {a["id"]: a for a in cenarios_antes[cenario["provider"]]["axes"]}
        eixos_depois = {a["id"]: a for a in cenario["axes"]}
        assert eixos_antes["financial"] == eixos_depois["financial"]
        fatores_antes = {f["id"]: f for f in eixos_antes["technical"]["factors"]}
        fatores_depois = {f["id"]: f for f in eixos_depois["technical"]["factors"]}
        assert fatores_antes["dependencies"]["coverage"] == 0
        assert fatores_depois["dependencies"]["coverage"] > 0
    topologia = com["topologia"]
    assert topologia["inventario_id"] == str(iid)
    assert "detalhe" not in topologia  # a superfície do agente é compacta
    resumo = topologia["resumo"]
    assert resumo["contagens"]["ondas"] == 3
    assert 0.8 < resumo["cobertura"]["fracao_encontrada"] < 0.9
    assert resumo["cobertura"]["lacunas"][0]["conta"] == "333333333333"
    assert any(r["regra"] == "banco_compartilhado" for r in resumo["riscos"])


async def test_pedido_de_analise_valida_o_inventario_antes_do_job(
    app, cur, mocks_inventario
) -> None:
    async with _cliente(app) as c:
        oci = await _enviar(c, mocks_inventario.oci)
        try:
            inexistente = await c.post(
                f"/v1/analyses/start/{cur}", json={"inventory_id": str(uuid4())}
            )
            outra_nuvem = await c.post(f"/v1/analyses/start/{cur}", json={"inventory_id": str(oci)})
        finally:
            await _apagar_inventario(oci)
    assert inexistente.status_code == 404
    assert inexistente.json()["detail"]["codigo"] == "INVENTARIO_NAO_ENCONTRADO"
    assert outra_nuvem.status_code == 422
    assert outra_nuvem.json()["detail"]["codigo"] == "INVENTARIO_DE_OUTRA_NUVEM"
    async with get_sessionmaker()() as s:
        jobs = (
            (await s.execute(select(AnalysisJobModel).where(AnalysisJobModel.report_id == cur)))
            .scalars()
            .all()
        )
    assert jobs == []  # nenhum job criado


async def test_flag_desligada_recusa_inventario_na_analise(cur, monkeypatch) -> None:
    monkeypatch.setenv("INVENTORY_ENABLED", "false")
    config.get_settings.cache_clear()
    try:
        async with _cliente(create_app()) as c:
            r = await c.post(f"/v1/analyses/start/{cur}", json={"inventory_id": str(uuid4())})
    finally:
        config.get_settings.cache_clear()
    assert r.status_code == 404
    assert r.json()["detail"]["codigo"] == "INVENTARIO_DESLIGADO"


async def test_analise_congela_a_topologia_e_impoe_a_ordem(app, cur, mocks_inventario) -> None:
    async with _cliente(app) as c:
        iid = await _enviar(c, mocks_inventario.aws)
        try:
            inicio = await c.post(f"/v1/analyses/start/{cur}", json={"inventory_id": str(iid)})
            assert inicio.status_code == 202, inicio.text
            job_id = UUID(inicio.json()["job_id"])
            # Declarar depois não muda a análise que já foi feita.
            await c.put(
                f"/v1/inventories/{iid}/declarations",
                json={"ferramentas": ["aplicacao:monitoramento"]},
            )
        finally:
            await _apagar_inventario(iid)

    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(AnalysisJobModel).where(AnalysisJobModel.job_id == job_id))
        ).scalar_one()
    assert job.status == "COMPLETED", job.error
    topologia = job.llm_input_payload["topologia"]
    assert topologia["declaracoes"] == {}
    detalhe = topologia["detalhe"]
    assert detalhe["posicionamento"] and detalhe["bancos"]
    assert detalhe["rede"]["subredes"] and detalhe["rede"]["grupos"]
    assert detalhe["discos"]["boot"] or detalhe["discos"]["sem_anexo"]
    pool = detalhe["posicionamento"][0]
    assert {"familia", "regiao", "posicionamentos", "sem_posicionamento"} <= set(pool)
    for pool in detalhe["posicionamento"]:
        # Uma linha por subrede: agrupar também pelos grupos de segurança partia
        # o pool em mais posicionamentos que máquinas aprovadas.
        subredes = [p["subrede"] for p in pool["posicionamentos"]]
        assert len(subredes) == len(set(subredes)), pool["familia"]
    versoes = [v for b in detalhe["bancos"] for v in b["versoes"]]
    assert "15.4" in versoes
    sequencia = job.ai_structured_output["agent_views"]["risk_migration"][
        "recommended_migration_sequence"
    ]
    assert sequencia[0].startswith("Onda 0 — fundação")
    assert any(s.startswith("Onda 1:") for s in sequencia)
    flags = job.ai_structured_output["agent_views"]["risk_migration"]["risk_flags"]
    assert any(f.startswith("[alta]") for f in flags)
