"""Derivação da expectativa de migração (Onda 8 / E3).

A classe 6R é calculada no BACKEND a partir da metadata curada — a IA recebe
pronta e só narra. Sem dado curado, devolve None (honesto) em vez de palpite.
"""
from __future__ import annotations

import pytest

from app.domain.services.migration_expectation import (
    derive_expectation,
    learn_class_defaults,
)


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


# ── aprendizado por classe (a dificuldade é do TIPO, não do produto) ──────


def test_aprende_padrao_do_tipo_a_partir_dos_curados() -> None:
    """Em vez de enumerar 234 service_types à mão, deriva dos exemplos."""
    defaults = learn_class_defaults([
        ("object_storage", {"requires_refactoring": False, "vendor_lock_in_level": "low"}),
        ("object_storage", {"requires_refactoring": False, "vendor_lock_in_level": "low"}),
        ("managed_relational_database", {"requires_refactoring": True,
                                         "vendor_lock_in_level": "medium"}),
    ])
    assert defaults["object_storage"].classe == "rehost"
    assert defaults["managed_relational_database"].classe == "refactor"
    # Herdado != curado: a origem fica declarada.
    assert defaults["object_storage"].fonte == "classe"
    assert defaults["object_storage"].as_dict()["fonte"] == "classe"


def test_agregacao_e_conservadora() -> None:
    """Se QUALQUER exemplo do tipo exige reescrita, o tipo vira refactor. Errar para
    o lado difícil não gera promessa de economia fácil."""
    defaults = learn_class_defaults([
        ("data_warehouse", {"requires_refactoring": False, "vendor_lock_in_level": "low"}),
        ("data_warehouse", {"requires_refactoring": True, "vendor_lock_in_level": "low"}),
    ])
    assert defaults["data_warehouse"].classe == "refactor"
    assert defaults["data_warehouse"].lock_in == "low"


def test_classe_mista_nao_afirma_lock_in() -> None:
    """Lote v4, V4.4: RDS e Cloud SQL médios, Azure SQL alto. O maior dava "alto" a
    qualquer banco relacional sem curadoria, inclusive PostgreSQL. A classe segue
    conservadora (sai do maior), mas a nota fica para a curadoria."""
    relacional = {"requires_refactoring": False}
    defaults = learn_class_defaults([
        ("managed_relational_database", {**relacional, "vendor_lock_in_level": "medium"}),
        ("managed_relational_database", {**relacional, "vendor_lock_in_level": "high"}),
        ("managed_relational_database", {**relacional, "vendor_lock_in_level": "medium"}),
    ])
    misto = defaults["managed_relational_database"]
    assert misto.classe == "replatform"
    assert misto.lock_in is None
    assert misto.lock_in_faixa == ("medium", "high")
    assert misto.as_dict()["lock_in_faixa"] == ["medium", "high"]
    assert "lock_in" not in misto.as_dict()


def test_tipo_sem_exemplo_curado_nao_e_inventado() -> None:
    """Sem evidência o sistema cala — não chuta a dificuldade."""
    defaults = learn_class_defaults([
        ("object_storage", {"requires_refactoring": False, "vendor_lock_in_level": "low"}),
    ])
    assert "web_application_firewall" not in defaults


def test_entradas_invalidas_sao_ignoradas() -> None:
    defaults = learn_class_defaults([
        (None, {"requires_refactoring": True}),
        ("", {"requires_refactoring": True}),
        ("x", None),
        ("y", {"pricing_model": "irrelevante"}),
    ])
    assert defaults == {}


def test_curada_prevalece_sobre_classe() -> None:
    """`as_dict` só marca `fonte` quando NÃO é curada — o caso normal fica limpo."""
    propria = derive_expectation({"requires_refactoring": True})
    assert propria is not None
    assert "fonte" not in propria.as_dict()
