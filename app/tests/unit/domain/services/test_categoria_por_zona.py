"""Categoria cotada por zona (P1): cada parte na taxa da zona dela."""

from __future__ import annotations

from decimal import Decimal

from app.domain.services.categoria_por_zona import dividir_por_zona


def test_partes_por_zona_com_a_maior_primeiro() -> None:
    partes = dividir_por_zona(
        "gcp",
        {"us-central1": Decimal("965"), "southamerica-east1": Decimal("35")},
        zona_dominante="meio-oeste",
        regiao_dominante="us-central1",
    )
    assert [p.regiao_origem for p in partes] == ["us-central1", "southamerica-east1"]
    assert [p.fracao for p in partes] == [Decimal("0.965"), Decimal("0.035")]
    assert partes[0].zona != partes[1].zona


def test_linha_sem_regiao_vai_para_a_zona_dominante_e_e_declarada() -> None:
    [parte] = dividir_por_zona(
        "aws",
        {"us-east-1": Decimal("70"), "": Decimal("30")},
        zona_dominante="virginia",
        regiao_dominante="us-east-1",
    )
    assert parte.zona == "virginia"
    assert parte.custo == Decimal("100")
    assert parte.sem_regiao == Decimal("30")


def test_tudo_sem_regiao_usa_a_regiao_dominante() -> None:
    [parte] = dividir_por_zona(
        "aws", {"": Decimal("10")}, zona_dominante="virginia", regiao_dominante="us-east-1"
    )
    assert (parte.zona, parte.regiao_origem, parte.fracao) == ("virginia", "us-east-1", Decimal(1))


def test_sem_custo_nao_ha_partes() -> None:
    assert dividir_por_zona("aws", {}, zona_dominante="virginia", regiao_dominante=None) == []
