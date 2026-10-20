"""Superfície `/v1/terraform-generations` — interruptor, isolamento e ciclo.

O que mais importa aqui é o **desligado**: com a flag `false`, as rotas respondem
404 como se não existissem. É o que sustenta a promessa de que a funcionalidade
pode estar deployada e continuar invisível.
"""

from __future__ import annotations

import io
import json
import zipfile
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.core import config
from app.domain.entities.analysis_job import AnalysisJob, AnalysisStatus
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.main import create_app

from .conftest import FIXTURES, _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)


@pytest.fixture
def _app_ligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERRAFORM_GENERATION_ENABLED", "true")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


@pytest.fixture
def _app_desligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERRAFORM_GENERATION_ENABLED", "false")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _corpo(job_id, **kw) -> dict:
    base = {
        "analysis_job_id": str(job_id),
        "project_name": "migracao-teste",
        "environment": "dev",
        "target_provider": "gcp",
        "region": "southamerica-east1",
        "network": {"mode": "create", "cidr": "10.20.0.0/16"},
    }
    return {**base, **kw}


async def _analise_pronta(report_id, payload: dict, user_id=HTTP_USER) -> AnalysisJob:
    sm = get_sessionmaker()
    async with sm() as s:
        job = AnalysisJob(
            job_id=uuid4(),
            user_id=user_id,
            report_id=report_id,
            llm_input_payload=payload,
        )
        job.status = AnalysisStatus.COMPLETED
        await PgAnalysisJobRepository(s).create(job)
        await s.commit()
    return job


# ── o interruptor ───────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_desligado_a_superficie_nao_existe(_app_desligado, _db_ready) -> None:
    async with _cliente(_app_desligado) as c:
        post = await c.post("/v1/terraform-generations", json=_corpo(uuid4()))
        get = await c.get(f"/v1/terraform-generations/{uuid4()}")
        dl = await c.get(f"/v1/terraform-generations/{uuid4()}/download")

    for resp in (post, get, dl):
        assert resp.status_code == 404
        assert resp.json()["detail"]["codigo"] == "GERACAO_TERRAFORM_DESLIGADA"
        assert resp.json()["detail"]["retryable"] is False


# ── validação de entrada ────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_analise_inexistente_e_404(_app_ligado, _db_ready) -> None:
    async with _cliente(_app_ligado) as c:
        r = await c.post("/v1/terraform-generations", json=_corpo(uuid4()))
    assert r.status_code == 404
    assert r.json()["detail"]["codigo"] == "ANALISE_NAO_ENCONTRADA"


@pytest.mark.asyncio(loop_scope="session")
async def test_analise_de_outro_usuario_e_404_nao_403(_app_ligado, _db_ready) -> None:
    """403 confirmaria que a análise existe. 404 não conta nada."""
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise_pronta(rid, {"provedor_origem": "aws"}, user_id=uuid4())
        async with _cliente(_app_ligado) as c:
            r = await c.post("/v1/terraform-generations", json=_corpo(job.job_id))
        assert r.status_code == 404
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_cidr_invalido_e_recusado(_app_ligado, _db_ready) -> None:
    """Rede é a única parte que vem inteira do usuário — validar é a defesa
    contra um `.tf` que não aplica."""
    async with _cliente(_app_ligado) as c:
        r = await c.post(
            "/v1/terraform-generations",
            json=_corpo(uuid4(), network={"mode": "create", "cidr": "10.20.0.0/28"}),
        )
    assert r.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_provedor_sem_template_e_recusado(_app_ligado, _db_ready) -> None:
    """`gcp`, `aws`, `azure` e `oci` têm adaptador. Qualquer outro é recusado na
    borda — melhor que gerar um pacote que ninguém sabe escrever."""
    async with _cliente(_app_ligado) as c:
        r = await c.post(
            "/v1/terraform-generations", json=_corpo(uuid4(), target_provider="ibm")
        )
    assert r.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("provedor", ["gcp", "aws", "azure", "oci"])
async def test_as_quatro_nuvens_sao_aceitas(_app_ligado, _db_ready, provedor) -> None:
    """Passa da validação de entrada: o 404 é da análise inexistente, não do
    provedor. Se o provedor fosse recusado viria 422."""
    async with _cliente(_app_ligado) as c:
        r = await c.post(
            "/v1/terraform-generations",
            json=_corpo(uuid4(), target_provider=provedor, region="us-central1"),
        )
    assert r.status_code == 404
    assert r.json()["detail"]["codigo"] == "ANALISE_NAO_ENCONTRADA"


# ── ciclo completo ──────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_gera_e_baixa_o_pacote(_app_ligado, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        # A request real da análise é a fonte — é ela que o usuário aprovou.
        async with _cliente(_app_ligado) as c:
            req = await c.get(f"/v1/analyses/{rid}/request?recalcular=true")
            assert req.status_code == 200, req.text
            payload = {k: v for k, v in req.json().items() if k != "_meta"}

        job = await _analise_pronta(rid, payload)

        async with _cliente(_app_ligado) as c:
            post = await c.post("/v1/terraform-generations", json=_corpo(job.job_id))
            assert post.status_code == 202, post.text
            gid = post.json()["generation_id"]
            assert post.json()["status"] == "PENDING"

            estado = await c.get(f"/v1/terraform-generations/{gid}")
            assert estado.status_code == 200
            assert estado.json()["status"] == "COMPLETED", estado.json().get("error")

            dl = await c.get(f"/v1/terraform-generations/{gid}/download")

        assert dl.status_code == 200
        assert dl.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
            nomes = z.namelist()
            manifesto = json.loads(
                z.read("migracao-teste-terraform/manifest.json").decode()
            )
        assert any(n.endswith("main.tf") for n in nomes)
        assert any(n.endswith("README.md") for n in nomes)
        # O pacote nunca declara economia: a cotação usa região de referência.
        assert manifesto["custo_declarado"] is None
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_o_zip_nao_leva_credencial(_app_ligado, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise_pronta(rid, {"provedor_origem": "aws"})
        async with _cliente(_app_ligado) as c:
            post = await c.post("/v1/terraform-generations", json=_corpo(job.job_id))
            gid = post.json()["generation_id"]
            dl = await c.get(f"/v1/terraform-generations/{gid}/download")

        with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
            junto = "\n".join(
                z.read(n).decode("utf-8", "ignore") for n in z.namelist()
            ).lower()
        for suspeito in ("private_key", "-----begin", "secret_access_key"):
            assert suspeito not in junto
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_analise_incompleta_pede_para_esperar(_app_ligado, _db_ready) -> None:
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()

        async with _cliente(_app_ligado) as c:
            r = await c.post("/v1/terraform-generations", json=_corpo(job.job_id))
        assert r.status_code == 409
        assert r.json()["detail"]["codigo"] == "ANALISE_NAO_CONCLUIDA"
        assert r.json()["detail"]["retryable"] is True
    finally:
        await _limpar(rid)


# ── Terraform do escopo (T5) ────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_escopo_sem_inventario_ligado_e_404(
    _app_ligado, _db_ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVENTORY_ENABLED", "false")
    config.get_settings.cache_clear()
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise_pronta(rid, {"provedor_origem": "aws"})
        async with _cliente(_app_ligado) as c:
            r = await c.post(
                "/v1/terraform-generations",
                json=_corpo(job.job_id, escopo={"aplicacoes": ["checkout"]}),
            )
        assert r.status_code == 404
        assert r.json()["detail"]["codigo"] == "INVENTARIO_DESLIGADO"
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_escopo_de_analise_sem_inventario_traz_o_erro_do_plano(
    _app_ligado, _db_ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Os erros do escopo são os da rota do plano de migração: a tela já os trata."""
    monkeypatch.setenv("INVENTORY_ENABLED", "true")
    config.get_settings.cache_clear()
    rid = await _ingest(FIXTURES / "aws_cost_explorer_full.csv", user_id=HTTP_USER)
    try:
        job = await _analise_pronta(rid, {"provedor_origem": "aws"})
        async with _cliente(_app_ligado) as c:
            r = await c.post(
                "/v1/terraform-generations",
                json=_corpo(job.job_id, escopo={"aplicacoes": ["checkout"]}),
            )
            invalido = await c.post(
                "/v1/terraform-generations",
                json=_corpo(job.job_id, escopo={"aplicacao": ["checkout"]}),
            )
        assert r.status_code == 409
        assert r.json()["detail"]["codigo"] == "INVENTARIO_AUSENTE"
        assert invalido.status_code == 422
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_o_legado_nao_muda_com_a_flag_ligada(_app_ligado, _db_ready) -> None:
    async with _cliente(_app_ligado) as c:
        r = await c.get(f"/v1/analyses/{uuid4()}/result")
    assert r.status_code == 404
    assert r.json()["detail"] == "analysis job not found", "contrato legado intacto"


async def _limpar(report_id) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text("delete from terraform_generation_jobs where report_id=:r"),
            {"r": report_id},
        )
        await s.execute(
            text("delete from analysis_jobs where report_id=:r"), {"r": report_id}
        )
        await s.commit()
    await _cleanup(report_id)
