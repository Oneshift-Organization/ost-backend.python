"""Aplicações e plano de migração pela API, com os mocks (PLANO_ESCOPO_DE_MIGRACAO).

O mock da AWS tem o que o plano precisa mostrar: auth, checkout e pagamentos
compartilham banco (vão juntos), o checkout chama o catalogo (vai antes), o
monitoramento lê o banco do checkout (fica para trás), e cada VM acessa o banco
da própria aplicação (a ligação EC2 → RDS que a topologia entre unidades não via).
"""

from __future__ import annotations

import gzip
import io
import os
import re
import shutil
import zipfile
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
    destinos = {d["provedor"]: d for d in p["destino"]}
    oci = destinos["oci"]
    # A OCI é o destino mais barato do escopo e quase tudo tem cotação (balanceador e
    # EKS não). Não necessariamente abaixo da origem: o RDS do mock roda com reserva
    # (~US$ 0,035 por vCPU-hora) e o destino é cotado on-demand (PLANO_PRECISAO_OCI P1.6)
    assert oci["custo_estimado"] < min(
        d["custo_estimado"] for k, d in destinos.items() if k != "oci"
    )
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
        pdf = await c.post(
            f"/v1/analyses/{com}/migration-plans?formato=pdf", json={"servicos": ["AmazonEC2"]}
        )
    assert r.status_code == 200, r.text
    vem = {v["servico"]: v["motivo"] for v in r.json()["servicos_que_vem_junto"]}
    assert vem["AmazonRDS"] == "AmazonEC2 acessa dados em AmazonRDS (tcp/5432)"
    assert any(f["nome"] == "AmazonEC2" for f in r.json()["fora_do_plano"])
    assert planilha.status_code == 200
    assert planilha.headers["content-type"] == _XLSX
    assert planilha.content[:2] == b"PK"
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"
    assert 'filename="plano-de-migracao-amazonec2-' in pdf.headers["content-disposition"]
    # Sem cotação vem com o motivo, pronto para exibir
    elb = next(s for s in r.json()["servicos"] if s["nome"] == "Amazon Elastic Load Balancing")
    assert elb["sem_cotacao"]["oci"] == "a análise ainda não cota balanceador de carga"


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


async def test_fatura_sem_id_com_inventario_estima_pelo_tipo_e_planeja_por_servico(
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
    # Formato detalhado legado da AWS: sem ID, mas com o tipo de uso. O custo chega às
    # máquinas do inventário pelo tipo, marcado como estimado (PLANO_CENARIOS_DE_ENTRADA)
    correspondencia = aplicacoes["correspondencia"]
    assert correspondencia["status"] == "estimado"
    assert correspondencia["liga_custo"] is True
    assert correspondencia["fracao_estimada"] > 0
    assert "Cost Explorer" not in correspondencia["mensagem"]
    assert len(aplicacoes["aplicacoes"]) >= 5
    assert por_servico.status_code == 200, por_servico.text
    assert por_servico.json()["unidades_do_plano"]
    assert por_aplicacao.status_code == 200
    plano = por_aplicacao.json()
    assert {"auth", "checkout", "pagamentos"} <= set(plano["aplicacoes"])
    assert plano["correspondencia"]["status"] == "estimado"


# ── só o serviço (pedido de 18/10) ──────────────────────────────────────

_VM = re.compile(
    r'resource "(aws_instance|google_compute_instance|azurerm_linux_virtual_machine|'
    r'oci_core_instance)"'
)
_BANCO = re.compile(
    r'resource "(aws_db_instance|google_sql_database_instance|azurerm_postgresql_flexible_server|'
    r'oci_database_autonomous_database)"'
)
_TIPOS = re.compile(r'resource "([a-z_]+)"')
_TEM_TERRAFORM = shutil.which("terraform") is not None and bool(
    os.environ.get("TF_PLUGIN_CACHE_DIR")
)


@pytest.fixture
def app_tf(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("INVENTORY_ENABLED", "true")
    monkeypatch.setenv("TERRAFORM_GENERATION_ENABLED", "true")
    monkeypatch.setenv("TERRAFORM_VALIDATE_ENABLED", "true" if _TEM_TERRAFORM else "false")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


async def _gerar(c: AsyncClient, job: str, destino: str, escopo: dict[str, Any]) -> dict[str, Any]:
    """Gera o pacote do escopo e devolve o estado, o main.tf e o README."""
    r = await c.post(
        "/v1/terraform-generations",
        json={
            "analysis_job_id": job,
            "project_name": "so-o-servico",
            "environment": "dev",
            "target_provider": destino,
            "region": {"gcp": "us-central1", "azure": "eastus", "oci": "us-ashburn-1"}[destino],
            "network": {"mode": "create", "cidr": "10.60.0.0/16"},
            "escopo": escopo,
        },
    )
    assert r.status_code == 202, r.text
    gid = r.json()["generation_id"]
    estado = (await c.get(f"/v1/terraform-generations/{gid}")).json()
    assert estado["status"] == "COMPLETED", estado.get("error")
    dl = await c.get(f"/v1/terraform-generations/{gid}/download")
    with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
        tf = "\n".join(z.read(n).decode() for n in z.namelist() if n.endswith(".tf"))
        readme = next(z.read(n).decode() for n in z.namelist() if n.endswith("README.md"))
    tipos = sorted(set(_TIPOS.findall(tf)))
    return {**estado, "tf": tf, "readme": readme, "tipos": tipos}


async def test_plano_so_do_banco_nao_leva_as_aplicacoes_e_mostra_a_fronteira(
    app: FastAPI, analise: tuple[str, str]
) -> None:
    com, _ = analise
    async with _cliente(app) as c:
        r = await c.post(
            f"/v1/analyses/{com}/migration-plans",
            json={"servicos": ["AmazonRDS"], "somente_os_servicos": True},
        )
        de_aplicacao = await c.post(
            f"/v1/analyses/{com}/migration-plans", json={"servicos": ["AmazonRDS"]}
        )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["escolha"]["somente_os_servicos"] is True
    assert {s["nome"] for s in p["servicos"]} == {"AmazonRDS"}
    assert p["vao_junto"] == [] and p["servicos_que_vem_junto"] == []
    # A VM do checkout fica na origem e acessa o banco que migra: forte, com o que fazer
    ligacao = next(
        f
        for f in p["fronteira"]
        if f["de"] == "AmazonEC2 (checkout)" and f["para"] == "AmazonRDS (checkout)"
    )
    assert ligacao["sentido"] == "entra"
    assert ligacao["natureza"] == "dados" and ligacao["forte"] is True
    assert ligacao["portas"] == ["tcp/5432"]
    assert "Troque o endereço na configuração de AmazonEC2 (checkout)" in ligacao["texto"]
    # O monitoramento lê o banco do checkout: também fica do outro lado
    assert any(
        f["para"] == "AmazonRDS (checkout)" and f["de"] != "AmazonEC2 (checkout)"
        for f in p["fronteira"]
    ), [(f["de"], f["para"]) for f in p["fronteira"]]
    # Sem a flag, o mesmo serviço continua levando as aplicações inteiras
    assert de_aplicacao.json()["fronteira"] == []
    assert any(s["nome"] == "AmazonEC2" for s in de_aplicacao.json()["servicos"])
    assert p["custo"] < de_aplicacao.json()["custo"]


async def test_so_o_servico_nao_combina_com_aplicacao(
    app: FastAPI, analise: tuple[str, str]
) -> None:
    com, _ = analise
    async with _cliente(app) as c:
        r = await c.post(
            f"/v1/analyses/{com}/migration-plans",
            json={
                "aplicacoes": ["checkout"],
                "servicos": ["AmazonRDS"],
                "somente_os_servicos": True,
            },
        )
    assert r.status_code == 422, r.text
    assert "só o serviço" in r.text


@pytest.mark.parametrize("destino", ["gcp", "azure", "oci"])
async def test_terraform_so_do_banco_cria_o_banco_sem_as_maquinas(
    app_tf: FastAPI, analise: tuple[str, str], destino: str
) -> None:
    com, _ = analise
    async with _cliente(app_tf) as c:
        g = await _gerar(c, com, destino, {"servicos": ["AmazonRDS"], "somente_os_servicos": True})
    assert _BANCO.search(g["tf"]), (g["tipos"], g["unsupported_items"])
    assert not _VM.search(g["tf"]), "só o banco: nenhuma máquina virtual"
    avisos = "\n".join(g["warnings"])
    assert "atravessam a fronteira" in avisos
    # A lista inteira está no README; a resposta traz as primeiras e o total
    assert "AmazonEC2 (checkout) fica na origem e acessa dados em AmazonRDS (checkout)" in g[
        "readme"
    ]
    assert "### O que fica do outro lado (fronteira)" in g["readme"]
    assert "**forte**" in g["readme"]
    if _TEM_TERRAFORM:
        assert g["validation"]["validado"] is True, g["validation"]["erros"]


# Sem a Azure: o banco do CI não tem preço de VM dela, e nenhum pool tem SKU da Azure (nem
# o pacote da conta inteira cria VM lá). As quatro nuvens com SKU estão no teste unitário
# do pacote (`test_terraform_escopo_pacote`), sobre os payloads congelados.
@pytest.mark.parametrize("destino", ["gcp", "oci"])
async def test_terraform_so_da_vm_cria_as_maquinas_sem_o_banco(
    app_tf: FastAPI, analise: tuple[str, str], destino: str
) -> None:
    com, _ = analise
    async with _cliente(app_tf) as c:
        g = await _gerar(c, com, destino, {"servicos": ["AmazonEC2"], "somente_os_servicos": True})
    assert _VM.search(g["tf"]), (g["tipos"], g["unsupported_items"], g["warnings"][:3])
    assert not _BANCO.search(g["tf"]), "só a VM: o banco fica na origem"
    assert "atravessam a fronteira" in "\n".join(g["warnings"])
    assert (
        "AmazonEC2 (checkout) migra e acessa dados em AmazonRDS (checkout), que fica na origem"
        in g["readme"]
    )
    if _TEM_TERRAFORM:
        assert g["validation"]["validado"] is True, g["validation"]["erros"]


async def test_terraform_da_aplicacao_continua_como_antes(
    app_tf: FastAPI, analise: tuple[str, str]
) -> None:
    com, _ = analise
    async with _cliente(app_tf) as c:
        g = await _gerar(c, com, "oci", {"aplicacoes": ["checkout"]})
    # A aplicação leva máquina e banco, e o README fala de grupos, não de fronteira
    assert _VM.search(g["tf"]) and _BANCO.search(g["tf"])
    assert "### O que fica do outro lado (fronteira)" not in g["readme"]
    assert not any("atravessam a fronteira" in a for a in g["warnings"])
    if _TEM_TERRAFORM:
        assert g["validation"]["validado"] is True, g["validation"]["erros"]








async def test_fronteira_inclui_quem_nao_tem_linha_na_fatura(
    app: FastAPI, analise: tuple[str, str]
) -> None:
    """O monitoramento lê todos os bancos sem linha própria na fatura: o serviço vem do tipo
    da VM, e a ligação aparece na fronteira do mesmo jeito."""
    com, _ = analise
    async with _cliente(app) as c:
        p = (
            await c.post(
                f"/v1/analyses/{com}/migration-plans",
                json={"servicos": ["AmazonRDS"], "somente_os_servicos": True},
            )
        ).json()
    monitor = {f["para"] for f in p["fronteira"] if f["de"] == "AmazonEC2 (monitoramento)"}
    assert {"AmazonRDS (checkout)", "AmazonRDS (auth)"} <= monitor


async def test_terraform_so_do_banco_avisa_a_regra_que_falta_no_destino(
    app_tf: FastAPI, analise: tuple[str, str]
) -> None:
    com, _ = analise
    async with _cliente(app_tf) as c:
        g = await _gerar(c, com, "oci", {"servicos": ["AmazonRDS"], "somente_os_servicos": True})
    assert g["warnings"][0].startswith("Só os serviços escolhidos:")
    assert g["warnings"][1].startswith(
        "Em OCI o banco do pacote fica na subrede de dados, mas sem a regra de entrada"
    )
    assert "libere tcp/5432 a partir da rede de origem" in g["warnings"][1]
    # O banco fica na subrede privada de dados, não fora da rede nova
    assert "ficou fora da rede nova" not in "\n".join(g["warnings"])
    assert "subnet_id" in g["tf"]
