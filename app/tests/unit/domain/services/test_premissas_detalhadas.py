"""Premissas de export detalhado (E7 do PLANO_CUR_BIGQUERY)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.services.premissas_detalhadas import (
    base_de_custo,
    desconto_origem_sobre_lista,
    fracao,
)


def test_base_amortizada() -> None:
    base = base_de_custo({"amortized": Decimal("100")})
    assert base["base"] == "amortizado"
    assert base["por_base"] == {"amortizado": 100.0}


def test_base_mista_quando_parte_ficou_faturada() -> None:
    # CUR sem as colunas de SP: as linhas cobertas ficam na base faturada.
    base = base_de_custo({"amortized": Decimal("90"), "billed": Decimal("10")})
    assert base["base"] == "misto"
    assert "custo zero" in base["explicacao"]


def test_sem_base_nao_ha_premissa() -> None:
    assert base_de_custo({}) is None


def test_desconto_de_origem() -> None:
    d = desconto_origem_sobre_lista(Decimal("200"), Decimal("150"))
    assert d["fracao"] == 0.25
    assert "recontratar compromisso" in d["explicacao"]


def test_sem_sob_demanda_nao_ha_desconto() -> None:
    assert desconto_origem_sobre_lista(Decimal("0"), Decimal("10")) is None


def test_fracao_sem_total() -> None:
    assert fracao(Decimal("5"), Decimal("0")) == 0.0
