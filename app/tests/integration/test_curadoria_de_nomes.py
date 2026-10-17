"""Curadoria de nomes de serviço (fatura como fonte, etapa A, decisão 2.11).

O fluxo: a coleta põe na fila o nome que o DE-PARA não reconhece, a curadoria aprova
escolhendo a linha do DE-PARA, e a análise nova passa a reconhecer o nome. Fatura de
teste fica fora; nenhum cliente decide.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import _DEV_USER
from app.api.v1.routes import curadoria
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.service_repositories import PGServiceComparisonRepository
from app.main import create_app
from app.schemas.auth import UserClaims

HTTP_USER = UUID(_DEV_USER.sub)
_NOME = "Zz Managed Streaming for Apache Kafka"


@pytest.fixture
def _app():
    return create_app()


def _cliente(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _fatura(nome_do_arquivo: str) -> UUID:
    """Uma fatura AWS com uma linha do serviço de nome novo."""
    rid = uuid4()
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "insert into billing_reports (id, user_id, provider, status, source_filename,"
                " created_at, updated_at) values (:id, :u, 'aws', 'DONE', :f, now(), now())"
            ),
            {"id": rid, "u": HTTP_USER, "f": nome_do_arquivo},
        )
        await s.execute(
            text(
                "insert into billing_enriched (id, report_id, user_id, provider, billing_period,"
                " service_name, cost, currency, source_line_hash, pricing_source,"
                " pricing_confidence, created_at, component_type)"
                " values (:id, :r, :u, 'aws', '2026-03', :n, 10, 'USD', :h, 'cur', 'high',"
                " now(), 'unclassified')"
            ),
            {"id": uuid4(), "r": rid, "u": HTTP_USER, "n": _NOME, "h": uuid4().hex},
        )
        await s.commit()
    return rid


async def _limpar(*reports: UUID) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        for rid in reports:
            await s.execute(text("delete from billing_enriched where report_id=:r"), {"r": rid})
            await s.execute(text("delete from billing_reports where id=:r"), {"r": rid})
        await s.execute(
            text("delete from service_name_suggestions where nome_na_fatura=:n"), {"n": _NOME}
        )
        await s.commit()


def _da_fila(resposta) -> list[dict]:
    return [s for s in resposta.json() if s["nome_na_fatura"] == _NOME]


@pytest.mark.asyncio(loop_scope="session")
async def test_coleta_aprovacao_e_apelido_na_analise(_app, _db_ready) -> None:
    real = await _fatura("cur-2026-03.csv.gz")
    teste = await _fatura("mock_aws_cur2_2026-03.csv.gz")
    try:
        async with _cliente(_app) as c:
            r = await c.post("/v1/admin/service-name-suggestion-collections")
            assert r.status_code == 201, r.text
            [sugestao] = _da_fila(await c.get("/v1/admin/service-name-suggestions"))
            # Só a fatura real conta: a de teste (mock) fica fora
            assert sugestao["faturas"] == 1
            assert sugestao["clientes"] == 1
            assert sugestao["status"] == "pendente"

            # Aprovar exige uma linha do DE-PARA do mesmo provedor
            r = await c.patch(
                f"/v1/admin/service-name-suggestions/{sugestao['id']}",
                json={"status": "aprovada", "alvo": "Serviço que não existe"},
            )
            assert r.status_code == 422
            assert r.json()["detail"]["codigo"] == "ALVO_FORA_DO_DE_PARA"

            r = await c.patch(
                f"/v1/admin/service-name-suggestions/{sugestao['id']}",
                json={"status": "aprovada", "alvo": "Amazon MSK"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["alvo"] == "Amazon MSK"
            assert r.json()["decidido_por"]

            # Decidido, sai da fila de pendentes, e a coleta seguinte não o devolve
            await c.post("/v1/admin/service-name-suggestion-collections")
            assert _da_fila(await c.get("/v1/admin/service-name-suggestions")) == []

        sm = get_sessionmaker()
        async with sm() as s:
            apelidos = await PGServiceComparisonRepository(s).apelidos_aprovados("aws")
        assert "Amazon MSK" in apelidos.values()
    finally:
        await _limpar(real, teste)


@pytest.mark.asyncio(loop_scope="session")
async def test_quem_nao_e_curador_nao_decide(_app, _db_ready, monkeypatch) -> None:
    """Com autenticação ligada, só e-mail da lista de curadores."""
    cliente_comum = UserClaims(sub=str(uuid4()), email="cliente@empresa.com", role="authenticated")
    with pytest.raises(Exception) as recusa:
        curadoria._curador(cliente_comum)
    assert recusa.value.status_code == 403
    assert recusa.value.detail["codigo"] == "ACESSO_DE_CURADORIA"

    configuracao = SimpleNamespace(curadores="Cliente@Empresa.com", auth_enabled=True)
    monkeypatch.setattr(curadoria, "get_settings", lambda: configuracao)
    assert curadoria._curador(cliente_comum) is cliente_comum
