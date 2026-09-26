"""Correlação pela API com billing e inventário mock das mesmas contas (I6).

O billing passa pelo pipeline real de ingestão; o inventário, pela API. A
correlação precisa achar tudo nas contas exportadas, apontar a conta que ficou
de fora como a lacuna, e explicar o snapshot de EBS (o Config não o registra).
"""

from __future__ import annotations

from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.core import config
from app.infrastructure.database.session import get_sessionmaker
from app.main import create_app

from .conftest import _cleanup, _ingest

HTTP_USER = UUID(_DEV_USER.sub)
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INVENTORY_ENABLED", "true")
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def billings(_db_ready, mocks_billing):
    ids = {
        "aws": await _ingest(mocks_billing.cur, user_id=HTTP_USER),
        "gcp": await _ingest(mocks_billing.bigquery, user_id=HTTP_USER),
        "azure": await _ingest(mocks_billing.azure, user_id=HTTP_USER),
        "oci": await _ingest(mocks_billing.oci, user_id=HTTP_USER),
    }
    try:
        yield ids
    finally:
        for rid in ids.values():
            await _cleanup(rid)


async def _enviar(c: AsyncClient, arquivos) -> UUID:
    r = await c.post(
        "/v1/inventories",
        files=[("files", (a.name, a.read_bytes(), "application/octet-stream")) for a in arquivos],
    )
    assert r.status_code == 202, r.text
    iid = UUID(r.json()["inventory_id"])
    assert (await c.get(f"/v1/inventories/{iid}")).json()["status"] == "done"
    return iid


async def _apagar(iid: UUID) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("delete from inventories where id = :i"), {"i": iid})
        await s.commit()


async def test_aws_acha_as_contas_exportadas_e_aponta_a_que_faltou(
    app, billings, mocks_inventario
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        iid = await _enviar(c, mocks_inventario.aws)
        try:
            r = await c.get(f"/v1/inventories/{iid}/correlation?report_id={billings['aws']}")
            assert r.status_code == 200, r.text
            corr = r.json()
        finally:
            await _apagar(iid)

    assert corr["moeda"] == "USD"
    assert 0.5 < corr["fracao_encontrada"] < 1
    # Toda lacuna é da conta que ficou fora do snapshot, ou snapshot de EBS.
    contas = {x["conta"] for x in corr["lacunas_por_escopo"]}
    assert "333333333333" in contas
    for lacuna in corr["lacunas_por_escopo"]:
        if lacuna["conta"] != "333333333333":
            assert all(e.startswith("snap-") for e in lacuna["exemplos"]), lacuna
    maior = corr["lacunas_por_escopo"][0]
    assert maior["conta"] == "333333333333"
    # Nas contas exportadas, tudo que tem custo foi achado.
    resumo = mocks_inventario.resumos["aws"]
    assert corr["recursos_encontrados"] == len(resumo.ids_billing_incluidos)
    assert corr["recursos_do_billing"] == len(
        resumo.ids_billing_incluidos | resumo.ids_billing_fora
    )
    # O mesmo id de "bucket" em S3 e ECR no mock: casou, contado como ambíguo.
    assert corr["ambiguos"] > 0
    # Ociosos com custo: discos sem anexo e máquinas paradas pagam disco.
    motivos = {o["motivo"] for o in corr["ociosos"] if o["custo"] > 0}
    assert {"disco_sem_anexo", "instancia_parada"} <= motivos
    assert corr["alinhamento"]["desalinhado"] is False
    esperado_risco = corr["fracao_encontrada"] < 0.8
    assert ("cobertura_baixa" in {x["regra"] for x in corr["riscos"]}) is esperado_risco


@pytest.mark.parametrize("provedor", ["gcp", "azure", "oci"])
async def test_nas_outras_nuvens_tudo_e_achado(app, billings, mocks_inventario, provedor) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        iid = await _enviar(c, getattr(mocks_inventario, provedor))
        try:
            corr = (
                await c.get(f"/v1/inventories/{iid}/correlation?report_id={billings[provedor]}")
            ).json()
        finally:
            await _apagar(iid)
    # GCP: pelo ID numérico do `global_name`; Azure: ids em caixa mista.
    assert corr["fracao_encontrada"] == 1.0, corr["lacunas_por_escopo"][:3]
    assert corr["lacunas_por_escopo"] == []


async def test_billing_de_outra_nuvem_e_recusado(app, billings, mocks_inventario) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        iid = await _enviar(c, mocks_inventario.oci)
        try:
            outra = await c.get(f"/v1/inventories/{iid}/correlation?report_id={billings['aws']}")
            inexistente = await c.get(
                f"/v1/inventories/{iid}/correlation?report_id=00000000-0000-0000-0000-000000000000"
            )
        finally:
            await _apagar(iid)
    assert outra.status_code == 422
    assert outra.json()["detail"]["codigo"] == "INVENTARIO_DE_OUTRA_NUVEM"
    assert inexistente.status_code == 404
    assert inexistente.json()["detail"]["codigo"] == "REPORT_NAO_ENCONTRADO"
