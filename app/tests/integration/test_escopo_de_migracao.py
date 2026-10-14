"""Aplicações e plano de migração pela API, com os mocks (PLANO_ESCOPO_DE_MIGRACAO).

O mock da AWS tem o que o plano precisa mostrar: auth, checkout e pagamentos
compartilham banco (vão juntos), o checkout chama o catalogo (vai antes), o
monitoramento lê o banco do checkout (fica para trás), e cada VM acessa o banco
da própria aplicação (a ligação EC2 → RDS que a topologia entre unidades não via).
"""

from __future__ import annotations

import gzip
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
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

from .conftest import FIXTURES, _cleanup, _ingest

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


def _de_outra_conta(arquivos: list[Path], destino: Path) -> list[Path]:
    """O mesmo inventário, de outras contas: números de conta, IDs e nomes de bucket trocados."""
    destino.mkdir(parents=True, exist_ok=True)
    saida = []
    for arquivo in arquivos:
        bruto = gzip.decompress(arquivo.read_bytes()).decode()
        for de, para in (("222222222222", "999999999999"), ("333333333333", "777777777777")):
            bruto = bruto.replace(de, para)
        bruto = bruto.replace("444444444444", "888888888888")
        bruto = re.sub(
            r"\b(i|vol|eni|sg|subnet|vpc|igw|nat|eipalloc|rtb|acl|snap)-([0-9a-f]{6,})",
            lambda m: f"{m.group(1)}-{m.group(2)[::-1]}",
            bruto,
        )
        bruto = re.sub(
            r"(arn:aws:s3:::)([a-z0-9.-]+)", lambda m: m.group(1) + "outra-" + m.group(2), bruto
        )
        bruto = re.sub(
            r'"(bucketName|name)":\s*"([a-z0-9.-]+)"',
            lambda m: f'"{m.group(1)}": "outra-{m.group(2)}"',
            bruto,
        )
        alvo = destino / arquivo.name.replace("222222222222", "999999999999")
        alvo.write_bytes(gzip.compress(bruto.encode()))
        saida.append(alvo)
    return saida


async def _enviar_inventario(c: AsyncClient, arquivos: list[Path]) -> UUID:
    r = await c.post(
        "/v1/inventories",
        files=[("files", (a.name, a.read_bytes(), "application/octet-stream")) for a in arquivos],
    )
    return UUID(r.json()["inventory_id"])


async def _apagar(rid: UUID, *inventarios: UUID) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("delete from analysis_jobs where report_id = :r"), {"r": rid})
        for iid in inventarios:
            await s.execute(text("delete from inventories where id = :i"), {"i": iid})
        await s.commit()
    await _cleanup(rid)


async def test_inventario_de_outra_conta_e_recusado_antes_da_analise(
    app: FastAPI, _db_ready: None, mocks_billing: Any, mocks_inventario: Any, tmp_path: Path
) -> None:
    rid = await _ingest(mocks_billing.cur, user_id=HTTP_USER)
    iid = None
    try:
        async with _cliente(app) as c:
            iid = await _enviar_inventario(c, _de_outra_conta(mocks_inventario.aws, tmp_path))
            r = await c.post(f"/v1/analyses/start/{rid}", json={"inventory_id": str(iid)})
    finally:
        await _apagar(rid, *([iid] if iid else []))
    assert r.status_code == 422, r.text
    detalhe = r.json()["detail"]
    assert detalhe["codigo"] == "INVENTARIO_NAO_CORRESPONDE"
    assert detalhe["contas_do_inventario"] == ["888888888888", "999999999999"]
    assert "222222222222" in detalhe["contas_da_fatura"]


async def test_fatura_resumida_com_inventario_avisa_e_so_planeja_por_aplicacao(
    app: FastAPI, _db_ready: None, mocks_inventario: Any
) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    iid = None
    try:
        async with _cliente(app) as c:
            iid = await _enviar_inventario(c, mocks_inventario.aws)
            inicio = await c.post(f"/v1/analyses/start/{rid}", json={"inventory_id": str(iid)})
            assert inicio.status_code == 202, inicio.text
            job = inicio.json()["job_id"]
            aplicacoes = (await c.get(f"/v1/analyses/{job}/applications")).json()
            por_servico = await c.post(
                f"/v1/analyses/{job}/migration-plans", json={"servicos": ["AmazonEC2"]}
            )
            por_aplicacao = await c.post(
                f"/v1/analyses/{job}/migration-plans", json={"aplicacoes": ["checkout"]}
            )
    finally:
        await _apagar(rid, *([iid] if iid else []))
    assert aplicacoes["correspondencia"]["status"] == "sem_id_de_recurso"
    assert aplicacoes["correspondencia"]["liga_custo"] is False
    assert len(aplicacoes["aplicacoes"]) >= 5
    assert por_servico.status_code == 422
    assert por_servico.json()["detail"]["codigo"] == "ESCOLHA_INVALIDA"
    assert por_aplicacao.status_code == 200
    plano = por_aplicacao.json()
    assert {"auth", "checkout", "pagamentos"} <= set(plano["aplicacoes"])
    assert plano["correspondencia"]["status"] == "sem_id_de_recurso"
