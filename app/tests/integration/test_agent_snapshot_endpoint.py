"""`/v1/agent/snapshots` pela HTTP, contra Postgres real.

Contrato: `MCP.PLAN/09-CONTRATO-SNAPSHOT.md` (v2). Plano:
`PLANO_SNAPSHOT_AGENTE.md`, itens C, D e F.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.core import config
from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.services.fingerprint_payload import hash_canonico
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_report_repo import PgBillingReportRepository
from app.main import create_app

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
