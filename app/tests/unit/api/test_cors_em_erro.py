"""CORS precisa sobreviver a um erro interno.

O sintoma que originou estes testes: o front em `https://oneshift.com.br` recebia
*"blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present"* ao
chamar `GET /v1/analyses`. A configuração de CORS estava correta o tempo todo —
o que acontecia é que uma exceção não tratada virava 500 pelo ServerErrorMiddleware
do Starlette, que fica por FORA do CORSMiddleware. A resposta saía sem o header, e
o browser culpava o CORS por um defeito de servidor.

Custou tempo de investigação porque a mensagem aponta para o lugar errado. Estes
testes existem para que ninguém pague esse custo de novo.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core import config
from app.core.config import get_settings
from app.main import ErroInternoMiddleware, create_app

# Origem tirada da própria config: `.env.dev` restringe a localhost enquanto
# produção libera oneshift.com.br, e o teste precisa valer nos dois.
ORIGEM = get_settings().cors_origins[0]


@pytest.fixture
def app_que_quebra() -> FastAPI:
    """App real (mesma stack de middlewares) com uma rota que explode."""
    app = create_app()

    @app.get("/_boom")
    def _boom() -> None:
        raise RuntimeError("falha interna qualquer")

    return app


def test_erro_interno_ainda_devolve_cors(app_que_quebra: FastAPI) -> None:
    """500 SEM `Access-Control-Allow-Origin` faz o browser reportar erro de CORS
    e esconder a causa real."""
    c = TestClient(app_que_quebra, raise_server_exceptions=False)
    r = c.get("/_boom", headers={"Origin": ORIGEM})

    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == ORIGEM


def test_erro_interno_tem_contrato_de_erro(app_que_quebra: FastAPI) -> None:
    """Um agente precisa distinguir RE-TENTE de DESISTA — 500 com corpo vazio
    não permite decidir."""
    c = TestClient(app_que_quebra, raise_server_exceptions=False)
    d = c.get("/_boom", headers={"Origin": ORIGEM}).json()["detail"]

    assert d["codigo"] == "ERRO_INTERNO"
    assert d["retryable"] is True


def test_request_id_do_cliente_volta_na_resposta(app_que_quebra: FastAPI) -> None:
    """Sem eco do X-Request-ID não há como casar o erro visto no browser com a
    linha de log do servidor."""
    c = TestClient(app_que_quebra, raise_server_exceptions=False)
    r = c.get("/_boom", headers={"Origin": ORIGEM, "X-Request-ID": "abc-123"})

    assert r.headers.get("x-request-id") == "abc-123"


def test_resposta_normal_segue_com_cors(app_que_quebra: FastAPI) -> None:
    """Rede de segurança: o middleware novo não pode atrapalhar o caminho feliz."""
    c = TestClient(app_que_quebra)
    r = c.get("/v1/health", headers={"Origin": ORIGEM})

    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ORIGEM


def test_ordem_dos_middlewares_esta_correta() -> None:
    """Trava a ordem, que é a essência do bug.

    Starlette aplica na ordem inversa do registro: o último adicionado é o mais
    externo. A captura precisa ser registrada ANTES do CORS para ficar por dentro
    dele. Inverter os dois recria o bug — e nenhum outro teste unitário pegaria,
    porque a configuração de CORS continuaria "correta".
    """
    tipos = [m.cls for m in create_app().user_middleware]
    assert tipos.index(CORSMiddleware) < tipos.index(ErroInternoMiddleware), (
        "CORSMiddleware precisa ser mais externo que ErroInternoMiddleware"
    )


# ── docs ────────────────────────────────────────────────────────────────


def test_docs_ligadas_por_padrao() -> None:
    """O contrato da API é público de propósito: é o que o time do agente/MCP
    consome para integrar."""
    c = TestClient(create_app())
    assert c.get("/docs").status_code == 200
    assert c.get("/openapi.json").status_code == 200


def test_docs_podem_ser_desligadas(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DOCS_ENABLED=false` fecha a superfície sem alterar código."""
    monkeypatch.setenv("DOCS_ENABLED", "false")
    config.get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
    finally:
        config.get_settings.cache_clear()
