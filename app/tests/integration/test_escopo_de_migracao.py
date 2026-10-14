"""Aplicações e plano de migração pela API, com os mocks (PLANO_ESCOPO_DE_MIGRACAO).

O mock da AWS tem o que o plano precisa mostrar: auth, checkout e pagamentos
compartilham banco (vão juntos), o checkout chama o catalogo (vai antes), o
monitoramento lê o banco do checkout (fica para trás), e cada VM acessa o banco
da própria aplicação (a ligação EC2 → RDS que a topologia entre unidades não via).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.core import config
from app.infrastructure.database.session import get_sessionmaker
from app.main import create_app

from .conftest import _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)
pytestmark = pytest.mark.asyncio(loop_scope="session")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("INVENTORY_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.setenv("MCP_PROMPTS_ENABLED", "false")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


def _cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def analise(
    _db_ready: None, mocks_billing: Any, mocks_inventario: Any
) -> AsyncIterator[tuple[str, str]]:
    """(job com inventário, job sem inventário) do mesmo billing CUR."""
    rid = await _ingest(mocks_billing.cur, user_id=HTTP_USER)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("INVENTORY_ENABLED", "true")
        mp.setenv("LLM_PROVIDER", "mock")
        mp.setenv("LLM_FALLBACK_PROVIDER", "none")
        mp.setenv("MCP_PROMPTS_ENABLED", "false")
        config.get_settings.cache_clear()
        iid = None
        try:
            async with _cliente(create_app()) as c:
                arquivos = mocks_inventario.aws
                r = await c.post(
                    "/v1/inventories",
                    files=[
                        ("files", (a.name, a.read_bytes(), "application/octet-stream"))
                        for a in arquivos
                    ],
                )
                iid = UUID(r.json()["inventory_id"])
                sem = (await c.post(f"/v1/analyses/start/{rid}")).json()["job_id"]
                com = (
                    await c.post(f"/v1/analyses/start/{rid}", json={"inventory_id": str(iid)})
                ).json()["job_id"]
            yield com, sem
        finally:
            config.get_settings.cache_clear()
            async with get_sessionmaker()() as s:
                await s.execute(text("delete from analysis_jobs where report_id = :r"), {"r": rid})
                if iid:
                    await s.execute(text("delete from inventories where id = :i"), {"i": iid})
                await s.commit()
            await _cleanup(rid)


async def test_aplicacoes_e_dependencias_entre_servicos(
    app: FastAPI, analise: tuple[str, str]
) -> None:
    com, _ = analise
    async with _cliente(app) as c:
        r = await c.get(f"/v1/analyses/{com}/applications")
    assert r.status_code == 200, r.text
    corpo = r.json()
    apps = {a["nome"]: a for a in corpo["aplicacoes"]}
    assert {"auth", "checkout", "pagamentos", "catalogo"} <= set(apps)
    assert set(apps["checkout"]["migra_junto_com"]) == {"auth", "pagamentos"}
    assert corpo["ferramentas"] == ["bastion"]
    assert corpo["chave_aplicacao"] == "app"
    ec2 = next(s for s in corpo["servicos"] if s["nome"] == "AmazonEC2")
    assert {"servico": "AmazonRDS", "natureza": "dados", "portas": ["tcp/5432"]} in ec2[
        "depende_de"
    ]
    assert len(ec2["aplicacoes"]) >= 5
    assert any(p["campo"] == "ordem" for p in corpo["premissas"])


async def test_plano_de_uma_aplicacao_num_ambiente(app: FastAPI, analise: tuple[str, str]) -> None:
    com, _ = analise
    async with _cliente(app) as c:
        r = await c.post(
            f"/v1/analyses/{com}/migration-plans",
            json={"aplicacoes": ["checkout"], "ambiente": "prod"},
        )
    assert r.status_code == 200, r.text
    p = r.json()
    assert set(p["aplicacoes"]) == {"auth", "checkout", "pagamentos"}
    assert all(v["motivo"].startswith("Compartilha banco") for v in p["vao_junto"])
    assert [g["nome"] for g in p["antes"]] == ["catalogo (prod)"]
    assert any(g["nome"].startswith("monitoramento (prod)") for g in p["para_tras"])
    assert {
        "de": "AmazonEC2",
        "para": "AmazonRDS",
        "natureza": "dados",
        "portas": ["tcp/5432"],
    } in p["ligacoes"]
    oci = next(d for d in p["destino"] if d["provedor"] == "oci")
    # A OCI sai mais barata e quase tudo tem cotação (balanceador e EKS não)
    assert oci["custo_estimado"] < oci["custo_origem_cotado"]
    assert oci["sem_cotacao"] < 0.02 * p["custo"]
    rds = next(s for s in p["servicos"] if s["nome"] == "AmazonRDS")
    assert rds["metodos"] == ["categoria"]


async def test_plano_de_um_servico_diz_o_que_veio_junto(
    app: FastAPI, analise: tuple[str, str]
) -> None:
    com, _ = analise
    async with _cliente(app) as c:
        r = await c.post(f"/v1/analyses/{com}/migration-plans", json={"servicos": ["AmazonEC2"]})
        planilha = await c.post(
            f"/v1/analyses/{com}/migration-plans?formato=xlsx", json={"servicos": ["AmazonEC2"]}
        )
    assert r.status_code == 200, r.text
    vem = {v["servico"]: v["motivo"] for v in r.json()["servicos_que_vem_junto"]}
    assert vem["AmazonRDS"] == "AmazonEC2 acessa dados em AmazonRDS (tcp/5432)"
    assert any(f["nome"] == "AmazonEC2" for f in r.json()["fora_do_plano"])
    assert planilha.status_code == 200
    assert planilha.headers["content-type"] == _XLSX
    assert planilha.content[:2] == b"PK"


async def test_erros_da_superficie(
    app: FastAPI, analise: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    com, sem = analise
    async with _cliente(app) as c:
        vazio = await c.post(f"/v1/analyses/{com}/migration-plans", json={})
        desconhecido = await c.post(
            f"/v1/analyses/{com}/migration-plans", json={"aplicacoes": ["nao-existe"]}
        )
        sem_inventario = await c.get(f"/v1/analyses/{sem}/applications")
    assert (vazio.status_code, vazio.json()["detail"]["codigo"]) == (422, "ESCOLHA_INVALIDA")
    assert desconhecido.json()["detail"]["codigo"] == "ESCOLHA_INVALIDA"
    assert sem_inventario.status_code == 409
    assert sem_inventario.json()["detail"]["codigo"] == "INVENTARIO_AUSENTE"

    monkeypatch.setenv("INVENTORY_ENABLED", "false")
    config.get_settings.cache_clear()
    try:
        async with _cliente(create_app()) as c:
            desligado = await c.get(f"/v1/analyses/{com}/applications")
    finally:
        config.get_settings.cache_clear()
    assert desligado.json()["detail"]["codigo"] == "INVENTARIO_DESLIGADO"
