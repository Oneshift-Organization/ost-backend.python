"""`/v1/agent/snapshots` pela HTTP, contra Postgres real.

Contrato: `MCP.PLAN/09-CONTRATO-SNAPSHOT.md` (v2). Plano:
`PLANO_SNAPSHOT_AGENTE.md`, itens C, D e F.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app import main
from app.api.deps import _DEV_USER
from app.core import config
from app.domain.entities.agent_snapshot import AgentSnapshot, agora
from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.services.fingerprint_payload import hash_canonico
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_agent_snapshot_repo import PgAgentSnapshotRepository
from app.infrastructure.repositories.pg_billing_report_repo import PgBillingReportRepository
from app.main import create_app
from app.schemas.agent_snapshot import EnvioDoAgenteIn

from .conftest import TEST_USER, _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)
pytestmark = pytest.mark.asyncio(loop_scope="session")


def _app(monkeypatch: pytest.MonkeyPatch, **flags: str) -> Iterator[FastAPI]:
    ambiente = {
        "AGENT_SNAPSHOT_ENABLED": "true",
        "AGENT_FLOW_ENABLED": "true",
        "INVENTORY_ENABLED": "true",
        "LLM_PROVIDER": "mock",
        "LLM_FALLBACK_PROVIDER": "none",
        "MCP_PROMPTS_ENABLED": "false",
        **flags,
    }
    for chave, valor in ambiente.items():
        monkeypatch.setenv(chave, valor)
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    yield from _app(monkeypatch)


def _cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def oci(_db_ready: None, mocks_billing: Any) -> AsyncIterator[UUID]:
    rid = await _ingest(mocks_billing.oci, user_id=HTTP_USER)
    try:
        yield rid
    finally:
        await _cleanup(rid)


async def _inventario(c: AsyncClient, arquivos: list[Any]) -> UUID:
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


async def _apagar_snapshots(rid: UUID) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("delete from agent_snapshots where report_id = :r"), {"r": rid})
        await s.commit()


def _detalhe(r: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = r.json()["detail"]
    return corpo


# ── preparação: interruptores e erros ───────────────────────────────────


@pytest.mark.parametrize(
    "flags",
    [{"AGENT_SNAPSHOT_ENABLED": "false"}, {"AGENT_FLOW_ENABLED": "false"}],
)
async def test_desligado_responde_snapshot_desligado(
    monkeypatch: pytest.MonkeyPatch, flags: dict[str, str], oci: UUID
) -> None:
    for app in _app(monkeypatch, **flags):
        async with _cliente(app) as c:
            r = await c.post("/v1/agent/snapshots", json={"report_id": str(oci)})
        assert r.status_code == 404
        assert _detalhe(r)["codigo"] == "SNAPSHOT_DESLIGADO"


async def test_relatorio_inexistente_ou_de_outro_usuario(app: FastAPI, _db_ready: None) -> None:
    alheio = BillingReport(user_id=TEST_USER, source_filename="alheio.csv")
    async with get_sessionmaker()() as s:
        await PgBillingReportRepository(s).save(alheio)
        await s.commit()
    try:
        async with _cliente(app) as c:
            for rid in (uuid4(), alheio.id):
                r = await c.post("/v1/agent/snapshots", json={"report_id": str(rid)})
                assert r.status_code == 404
                assert _detalhe(r)["codigo"] == "REPORT_NAO_ENCONTRADO"
                assert _detalhe(r)["retryable"] is False
    finally:
        await _cleanup(alheio.id)


async def test_ingestao_em_andamento_e_repetivel(app: FastAPI, _db_ready: None) -> None:
    andamento = BillingReport(user_id=HTTP_USER, source_filename="andamento.csv")
    andamento.status = JobStatus.PROCESSING
    async with get_sessionmaker()() as s:
        await PgBillingReportRepository(s).save(andamento)
        await s.commit()
    try:
        async with _cliente(app) as c:
            r = await c.post("/v1/agent/snapshots", json={"report_id": str(andamento.id)})
        assert r.status_code == 409
        assert _detalhe(r) == {
            "codigo": "REPORT_NAO_PRONTO",
            "mensagem": "ingestão ainda em andamento (status=processing)",
            "retryable": True,
            "status_report": "processing",
        }
    finally:
        await _cleanup(andamento.id)


async def test_inventario_desligado_inexistente_ou_de_outra_nuvem(
    monkeypatch: pytest.MonkeyPatch, oci: UUID, mocks_inventario: Any
) -> None:
    for app in _app(monkeypatch, INVENTORY_ENABLED="false"):
        async with _cliente(app) as c:
            r = await c.post(
                "/v1/agent/snapshots", json={"report_id": str(oci), "inventory_id": str(uuid4())}
            )
        assert (r.status_code, _detalhe(r)["codigo"]) == (404, "INVENTARIO_DESLIGADO")

    for app in _app(monkeypatch):
        async with _cliente(app) as c:
            r = await c.post(
                "/v1/agent/snapshots", json={"report_id": str(oci), "inventory_id": str(uuid4())}
            )
            assert (r.status_code, _detalhe(r)["codigo"]) == (404, "INVENTARIO_NAO_ENCONTRADO")
            gcp = await _inventario(c, mocks_inventario.gcp)
            try:
                r = await c.post(
                    "/v1/agent/snapshots", json={"report_id": str(oci), "inventory_id": str(gcp)}
                )
            finally:
                await _apagar_inventario(gcp)
        assert (r.status_code, _detalhe(r)["codigo"]) == (422, "INVENTARIO_DE_OUTRA_NUVEM")


# ── preparação: criação, reaproveitamento e coerência ───────────────────


async def test_cria_e_depois_reaproveita(app: FastAPI, oci: UUID) -> None:
    try:
        async with _cliente(app) as c:
            r1 = await c.post("/v1/agent/snapshots", json={"report_id": str(oci)})
            r2 = await c.post("/v1/agent/snapshots", json={"report_id": str(oci)})
        assert (r1.status_code, r2.status_code) == (201, 200)
        s1, s2 = r1.json()["snapshot"], r2.json()["snapshot"]
        assert (s1["reused"], s2["reused"]) == (False, True)
        assert s1["snapshot_id"] == s2["snapshot_id"]
        assert s1["fingerprint"] == s2["fingerprint"]
        assert s2["expires_at"] >= s1["expires_at"]
        assert s1["versions"]["authority"] == "agent-authority-v1"
        assert s1["versions"]["topology"] is None
        assert s1["inventory_id"] is None
    finally:
        await _apagar_snapshots(oci)


async def test_payload_bate_com_o_request_recalculado(app: FastAPI, oci: UUID) -> None:
    """C3: os dois caminhos que o agente pode ler não divergem."""
    try:
        async with _cliente(app) as c:
            snap = (await c.post("/v1/agent/snapshots", json={"report_id": str(oci)})).json()
            req = (await c.get(f"/v1/analyses/{oci}/request?recalcular=true")).json()
        meta = req.pop("_meta")
        assert snap["payload"] == req
        assert snap["pools_sem_equivalente"] == meta["pools_sem_equivalente"]
        assert snap["snapshot"]["payload_fingerprint"] == hash_canonico(snap["payload"])
    finally:
        await _apagar_snapshots(oci)


async def test_com_inventario_entrega_compacto_e_guarda_o_detalhe(
    app: FastAPI, oci: UUID, mocks_inventario: Any
) -> None:
    async with _cliente(app) as c:
        iid = await _inventario(c, mocks_inventario.oci)
        try:
            r = await c.post(
                "/v1/agent/snapshots", json={"report_id": str(oci), "inventory_id": str(iid)}
            )
            req = (await c.get(f"/v1/analyses/{oci}/request?inventory_id={iid}")).json()
            assert r.status_code == 201
            corpo = r.json()
            req.pop("_meta")
            assert corpo["payload"] == req
            assert "detalhe" not in corpo["payload"]["topologia"]
            assert corpo["snapshot"]["versions"]["topology"] == "oneshift-topologia-v1"
            assert corpo["snapshot"]["inventory_id"] == str(iid)
            async with get_sessionmaker()() as s:
                gravado = (
                    await s.execute(
                        text("select payload, inventory_marker from agent_snapshots where id = :i"),
                        {"i": corpo["snapshot"]["snapshot_id"]},
                    )
                ).one()
            assert "detalhe" in gravado.payload["topologia"]
            assert gravado.inventory_marker
        finally:
            await _apagar_snapshots(oci)
            await _apagar_inventario(iid)


# ── envio ───────────────────────────────────────────────────────────────

_PARECER = {
    "posicao": "diverge",
    "motivo": "A economia depende de cargas com lock-in alto.",
    "riscos": ["residência de dados"],
}
_ENVIO = {
    "recommendation": "migrar para Azure",
    "recommendation_rationale": "melhor rede",
    "strategic_notes": ["nota"],
    "action_plan": ["passo 1"],
    "provider_comparison": [
        {"provider": "oci", "estimated_monthly_cost": 1.0, "strengths": ["preço"]}
    ],
    "sugestoes": [
        {
            "tipo": "contestacao_preco",
            "severidade": "alta",
            "titulo": "preço defasado",
            "detalhe": "abaixo do que conheço",
            "referencia": "OCI/E5/sa-saopaulo-1",
        }
    ],
    "payback": {"meses": 7, "premissa_custo_migracao_usd": 18000, "base": "12 cargas"},
    "parecer": _PARECER,
}


async def _preparar(c: AsyncClient, rid: UUID, inventario: UUID | None = None) -> dict[str, Any]:
    corpo: dict[str, Any] = {"report_id": str(rid)}
    if inventario is not None:
        corpo["inventory_id"] = str(inventario)
    r = await c.post("/v1/agent/snapshots", json=corpo)
    assert r.status_code in (200, 201), r.text
    resposta: dict[str, Any] = r.json()
    return resposta


async def _enviar(c: AsyncClient, snapshot_id: str, corpo: dict[str, Any]) -> Any:
    return await c.post(f"/v1/agent/snapshots/{snapshot_id}/submissions", json=corpo)


async def _sql(comando: str, **params: Any) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text(comando), params)
        await s.commit()


async def test_relatorio_sem_analise_previa_recebe_o_envio(app: FastAPI, oci: UUID) -> None:
    """O cenário que hoje dá PAYLOAD_AUSENTE: billing novo, direto para o agente."""
    try:
        async with _cliente(app) as c:
            snap = await _preparar(c, oci)
            r = await _enviar(c, snap["snapshot"]["snapshot_id"], _ENVIO)
        assert r.status_code == 201, r.text
        corpo = r.json()
        envio, analise = corpo["submission"], corpo["analysis"]
        assert envio["revision"] == 1
        assert envio["fingerprint"] == snap["snapshot"]["fingerprint"]
        assert envio["versions"] == snap["snapshot"]["versions"]
        esperado = EnvioDoAgenteIn.model_validate(_ENVIO).model_dump(mode="json")
        assert envio["request_hash"] == hash_canonico(esperado)

        # A decisão e o texto da recomendação são do backend, do payload lido.
        decisao = snap["payload"]["decision_support"]
        assert analise["decision_support"] == decisao
        escolhido = decisao["selected_provider"]
        assert analise["recommendation"] == (
            f"Avaliar {escolhido.upper()}. {decisao['selection_reason']}"
            if escolhido
            else decisao["selection_reason"]
        )
        # O julgamento do agente fica.
        assert analise["parecer_agente"] == _PARECER
        assert analise["recommendation_rationale"] == "melhor rede"
        assert analise["action_plan"] == ["passo 1"]
        contestacao = next(s for s in analise["sugestoes"] if s["origem"] == "agente")
        assert contestacao["efeito_backend"] == "confianca_rebaixada"
        assert envio["campos_sobrescritos"] >= 1
    finally:
        await _apagar_snapshots(oci)


async def test_mesmo_corpo_e_repeticao_e_corpo_novo_e_revisao(app: FastAPI, oci: UUID) -> None:
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            r1 = await _enviar(c, sid, _ENVIO)
            r2 = await _enviar(c, sid, _ENVIO)
            reordenado = dict(reversed(list(_ENVIO.items())))  # ordem não conta
            r3 = await _enviar(c, sid, reordenado)
            r4 = await _enviar(c, sid, {**_ENVIO, "action_plan": ["outro passo"]})
        assert (r1.status_code, r2.status_code, r3.status_code, r4.status_code) == (
            201,
            200,
            200,
            201,
        )
        assert r2.headers["Idempotent-Replayed"] == "true"
        assert "Idempotent-Replayed" not in r1.headers
        assert r2.json() == r1.json() == r3.json()
        assert r4.json()["submission"]["revision"] == 2
    finally:
        await _apagar_snapshots(oci)


async def test_envios_simultaneos(app: FastAPI, oci: UUID) -> None:
    """Garantia do banco, não de consulta prévia: iguais viram um envio só;
    diferentes viram revisões sem buraco nem repetição."""
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            iguais = await asyncio.gather(*(_enviar(c, sid, _ENVIO) for _ in range(4)))
            diferentes = await asyncio.gather(
                *(_enviar(c, sid, {**_ENVIO, "action_plan": [f"p{i}"]}) for i in range(4))
            )
        assert sorted(r.status_code for r in iguais) == [200, 200, 200, 201]
        assert len({r.json()["submission"]["submission_id"] for r in iguais}) == 1
        assert all(r.status_code == 201 for r in diferentes)
        assert sorted(r.json()["submission"]["revision"] for r in diferentes) == [2, 3, 4, 5]
    finally:
        await _apagar_snapshots(oci)


async def test_envio_nao_toca_na_analise_da_tela(app: FastAPI, oci: UUID) -> None:
    """O defeito da rota antiga: o envio gravava por cima da análise da tela."""
    job_id = uuid4()
    tela = {"recommendation": "texto do LLM da tela", "strategic_notes": ["a", "b", "c"]}
    await _sql(
        "insert into analysis_jobs (job_id, user_id, report_id, status, created_at, "
        "ai_structured_output, llm_input_payload) values (:j, :u, :r, 'COMPLETED', now(), "
        "cast(:o as jsonb), cast('{\"gasto_total\": 1}' as jsonb))",
        j=job_id,
        u=HTTP_USER,
        r=oci,
        o=json.dumps(tela),
    )
    try:
        async with _cliente(app) as c:
            snap = await _preparar(c, oci)
            assert (await _enviar(c, snap["snapshot"]["snapshot_id"], _ENVIO)).status_code == 201
        async with get_sessionmaker()() as s:
            linha = (
                await s.execute(
                    text(
                        "select ai_structured_output, agent_raw_output, sugestoes "
                        "from analysis_jobs where job_id = :j"
                    ),
                    {"j": job_id},
                )
            ).one()
        assert linha.ai_structured_output == tela
        assert linha.agent_raw_output is None and linha.sugestoes is None
    finally:
        await _sql("delete from analysis_jobs where job_id = :j", j=job_id)
        await _apagar_snapshots(oci)


async def test_vencido_recusa_envio_novo_mas_devolve_o_gravado(app: FastAPI, oci: UUID) -> None:
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            primeiro = await _enviar(c, sid, _ENVIO)
            await _sql(
                "update agent_snapshots set expires_at = now() - interval '1 hour' where id = :i",
                i=sid,
            )
            novo = await _enviar(c, sid, {**_ENVIO, "action_plan": ["depois de vencer"]})
            repetido = await _enviar(c, sid, _ENVIO)
        assert (novo.status_code, _detalhe(novo)["codigo"]) == (410, "SNAPSHOT_EXPIRADO")
        assert _detalhe(novo)["retryable"] is False
        assert repetido.status_code == 200
        assert repetido.json() == primeiro.json()
    finally:
        await _apagar_snapshots(oci)


@pytest.mark.parametrize(
    ("campo", "valor_antigo"),
    [("authority", "agent-authority-v0"), ("decision_policy", "score-7rs-v2")],
)
async def test_versao_anterior_e_recusada_sem_reinterpretar(
    app: FastAPI, oci: UUID, campo: str, valor_antigo: str
) -> None:
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            await _sql(
                "update agent_snapshots set versions = "
                "jsonb_set(versions, :p, to_jsonb(cast(:v as text))) where id = :i",
                p=[campo],
                v=valor_antigo,
                i=sid,
            )
            r = await _enviar(c, sid, _ENVIO)
        assert r.status_code == 409
        d = _detalhe(r)
        assert (d["codigo"], d["motivo"], d["retryable"]) == (
            "SNAPSHOT_DESATUALIZADO",
            "versao",
            False,
        )
        assert d["versoes_divergentes"] == [campo]
    finally:
        await _apagar_snapshots(oci)


async def test_relatorio_que_deixou_de_estar_pronto(app: FastAPI, oci: UUID) -> None:
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            await _sql("update billing_reports set status = 'processing' where id = :r", r=oci)
            r = await _enviar(c, sid, _ENVIO)
        assert r.status_code == 409
        assert (_detalhe(r)["codigo"], _detalhe(r)["motivo"]) == (
            "SNAPSHOT_DESATUALIZADO",
            "relatorio",
        )
    finally:
        await _sql("update billing_reports set status = 'done' where id = :r", r=oci)
        await _apagar_snapshots(oci)


async def test_inventario_alterado_ou_apagado(
    app: FastAPI, oci: UUID, mocks_inventario: Any
) -> None:
    try:
        async with _cliente(app) as c:
            iid = await _inventario(c, mocks_inventario.oci)
            try:
                sid = (await _preparar(c, oci, iid))["snapshot"]["snapshot_id"]
                declarado = await c.put(
                    f"/v1/inventories/{iid}/declarations",
                    json={"criticidade": {"aplicacoes": {"checkout": "alta"}}},
                )
                assert declarado.status_code == 200, declarado.text
                alterado = await _enviar(c, sid, _ENVIO)
                # Preparar de novo traz o estado atual e volta a aceitar.
                sid2 = (await _preparar(c, oci, iid))["snapshot"]["snapshot_id"]
                aceito = await _enviar(c, sid2, _ENVIO)
            finally:
                await _apagar_inventario(iid)
            apagado = await _enviar(c, sid2, {**_ENVIO, "action_plan": ["outro"]})
        for r in (alterado, apagado):
            assert r.status_code == 409
            assert (_detalhe(r)["codigo"], _detalhe(r)["motivo"]) == (
                "SNAPSHOT_DESATUALIZADO",
                "inventario",
            )
        assert aceito.status_code == 201
    finally:
        await _apagar_snapshots(oci)


async def test_snapshot_de_outro_usuario_ou_inexistente(app: FastAPI, oci: UUID) -> None:
    alheio = AgentSnapshot(
        user_id=TEST_USER,
        report_id=oci,
        inventory_id=None,
        inventory_marker=None,
        fingerprint="a" * 64,
        payload_fingerprint="b" * 64,
        versions={},
        payload={},
        expires_at=agora() + timedelta(hours=1),
    )
    async with get_sessionmaker()() as s:
        await PgAgentSnapshotRepository(s).gravar_ou_reaproveitar(alheio)
        await s.commit()
    try:
        async with _cliente(app) as c:
            for sid in (alheio.id, uuid4()):
                r = await _enviar(c, str(sid), _ENVIO)
                assert r.status_code == 404
                assert _detalhe(r)["codigo"] == "SNAPSHOT_NAO_ENCONTRADO"
    finally:
        await _apagar_snapshots(oci)


async def test_payback_sem_premissa_e_corpo_invalido(app: FastAPI, oci: UUID) -> None:
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            sem_premissa = await _enviar(
                c, sid, {**_ENVIO, "payback": {"meses": 7, "base": "chute"}}
            )
            parecer_ruim = await _enviar(
                c, sid, {**_ENVIO, "parecer": {"posicao": "talvez", "motivo": "x"}}
            )
        assert sem_premissa.status_code == 422
        assert parecer_ruim.status_code == 422
    finally:
        await _apagar_snapshots(oci)


async def test_envio_com_flag_desligada(monkeypatch: pytest.MonkeyPatch, oci: UUID) -> None:
    for app in _app(monkeypatch, AGENT_SNAPSHOT_ENABLED="false"):
        async with _cliente(app) as c:
            r = await _enviar(c, str(uuid4()), _ENVIO)
        assert (r.status_code, _detalhe(r)["codigo"]) == (404, "SNAPSHOT_DESLIGADO")


# ── consulta, transição e manutenção (item E) ───────────────────────────


async def test_consulta_por_id_e_lista_com_revisoes(app: FastAPI, oci: UUID) -> None:
    try:
        async with _cliente(app) as c:
            sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
            r1 = (await _enviar(c, sid, _ENVIO)).json()
            r2 = (await _enviar(c, sid, {**_ENVIO, "action_plan": ["revisão 2"]})).json()
            item = await c.get(f"/v1/agent/submissions/{r1['submission']['submission_id']}")
            por_relatorio = await c.get(f"/v1/agent/submissions?report_id={oci}")
            por_snapshot = await c.get(f"/v1/agent/submissions?snapshot_id={sid}&limit=1")
            fora_da_faixa = await c.get("/v1/agent/submissions?limit=0")
            inexistente = await c.get(f"/v1/agent/submissions/{uuid4()}")
        assert item.status_code == 200 and item.json() == r1
        lista = por_relatorio.json()
        assert [e["submission"]["revision"] for e in lista] == [2, 1]
        assert lista[0]["submission"] == r2["submission"]
        resumo = lista[0]["analysis_summary"]
        assert resumo["recommendation"] == r2["analysis"]["recommendation"]
        assert resumo["sugestoes"] == len(r2["analysis"]["sugestoes"])
        assert [e["submission"]["revision"] for e in por_snapshot.json()] == [2]
        assert fora_da_faixa.status_code == 422
        assert inexistente.status_code == 404
        assert _detalhe(inexistente)["codigo"] == "ENVIO_NAO_ENCONTRADO"
    finally:
        await _apagar_snapshots(oci)


async def test_expurgo_so_com_a_flag_e_so_sem_envio(
    monkeypatch: pytest.MonkeyPatch, oci: UUID
) -> None:
    try:
        for app in _app(monkeypatch):
            async with _cliente(app) as c:
                vazio = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
                usado = (await _preparar(c, oci, None))["snapshot"]["snapshot_id"]
        assert vazio == usado  # mesmo conteúdo: um snapshot só
        await _sql(
            "update agent_snapshots set expires_at = now() - interval '30 days' where id = :i",
            i=vazio,
        )
        for _ in _app(monkeypatch, AGENT_SNAPSHOT_ENABLED="false"):
            assert await main._expurgar_snapshots_do_agente() == 0
        for _ in _app(monkeypatch):
            assert await main._expurgar_snapshots_do_agente() >= 1
        async with get_sessionmaker()() as s:
            restante = (
                await s.execute(
                    text("select count(*) from agent_snapshots where id = :i"), {"i": vazio}
                )
            ).scalar()
        assert restante == 0
    finally:
        await _apagar_snapshots(oci)


async def test_expurgo_preserva_o_snapshot_com_envio(
    monkeypatch: pytest.MonkeyPatch, oci: UUID
) -> None:
    try:
        for app in _app(monkeypatch):
            async with _cliente(app) as c:
                sid = (await _preparar(c, oci))["snapshot"]["snapshot_id"]
                assert (await _enviar(c, sid, _ENVIO)).status_code == 201
            await _sql(
                "update agent_snapshots set expires_at = now() - interval '30 days' where id = :i",
                i=sid,
            )
            await main._expurgar_snapshots_do_agente()
            async with _cliente(app) as c:
                repetido = await _enviar(c, sid, _ENVIO)
        assert repetido.status_code == 200
    finally:
        await _apagar_snapshots(oci)


async def test_openapi_marca_a_rota_antiga_como_deprecated(app: FastAPI) -> None:
    async with _cliente(app) as c:
        caminhos = (await c.get("/openapi.json")).json()["paths"]
    assert caminhos["/v1/agent/analyses/{report_id}/result"]["post"]["deprecated"] is True
    assert "deprecated" not in caminhos["/v1/agent/analyses/{report_id}/result"]["get"]
    for novo in (
        "/v1/agent/snapshots",
        "/v1/agent/snapshots/{snapshot_id}/submissions",
        "/v1/agent/submissions",
        "/v1/agent/submissions/{submission_id}",
    ):
        assert novo in caminhos


# ── bateria do time do MCP: os cenários que faltavam (F1) ────────────────


async def test_dois_inventarios_dao_dois_snapshots_independentes(
    app: FastAPI, oci: UUID, mocks_inventario: Any
) -> None:
    async with _cliente(app) as c:
        a = await _inventario(c, mocks_inventario.oci)
        b = await _inventario(c, mocks_inventario.oci)
        try:
            snap_a = await _preparar(c, oci, a)
            snap_b = await _preparar(c, oci, b)
            assert snap_a["snapshot"]["snapshot_id"] != snap_b["snapshot"]["snapshot_id"]
            assert snap_a["payload"]["topologia"]["inventario_id"] == str(a)
            assert snap_b["payload"]["topologia"]["inventario_id"] == str(b)
            # Mexer em B não afeta o envio contra A.
            await _apagar_inventario(b)
            r = await _enviar(c, snap_a["snapshot"]["snapshot_id"], _ENVIO)
            assert r.status_code == 201, r.text
            assert r.json()["submission"]["snapshot_id"] == snap_a["snapshot"]["snapshot_id"]
            r_b = await _enviar(c, snap_b["snapshot"]["snapshot_id"], _ENVIO)
            assert _detalhe(r_b)["motivo"] == "inventario"
        finally:
            await _apagar_snapshots(oci)
            await _apagar_inventario(a)
            await _apagar_inventario(b)


async def test_relatorio_pronto_sem_dados_e_contexto_indisponivel(
    app: FastAPI, _db_ready: None
) -> None:
    vazio = BillingReport(user_id=HTTP_USER, source_filename="vazio.csv")
    vazio.status = JobStatus.DONE
    async with get_sessionmaker()() as s:
        await PgBillingReportRepository(s).save(vazio)
        await s.commit()
    try:
        async with _cliente(app) as c:
            r = await c.post("/v1/agent/snapshots", json={"report_id": str(vazio.id)})
            req = await c.get(f"/v1/analyses/{vazio.id}/request")
        assert r.status_code in (404, 422)
        assert _detalhe(r)["codigo"] == "CONTEXTO_INDISPONIVEL"
        assert _detalhe(r)["retryable"] is False
        # O mesmo erro do /request, pelo mesmo montador.
        assert (req.status_code, _detalhe(req)) == (r.status_code, _detalhe(r))
    finally:
        await _cleanup(vazio.id)
