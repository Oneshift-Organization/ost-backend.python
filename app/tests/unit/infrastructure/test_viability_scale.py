"""Escala do `viability_score` vinda do MCP.

O que estes testes protegem: a conversão não pode depender do **valor**. A regra
antiga (`if 0 <= score <= 100: score *= 10`) transformava 80 em 800 — uma
migração ruim virava excelente — e só errava nos casos ruins, que são justamente
os que a análise precisa acertar para desaconselhar a migração.
"""

from __future__ import annotations

import pytest

from app.core import config
from app.infrastructure.llm.orchestrator import _normalize_mcp_viability_score
from app.schemas.analysis_v2 import AnalysisV2Response


@pytest.fixture
def _escala(monkeypatch: pytest.MonkeyPatch):
    def _definir(valor: str) -> None:
        monkeypatch.setenv("MCP_VIABILITY_SCALE", valor)
        config.get_settings.cache_clear()

    yield _definir
    config.get_settings.cache_clear()


def _resposta(score: int) -> AnalysisV2Response:
    return AnalysisV2Response(viability_score=score)


# ── o caso que o código antigo errava ───────────────────────────────────


def test_score_baixo_na_escala_da_api_nao_e_inflado(_escala) -> None:
    """80 em 1000 é migração ruim. A regra antiga devolvia 800 — excelente."""
    _escala("0-1000")
    assert _normalize_mcp_viability_score(_resposta(80)).viability_score == 80


def test_score_baixo_na_escala_do_mcp_e_convertido(_escala) -> None:
    """O mesmo 80, com o prompt declarando 0..100, vale 800. O número de entrada
    é idêntico — só a declaração muda."""
    _escala("0-100")
    assert _normalize_mcp_viability_score(_resposta(80)).viability_score == 800


# ── a conversão não olha para a magnitude ───────────────────────────────


@pytest.mark.parametrize("score", [0, 1, 42, 99, 100])
def test_escala_da_api_nunca_converte(_escala, score: int) -> None:
    _escala("0-1000")
    assert _normalize_mcp_viability_score(_resposta(score)).viability_score == score


@pytest.mark.parametrize(("origem", "esperado"), [(0, 0), (1, 10), (72, 720), (100, 1000)])
def test_escala_do_mcp_sempre_converte(_escala, origem: int, esperado: int) -> None:
    _escala("0-100")
    r = _normalize_mcp_viability_score(_resposta(origem))
    assert r.viability_score == esperado


# ── modelo desobedecendo a escala declarada ─────────────────────────────


def test_score_acima_da_escala_declarada_nao_e_convertido(_escala) -> None:
    """Prompt pede 0..100 e o modelo devolve 720: quem errou foi o modelo.

    Converter daria 7200, e `model_copy` **não revalida** — o valor entraria no
    payload sem passar pelo `le=1000` do schema.
    """
    _escala("0-100")
    r = _normalize_mcp_viability_score(_resposta(720))
    assert r.viability_score == 720


def test_conversao_nunca_estoura_o_teto_da_api(_escala) -> None:
    _escala("0-100")
    r = _normalize_mcp_viability_score(_resposta(100))
    assert r.viability_score <= 1000


# ── o default preserva o comportamento de hoje ──────────────────────────


def test_default_e_a_escala_do_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ninguém precisa configurar nada para continuar como estava — o que muda é
    que a regra virou declaração em vez de palpite."""
    monkeypatch.delenv("MCP_VIABILITY_SCALE", raising=False)
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().mcp_viability_scale == "0-100"
        assert _normalize_mcp_viability_score(_resposta(72)).viability_score == 720
    finally:
        config.get_settings.cache_clear()
