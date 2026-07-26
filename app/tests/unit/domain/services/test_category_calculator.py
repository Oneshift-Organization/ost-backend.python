"""Calculadora por categoria — reprecificação por razão de taxa (Wave 4)."""
from __future__ import annotations

from decimal import Decimal

from app.domain.services.category_calculator import (
    CategoryRate,
    estimate_by_cost_ratio,
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
    out = {e.provider: e for e in estimate_by_cost_ratio(
        source_provider="aws", source_cost=Decimal("1000"), rates=rates
    )}
    assert out["gcp"].estimated_monthly_cost == Decimal("2000")
    assert out["gcp"].cost_delta == Decimal("1000")
    assert out["oci"].estimated_monthly_cost == Decimal("250")  # 0.02/0.08 * 1000
    assert "aws" not in out  # origem não vira alvo dela mesma


def test_no_source_rate_yields_nothing() -> None:
    """Sem taxa de origem não dá para inferir a quantidade → vazio (honesto)."""
    rates = {"gcp": _rate("gcp", "0.16")}
    assert estimate_by_cost_ratio(
        source_provider="aws", source_cost=Decimal("1000"), rates=rates
    ) == []


def test_confidence_is_weakest_link() -> None:
    rates = {
        "aws": _rate("aws", "0.08", conf="official"),
        "gcp": _rate("gcp", "0.16", conf="reference"),
    }
    out = estimate_by_cost_ratio(
        source_provider="aws", source_cost=Decimal("1000"), rates=rates
    )
    assert out[0].pricing_confidence == "reference"  # min(official, reference)


def test_zero_rate_targets_skipped() -> None:
    rates = {"aws": _rate("aws", "0.08"), "gcp": _rate("gcp", "0")}
    out = estimate_by_cost_ratio(
        source_provider="aws", source_cost=Decimal("1000"), rates=rates
    )
    assert out == []
