"""Derivação da expectativa de migração (Onda 8 / E3).

A classe 6R é calculada no BACKEND a partir da metadata curada — a IA recebe
pronta e só narra. Sem dado curado, devolve None (honesto) em vez de palpite.
"""
from __future__ import annotations

import pytest

from app.domain.services.migration_expectation import derive_expectation


@pytest.mark.parametrize(
    ("metadata", "classe", "esforco"),
    [
        # Exige reescrita -> refactor, independente do lock-in (ex.: RDS).
        ({"requires_refactoring": True, "vendor_lock_in_level": "medium"}, "refactor", "alto"),
        ({"requires_refactoring": True, "vendor_lock_in_level": "low"}, "refactor", "alto"),
        # Sem reescrita, mas preso ao fornecedor -> ajuste de plataforma (ex.: EC2).
        ({"requires_refactoring": False, "vendor_lock_in_level": "medium"}, "replatform", "medio"),
        ({"requires_refactoring": False, "vendor_lock_in_level": "high"}, "replatform", "medio"),
        # Sem reescrita e portável -> lift-and-shift (ex.: S3, CloudWatch).
        ({"requires_refactoring": False, "vendor_lock_in_level": "low"}, "rehost", "baixo"),
    ],
)
def test_classe_6r_derivada(metadata: dict, classe: str, esforco: str) -> None:
    exp = derive_expectation(metadata)
    assert exp is not None
    assert exp.classe == classe
    assert exp.esforco == esforco


def test_lock_in_normalizado() -> None:
    exp = derive_expectation({"requires_refactoring": False, "vendor_lock_in_level": " HIGH "})
    assert exp is not None
    assert exp.lock_in == "high"
    assert exp.classe == "replatform"


@pytest.mark.parametrize("metadata", [None, {}, {"pricing_model": "per_gb_monthly"}])
def test_sem_dado_curado_devolve_none(metadata: dict | None) -> None:
    """Melhor a IA saber que não temos o dado do que receber palpite."""
    assert derive_expectation(metadata) is None


def test_lock_in_sozinho_ja_deriva() -> None:
    """Metadata parcial (só lock-in) ainda produz classe utilizável."""
    exp = derive_expectation({"vendor_lock_in_level": "high"})
    assert exp is not None
    assert exp.classe == "replatform"
    assert exp.requer_refatoracao is False


def test_as_dict_omite_lock_in_ausente() -> None:
    exp = derive_expectation({"requires_refactoring": True})
    assert exp is not None
    d = exp.as_dict()
    assert d == {
        "classe_migracao": "refactor",
        "esforco": "alto",
        "requer_refatoracao": True,
    }
    assert "lock_in" not in d
