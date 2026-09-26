"""Natureza da linha: o que fica fora da base de análise (RN3)."""

from __future__ import annotations

import pytest

from app.domain.value_objects.charge_kind import (
    FORA_DA_BASE,
    VALORES,
    ChargeKind,
    fora_da_base,
)


@pytest.mark.parametrize("natureza", ["tax", "credit", "refund", "adjustment"])
def test_fora_da_base(natureza: str) -> None:
    assert fora_da_base(natureza) is True


@pytest.mark.parametrize("natureza", ["usage", "commitment", "other"])
def test_dentro_da_base(natureza: str) -> None:
    # Compromisso FICA na base: é custo real (ocioso, no amortizado). Só não
    # vira capacidade — isso é decisão do agregador, não deste filtro.
    assert fora_da_base(natureza) is False


def test_legado_sem_natureza_nunca_e_excluido_aqui() -> None:
    # O legado tem os próprios filtros (raw_payload). Decidir por `None` aqui
    # mudaria o comportamento travado pela caracterização.
    assert fora_da_base(None) is False


def test_vocabulario_fechado() -> None:
    assert {k.value for k in ChargeKind} == VALORES
    assert FORA_DA_BASE.issubset(set(ChargeKind))
    assert set(ChargeKind) - FORA_DA_BASE
