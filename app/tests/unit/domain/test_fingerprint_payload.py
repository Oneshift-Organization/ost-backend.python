"""Hash canônico: a identidade do snapshot e do envio do agente."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.fingerprint_payload import hash_canonico, json_canonico


def test_ordem_das_chaves_nao_muda_o_hash() -> None:
    a = {"b": 1, "a": {"y": [1, 2], "x": None}}
    b = {"a": {"x": None, "y": [1, 2]}, "b": 1}
    assert hash_canonico(a) == hash_canonico(b)


def test_ordem_das_listas_muda_o_hash() -> None:
    # Lista é sequência: onda 1 antes da 2 não é o mesmo que o contrário.
    assert hash_canonico([1, 2]) != hash_canonico([2, 1])


def test_json_canonico_sem_espacos_e_em_utf8() -> None:
    assert json_canonico({"região": "São Paulo", "n": 1.5}) == '{"n":1.5,"região":"São Paulo"}'


def test_hash_e_sha256_hexadecimal() -> None:
    h = hash_canonico({"a": 1})
    assert len(h) == 64
    assert h == "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"


def test_inteiro_e_decimal_de_mesmo_valor_sao_diferentes() -> None:
    # `1` e `1.0` saem diferentes no JSON; o hash acompanha o que o cliente recebe.
    assert hash_canonico({"v": 1}) != hash_canonico({"v": 1.0})


def test_decimal_e_recusado_em_vez_de_virar_texto() -> None:
    with pytest.raises(TypeError):
        hash_canonico({"custo": Decimal("1.50")})


def test_nan_e_recusado() -> None:
    with pytest.raises(ValueError):
        hash_canonico({"custo": float("nan")})
