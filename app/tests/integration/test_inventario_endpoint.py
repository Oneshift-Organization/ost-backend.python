"""Superfície `/v1/inventories` contra o Postgres (I5 do PLANO_INVENTARIO_API).

Ciclo inteiro pela API com os mocks: envio dos arquivos, processamento em
segundo plano, recursos, topologia, declarações e remoção em cascata. E o que
não pode vazar: inventário de outro usuário é 404, e a flag desligada esconde a
superfície inteira.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.core import config
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.services.topologia import analisar_topologia
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos
from app.infrastructure.repositories.pg_inventory_repo import PgInventoryRepository
from app.main import create_app

HTTP_USER = UUID(_DEV_USER.sub)
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def _ligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INVENTORY_ENABLED", "true")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


@pytest.fixture
def _desligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INVENTORY_ENABLED", "false")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _arquivos(caminhos: list[Path]):
    return [("files", (c.name, c.read_bytes(), "application/octet-stream")) for c in caminhos]


async def _apagar_tudo(*ids: UUID) -> None:
    async with get_sessionmaker()() as s:
        for i in ids:
            await s.execute(text("delete from inventories where id = :i"), {"i": i})
        await s.commit()


async def _linhas(tabela: str, inventario_id: UUID) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                text(f"select count(*) from {tabela} where inventory_id = :i"),
                {"i": inventario_id},
            )
        ).scalar_one()


# ── o interruptor ───────────────────────────────────────────────────────


async def test_desligado_a_superficie_nao_existe(_desligado, _db_ready) -> None:
    async with _cliente(_desligado) as c:
        respostas = [
            await c.get("/v1/inventories"),
            await c.get(f"/v1/inventories/{uuid4()}"),
            await c.get(f"/v1/inventories/{uuid4()}/topology"),
            await c.post("/v1/inventories", files=[("files", ("a.json", b"{}", "x"))]),
        ]
    for r in respostas:
        assert r.status_code == 404
        assert r.json()["detail"]["codigo"] == "INVENTARIO_DESLIGADO"


# ── ciclo completo ──────────────────────────────────────────────────────


async def test_ciclo_completo_com_o_mock_da_aws(_ligado, _db_ready, mocks_inventario) -> None:
    async with _cliente(_ligado) as c:
        envio = await c.post("/v1/inventories", files=_arquivos(mocks_inventario.aws))
        assert envio.status_code == 202, envio.text
        iid = UUID(envio.json()["inventory_id"])
        try:
            detalhe = (await c.get(f"/v1/inventories/{iid}")).json()
            assert detalhe["status"] == "done", detalhe["error"]
            assert detalhe["provider"] == "aws"
            assert set(detalhe["detected_formats"].values()) == {"aws_config"}
            assert detalhe["resource_count"] == 734
            assert detalhe["counts_by_kind"]["instancia"] == 297
            assert detalhe["captured_at"].startswith("2026-03-31")
            assert detalhe["captured_at_estimated"] is False
            assert len(detalhe["fingerprint"]) == 64

            pagina = (
                await c.get(f"/v1/inventories/{iid}/resources?kind=banco&limit=10&offset=5")
            ).json()
            assert pagina["total"] == 85
            assert len(pagina["items"]) == 10
            chaves = [i["key"] for i in pagina["items"]]
            assert chaves == sorted(chaves)

            topologia = (await c.get(f"/v1/inventories/{iid}/topology")).json()
            # Sem declarações, a topologia é função só do conteúdo.
            assert topologia["fingerprint"] == detalhe["fingerprint"]
            assert topologia["resumo"]["ondas"] == 3
            assert {r["regra"] for r in topologia["riscos"]} >= {
                "banco_compartilhado",
                "hub_de_dados",
                "ferramenta_administrativa",
            }
            assert topologia["limites"]["cortadas"] == {}

            # Declarar o monitoramento como ferramenta muda a topologia.
            antes = len(topologia["ferramentas"])
            decl = await c.put(
                f"/v1/inventories/{iid}/declarations",
                json={
                    "ferramentas": ["aplicacao:monitoramento"],
                    "criticidade": {"aplicacoes": {"relatorios": "alta"}},
                },
            )
            assert decl.status_code == 200, decl.text
            assert decl.json()["declarations"]["ferramentas"] == ["aplicacao:monitoramento"]
            depois = (await c.get(f"/v1/inventories/{iid}/topology")).json()
            assert len(depois["ferramentas"]) > antes
            assert depois["fingerprint"] != detalhe["fingerprint"]
            relatorios = next(
                g for g in depois["grupos"] if g["nome"].startswith("relatorios (prod) · us")
            )
            assert relatorios["criticidade"]["efetiva"] == "alta"

            ruim = await c.put(
                f"/v1/inventories/{iid}/declarations",
                json={"criticidade": {"recursos": {"nao-existe": "alta"}}},
            )
            assert ruim.status_code == 422
            assert "nao-existe" in ruim.json()["detail"]["mensagem"]

            apagar = await c.delete(f"/v1/inventories/{iid}")
            assert apagar.status_code == 204
            assert (await c.get(f"/v1/inventories/{iid}")).status_code == 404
            assert await _linhas("inventory_resources", iid) == 0
            assert await _linhas("inventory_relations", iid) == 0
        finally:
            await _apagar_tudo(iid)


async def test_arquivo_invalido_falha_com_motivo(_ligado, _db_ready) -> None:
    async with _cliente(_ligado) as c:
        envio = await c.post(
            "/v1/inventories", files=[("files", ("planilha.json", b'{"linhas": [1]}', "x"))]
        )
        iid = UUID(envio.json()["inventory_id"])
        try:
            detalhe = (await c.get(f"/v1/inventories/{iid}")).json()
            assert detalhe["status"] == "failed"
            assert "não reconhecido" in detalhe["error"]
            topo = await c.get(f"/v1/inventories/{iid}/topology")
            assert topo.status_code == 409
            assert topo.json()["detail"]["codigo"] == "INVENTARIO_NAO_PROCESSADO"
        finally:
            await _apagar_tudo(iid)


async def test_extensao_e_quantidade_de_arquivos(_ligado, _db_ready, monkeypatch) -> None:
    async with _cliente(_ligado) as c:
        r = await c.post("/v1/inventories", files=[("files", ("custos.xlsx", b"x", "x"))])
        assert r.status_code == 415
        assert r.json()["detail"]["codigo"] == "EXTENSAO_NAO_ACEITA"
        muitos = [("files", (f"{i}.json", b"{}", "x")) for i in range(21)]
        r = await c.post("/v1/inventories", files=muitos)
        assert r.status_code == 422
        assert r.json()["detail"]["codigo"] == "ARQUIVOS_DEMAIS"


# ── isolamento ──────────────────────────────────────────────────────────


async def test_inventario_de_outro_usuario_e_404(_ligado, _db_ready) -> None:
    alheio = InventarioEnviado(user_id=uuid4(), arquivos=["x.json"])
    async with get_sessionmaker()() as s:
        await PgInventoryRepository(s).salvar(alheio)
        await s.commit()
    try:
        async with _cliente(_ligado) as c:
            respostas = [
                await c.get(f"/v1/inventories/{alheio.id}"),
                await c.get(f"/v1/inventories/{alheio.id}/resources"),
                await c.get(f"/v1/inventories/{alheio.id}/topology"),
                await c.put(f"/v1/inventories/{alheio.id}/declarations", json={}),
                await c.delete(f"/v1/inventories/{alheio.id}"),
            ]
            lista = (await c.get("/v1/inventories?limit=200")).json()
        for r in respostas:
            assert r.status_code == 404, r.text
            assert r.json()["detail"]["codigo"] == "INVENTARIO_NAO_ENCONTRADO"
        assert str(alheio.id) not in {i["inventory_id"] for i in lista}
    finally:
        await _apagar_tudo(alheio.id)


# ── repositório: ida e volta sem perder nada ────────────────────────────


@pytest.mark.parametrize("provedor", ["aws", "gcp", "azure", "oci"])
async def test_ida_e_volta_preserva_a_analise(_db_ready, mocks_inventario, provedor) -> None:
    arquivos = getattr(mocks_inventario, provedor)
    original = normalizar_arquivos([(a.name, a) for a in arquivos]).inventario
    registro = InventarioEnviado(user_id=HTTP_USER, arquivos=[a.name for a in arquivos])
    try:
        async with get_sessionmaker()() as s:
            repo = PgInventoryRepository(s)
            await repo.salvar(registro)
            await repo.gravar_conteudo(registro.id, HTTP_USER, original)
            # Idempotente: gravar de novo substitui, não duplica.
            await repo.gravar_conteudo(registro.id, HTTP_USER, original)
            await s.commit()
        registro.provedor = original.provedor
        registro.capturado_em = original.capturado_em
        registro.contagens = dict(original.contagens)
        async with get_sessionmaker()() as s:
            lido = await PgInventoryRepository(s).carregar_conteudo(registro)
        assert lido.recursos == original.recursos
        assert set(lido.relacoes) == set(original.relacoes)
        assert set(lido.regras) == set(original.regras)
        assert analisar_topologia(lido) == analisar_topologia(original)
    finally:
        await _apagar_tudo(registro.id)
