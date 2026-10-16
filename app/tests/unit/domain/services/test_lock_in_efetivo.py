"""Nível de lock-in da origem pela melhor evidência (lote v4: V4.4 e V4.5)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.services.lock_in_efetivo import (
    CATALOGO,
    CLASSE,
    INVENTARIO,
    lock_in_da_origem,
    lock_in_efetivo,
)

_AURORA = {"nivel": "medium", "motivo": "Aurora: armazenamento próprio."}
_DYNAMO = {"nivel": "high", "motivo": "DynamoDB: API própria."}
_POSTGRES = {"nivel": "low", "motivo": "PostgreSQL: motor aberto."}


@pytest.mark.parametrize(
    ("catalogo", "exclusividade", "cobertura", "esperado"),
    [
        # o inventário sobe livremente
        ("low", _AURORA, "0.1", ("medium", INVENTARIO)),
        ("medium", _DYNAMO, "0.05", ("high", INVENTARIO)),
        # igual: fica o catálogo
        ("medium", _AURORA, "1", ("medium", CATALOGO)),
        # descer exige cobertura alta
        ("medium", _POSTGRES, "0.95", ("low", INVENTARIO)),
        ("medium", _POSTGRES, "0.89", ("medium", CATALOGO)),
        # sem catálogo: recurso proprietário preenche; "baixo" só com cobertura alta
        (None, _AURORA, "0.1", ("medium", INVENTARIO)),
        (None, _POSTGRES, "0.95", ("low", INVENTARIO)),
        (None, _POSTGRES, "0.5", (None, None)),
    ],
)
def test_catalogo_e_inventario(
    catalogo: str | None, exclusividade: dict, cobertura: str, esperado: tuple
) -> None:
    resultado = lock_in_efetivo(catalogo, exclusividade=exclusividade, cobertura=cobertura)
    assert (resultado.nivel, resultado.fonte) == esperado
    assert resultado.provisorio is False


def test_motivo_do_inventario_vem_da_exclusividade() -> None:
    assert lock_in_efetivo("low", exclusividade=_DYNAMO).motivo == "DynamoDB: API própria."


def test_inventario_estimado_sobe_provisorio_e_nao_desce() -> None:
    sobe = lock_in_efetivo("low", exclusividade=_AURORA, inventario_estimado=True)
    assert (sobe.nivel, sobe.fonte, sobe.provisorio) == ("medium", INVENTARIO, True)
    desce = lock_in_efetivo(
        "medium", exclusividade=_POSTGRES, cobertura="1", inventario_estimado=True
    )
    assert (desce.nivel, desce.fonte, desce.provisorio) == ("medium", CATALOGO, False)


def test_classe_so_sem_catalogo_e_sem_inventario() -> None:
    assert lock_in_efetivo("low", classe="high").fonte == CATALOGO
    assert lock_in_efetivo(None, exclusividade=_AURORA, classe="low").fonte == INVENTARIO
    hipotese = lock_in_efetivo(None, classe="medium")
    assert (hipotese.nivel, hipotese.fonte, hipotese.provisorio) == ("medium", CLASSE, True)


@pytest.mark.parametrize("valor", [None, "", "alto", "unknown", 3])
def test_valor_invalido_e_desconhecido(valor: Any) -> None:
    resultado = lock_in_efetivo(valor, exclusividade={"nivel": valor}, classe=valor)
    assert (resultado.nivel, resultado.fonte, resultado.provisorio) == (None, None, False)


@pytest.mark.parametrize("cobertura", [None, "abc", "-0.5", "1.5", True, "NaN"])
def test_cobertura_invalida_nao_prova_ausencia(cobertura: Any) -> None:
    resultado = lock_in_efetivo("medium", exclusividade=_POSTGRES, cobertura=cobertura)
    assert resultado.fonte == CATALOGO


def _workload(**extra: Any) -> dict[str, Any]:
    return {"name": "AmazonRDS", "type_source": "curado", **extra}


def test_da_origem_le_o_catalogo_do_snapshot() -> None:
    w = _workload(
        source_metadata={"vendor_lock_in_level": "medium"},
        migration={"lock_in": "medium", "classe_migracao": "refactor"},
    )
    assert lock_in_da_origem(w).fonte == CATALOGO


def test_da_origem_le_a_classe_e_o_inventario() -> None:
    w = _workload(source_metadata={}, migration={"lock_in": "high", "fonte": "classe"})
    assert lock_in_da_origem(w).fonte == CLASSE
    entrada = {"exclusividade": _AURORA, "cobertura": "0.4", "cost_estimated": False}
    assert (lock_in_da_origem(w, entrada).nivel, lock_in_da_origem(w, entrada).fonte) == (
        "medium",
        INVENTARIO,
    )


def test_migra_junto_nao_e_lock_in() -> None:
    w = _workload(source_metadata={"vendor_lock_in_level": "low"})
    entrada = {"nivel": "high", "motivo": "Banco acessado por outras aplicações", "cobertura": 1}
    assert (lock_in_da_origem(w, entrada).nivel, lock_in_da_origem(w, entrada).fonte) == (
        "low",
        CATALOGO,
    )


def test_payload_antigo_sem_source_metadata_usa_a_expectativa_curada() -> None:
    assert lock_in_da_origem(_workload(migration={"lock_in": "medium"})).fonte == CATALOGO
    da_classe = _workload(migration={"lock_in": "medium", "fonte": "classe"})
    assert lock_in_da_origem(da_classe).fonte == CLASSE


def test_tipo_inferido_nao_tem_evidencia() -> None:
    w = _workload(type_source="inferido", source_metadata={"vendor_lock_in_level": "low"})
    assert lock_in_da_origem(w, {"exclusividade": _DYNAMO}).nivel is None
