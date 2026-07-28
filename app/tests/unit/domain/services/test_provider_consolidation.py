"""Consolidação por provedor + recomendação determinística (Wave 5)."""
from __future__ import annotations

from decimal import Decimal

from app.domain.services.provider_consolidation import (
    ProviderTally,
    consolidate,
    weakest_confidence,
)


def test_recommends_biggest_saving_with_carryover() -> None:
    # Atual 1000. OCI reprecifica os 800 cobertos por 400 (economia 400); o resto
    # (200) fica ao custo atual → total 600.
    tallies = {
        "oci": ProviderTally(Decimal("400"), Decimal("800"), "official"),
        "gcp": ProviderTally(Decimal("900"), Decimal("800"), "low"),  # mais caro
    }
    r = consolidate(source_provider="aws", current_total=Decimal("1000"), tallies=tallies)
    assert r["recomendado"] == "oci"
    oci = next(p for p in r["por_provedor"] if p["provedor"] == "oci")
    assert oci["custo_estimado_total"] == 600.0
    assert oci["economia_mensal"] == 400.0
    assert oci["economia_anual"] == 4800.0
    assert oci["cobertura"] == 0.8


def test_low_coverage_not_recommended() -> None:
    # Economia grande, mas cobre só 20% → não elegível a recomendação.
    tallies = {"oci": ProviderTally(Decimal("10"), Decimal("200"), "official")}
    r = consolidate(source_provider="aws", current_total=Decimal("1000"), tallies=tallies)
    assert r["recomendado"] is None
    assert "manter" in r["justificativa"].lower()


def test_no_saving_recommends_stay() -> None:
    tallies = {"gcp": ProviderTally(Decimal("900"), Decimal("800"), "reference")}
    r = consolidate(source_provider="aws", current_total=Decimal("1000"), tallies=tallies)
    assert r["recomendado"] is None


def test_providers_sorted_by_saving() -> None:
    tallies = {
        "gcp": ProviderTally(Decimal("700"), Decimal("800"), "low"),   # economia 100
        "oci": ProviderTally(Decimal("500"), Decimal("800"), "official"),  # economia 300
    }
    r = consolidate(source_provider="aws", current_total=Decimal("1000"), tallies=tallies)
    assert [p["provedor"] for p in r["por_provedor"]] == ["oci", "gcp"]


def test_weakest_confidence() -> None:
    assert weakest_confidence(["official", "reference", "low"]) == "low"
    assert weakest_confidence(["official", "official"]) == "official"
    assert weakest_confidence([]) == "missing"
