"""Calculadora por categoria — reprecificação por razão de taxa (Wave 4)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.services.category_calculator import (
    CategoryRate,
    estimate_by_cost_ratio,
    estimate_by_quantity,
)


def _rate(provider: str, rate: str, conf: str = "reference") -> CategoryRate:
    return CategoryRate(provider=provider, rate=Decimal(rate), unit="gb_month", confidence=conf)


def test_reprices_by_ratio() -> None:
    # AWS $1000 a $0.08/GB → 12500 GB. GCP a $0.16 → $2000 (razão 2x).
    rates = {
        "aws": _rate("aws", "0.08"),
        "gcp": _rate("gcp", "0.16"),
        "oci": _rate("oci", "0.02"),
    }
    out = {
        e.provider: e
        for e in estimate_by_cost_ratio(
            source_provider="aws", source_cost=Decimal("1000"), rates=rates
        )
    }
    assert out["gcp"].estimated_monthly_cost == Decimal("2000")
    assert out["gcp"].cost_delta == Decimal("1000")
    assert out["oci"].estimated_monthly_cost == Decimal("250")  # 0.02/0.08 * 1000
    assert "aws" not in out  # origem não vira alvo dela mesma


def test_no_source_rate_yields_nothing() -> None:
    """Sem taxa de origem não dá para inferir a quantidade → vazio (honesto)."""
    rates = {"gcp": _rate("gcp", "0.16")}
    assert (
        estimate_by_cost_ratio(source_provider="aws", source_cost=Decimal("1000"), rates=rates)
        == []
    )


def test_confidence_is_weakest_link() -> None:
    rates = {
        "aws": _rate("aws", "0.08", conf="official"),
        "gcp": _rate("gcp", "0.16", conf="reference"),
    }
    out = estimate_by_cost_ratio(source_provider="aws", source_cost=Decimal("1000"), rates=rates)
    assert out[0].pricing_confidence == "reference"  # min(official, reference)


def test_zero_rate_target_is_free() -> None:
    # Taxa zero é serviço grátis no destino (NAT e VPN da OCI); taxa ausente é que
    # deixa o destino sem cotação. Antes, zero era descartado como se faltasse.
    rates = {"aws": _rate("aws", "0.08"), "gcp": _rate("gcp", "0")}
    out = estimate_by_cost_ratio(source_provider="aws", source_cost=Decimal("1000"), rates=rates)
    assert [(e.provider, e.estimated_monthly_cost, e.cost_delta) for e in out] == [
        ("gcp", Decimal("0"), Decimal("-1000"))
    ]


def test_zero_source_rate_yields_nothing() -> None:
    rates = {"aws": _rate("aws", "0"), "gcp": _rate("gcp", "0.16")}
    assert (
        estimate_by_cost_ratio(source_provider="aws", source_cost=Decimal("1000"), rates=rates)
        == []
    )


def _hora(provider: str, rate: str, conf: str = "official") -> CategoryRate:
    return CategoryRate(provider=provider, rate=Decimal(rate), unit="hour", confidence=conf)


def test_quantity_times_target_rate() -> None:
    # NAT de São Paulo: 730 h a US$ 0,093 na origem; o destino cobra a taxa dele
    rates = {
        "azure": _hora("azure", "0.045"),
        "gcp": _hora("gcp", "0.044", conf="reference"),
        "oci": _hora("oci", "0", conf="reference"),
    }
    out = estimate_by_quantity(
        source_provider="aws",
        source_cost=Decimal("67.89"),
        quantity=Decimal("730"),
        measured_cost=Decimal("67.89"),
        effective_price=Decimal("0.093"),
        rates=rates,
        unit="hour",
    )
    custos = {e.provider: e.estimated_monthly_cost for e in out}
    assert custos == {"azure": Decimal("32.850"), "gcp": Decimal("32.120"), "oci": Decimal("0")}
    assert {e.provider: e.pricing_confidence for e in out}["gcp"] == "reference"


def test_cost_without_quantity_uses_the_effective_price() -> None:
    # 100 de 130 têm quantidade (1.000 h a 0,10); os 30 restantes valem 300 h
    out = estimate_by_quantity(
        source_provider="aws",
        source_cost=Decimal("130"),
        quantity=Decimal("1000"),
        measured_cost=Decimal("100"),
        effective_price=Decimal("0.10"),
        rates={"gcp": _hora("gcp", "0.05")},
        unit="hour",
    )
    assert out[0].estimated_monthly_cost == Decimal("65.00")


def test_target_in_other_unit_or_without_rate_is_left_out() -> None:
    gb = CategoryRate(provider="gcp", rate=Decimal("0.01"), unit="gb")
    out = estimate_by_quantity(
        source_provider="aws",
        source_cost=Decimal("10"),
        quantity=Decimal("100"),
        measured_cost=Decimal("10"),
        effective_price=Decimal("0.10"),
        rates={"gcp": gb, "aws": _hora("aws", "0.10")},
        unit="hour",
    )
    assert out == []


def test_no_effective_price_yields_nothing() -> None:
    assert (
        estimate_by_quantity(
            source_provider="aws",
            source_cost=Decimal("10"),
            quantity=Decimal("0"),
            measured_cost=Decimal("0"),
            effective_price=Decimal("0"),
            rates={"gcp": _hora("gcp", "0.05")},
            unit="hour",
        )
        == []
    )
