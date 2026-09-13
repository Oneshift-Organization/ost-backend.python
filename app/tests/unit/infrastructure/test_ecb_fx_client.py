"""Parsing das cotações do BCE. Offline — a resposta é injetada.

O risco que estes testes cobrem não é a rede: é a **direção da fração**. Um
câmbio invertido produz números plausíveis na direção errada, e nada na análise
o denunciaria.
"""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import pytest

from app.infrastructure.fx import ecb_fx_client
from app.infrastructure.fx.ecb_fx_client import FONTE, fetch_fx_rates


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _responder(monkeypatch: pytest.MonkeyPatch, corpo: object) -> None:
    def _fake(_req: object, timeout: float = 0) -> _Resposta:
        return _Resposta(json.dumps(corpo).encode())

    monkeypatch.setattr(ecb_fx_client.urllib.request, "urlopen", _fake)


_OK = {"amount": 1.0, "base": "USD", "date": "2026-09-07", "rates": {"BRL": 5.1261}}


def test_taxa_e_moeda_de_origem_por_dolar(monkeypatch: pytest.MonkeyPatch) -> None:
    """`taxa` é 'unidades da origem por 1 USD' — 5,12 reais por dólar.

    Se a fração viesse invertida (0,195), a conversão **multiplicaria** o custo
    em BRL por ~26 em vez de dividir, e o bill GCP apareceria como o mais caro
    do mundo sem nenhum aviso.
    """
    _responder(monkeypatch, _OK)
    (brl,) = fetch_fx_rates(("BRL",))
    assert brl.par == "BRL/USD"
    assert brl.taxa == Decimal("5.1261")
    assert brl.taxa > 1, "real vale menos que dólar"


def test_carrega_data_e_fonte(monkeypatch: pytest.MonkeyPatch) -> None:
    """É o par (data, fonte) que dá ao ledger o que o fallback do env não tem."""
    _responder(monkeypatch, _OK)
    (brl,) = fetch_fx_rates(("BRL",))
    assert brl.data == date(2026, 9, 7)
    assert brl.fonte == FONTE


def test_moeda_de_destino_e_ignorada(monkeypatch: pytest.MonkeyPatch) -> None:
    """USD/USD não é câmbio. Pedi-lo à fonte devolveria erro."""
    _responder(monkeypatch, _OK)
    assert fetch_fx_rates(("USD",)) == []


def test_resposta_sem_data_nao_grava_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uma taxa sem data não é melhor que o env — é o env com passos extras."""
    _responder(monkeypatch, {**_OK, "date": None})
    assert fetch_fx_rates(("BRL",)) == []


def test_taxa_invalida_e_descartada_sem_derrubar_as_outras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _responder(
        monkeypatch,
        {**_OK, "rates": {"BRL": 5.1261, "EUR": 0, "GBP": "x"}},
    )
    pares = {t.par for t in fetch_fx_rates(("BRL", "EUR", "GBP"))}
    assert pares == {"BRL/USD"}


def test_fonte_fora_do_ar_degrada_para_vazio(monkeypatch: pytest.MonkeyPatch) -> None:
    """O sync não é caminho crítico: derrubá-lo apagaria as taxas já gravadas."""

    def _explode(*_a: object, **_k: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(ecb_fx_client.urllib.request, "urlopen", _explode)
    assert fetch_fx_rates(("BRL",)) == []
