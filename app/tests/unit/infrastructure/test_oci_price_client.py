"""Geração de configs flex OCI a partir das taxas reais (Wave 3b).

Testa a lógica (parse das taxas + geração de configs + conversão OCPU→vCPU) sem
rede, injetando itens canônicos no lugar do fetch.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.infrastructure.pricing import oci_price_client


def _item(display: str, metric: str, value: float, *, franquia: float | None = None) -> dict:
    # A API traz a franquia grátis como primeira faixa (a A1 tem 3.000 OCPU-h por mês)
    faixas = [{"value": value}]
    if franquia is not None:
        faixas = [
            {"value": 0, "rangeMin": 0, "rangeMax": franquia},
            {"value": value, "rangeMin": franquia, "rangeMax": 999999999},
        ]
    return {
        "displayName": display,
        "metricName": metric,
        "currencyCodeLocalizations": [{"prices": faixas}],
    }


_SAMPLE = [
    _item("Compute - Standard - E5 - OCPU", "OCPU Per Hour", 0.03),
    _item("Compute - Standard - E5 - Memory", "Gigabytes Per Hour", 0.002),
    _item("OCI - Compute - Standard - A4 - OCPU", "OCPU Per Hour", 0.0138),
    _item("OCI - Compute - Standard - A4 - Memory", "Gigabytes Per Hour", 0.0027),
    # Ruído que deve ser ignorado:
    _item("Compute - Bare Metal Standard - X7", "OCPU Per Hour", 0.0638),
    _item("Compute - Standard - E5 Ax - OCPU", "OCPU Per Hour", 0.99),
    _item("Compute - Standard - A1 - OCPU", "OCPU Per Hour", 0.01, franquia=3000),
    _item("Compute - Standard - A1 - Memory", "Gigabyte Per Hour", 0.0015, franquia=18000),
    # Família sem preço pago nenhum fica de fora
    _item("Compute - Standard - A9 - OCPU", "OCPU Per Hour", 0.0),
    _item("Compute - Standard - A9 - Memory", "Gigabytes Per Hour", 0.0),
]


@pytest.fixture
def _patched(monkeypatch):
    monkeypatch.setattr(oci_price_client, "_fetch", lambda timeout: _SAMPLE)


def test_generates_only_flex_families(_patched) -> None:
    pts = oci_price_client.fetch_oci_compute_prices()
    fams = {p.sku_key.split(".")[2] for p in pts}
    # Bare Metal, Ax e família sem preço pago fora; a A1 entra pela faixa paga
    assert fams == {"E5", "A4", "A1"}


def test_vcpu_por_ocpu_pela_regra_oficial(_patched) -> None:
    """x86 e AmpereOne (A2/A4): 2 vCPU por OCPU; A1: 1 (docs.oracle.com, Compute Shapes)."""
    pts = {p.sku_key: p for p in oci_price_client.fetch_oci_compute_prices()}
    assert pts["VM.Standard.E5.Flex.8c64g"].vcpus == Decimal("16")
    assert pts["VM.Standard.A4.Flex.8c64g"].vcpus == Decimal("16")
    assert pts["VM.Standard.A1.Flex.8c64g"].vcpus == Decimal("8")
    assert pts["VM.Standard.E5.Flex.8c64g"].ram_gib == Decimal("64")


def test_a1_pela_faixa_paga(_patched) -> None:
    pts = {p.sku_key: p for p in oci_price_client.fetch_oci_compute_prices()}
    # 2 OCPU x 0,01 + 16 GB x 0,0015 = 0,044
    assert pts["VM.Standard.A1.Flex.2c16g"].price == Decimal("0.0440")


def test_grade_de_ram_por_vcpu_dentro_do_limite(_patched) -> None:
    """De 1 a 16 GB por vCPU, sem passar de 64 GB por OCPU (limite do Flex)."""
    pts = oci_price_client.fetch_oci_compute_prices()
    for fam, por_ocpu in (("E5", 2), ("A1", 1), ("A4", 2)):
        razoes = {
            p.ram_gib / p.vcpus for p in pts if p.sku_key.startswith(f"VM.Standard.{fam}.")
        }
        assert razoes == {Decimal(r) for r in (1, 2, 4, 8, 16)}
        assert all(
            p.ram_gib / (p.vcpus / por_ocpu) <= 64
            for p in pts
            if p.sku_key.startswith(f"VM.Standard.{fam}.")
        )


def test_price_is_ocpu_plus_memory(_patched) -> None:
    pts = {p.sku_key: p for p in oci_price_client.fetch_oci_compute_prices()}
    e5 = pts["VM.Standard.E5.Flex.8c128g"]  # 8 OCPU, 128 GB
    # 8*0.03 + 128*0.002 = 0.24 + 0.256 = 0.496
    assert e5.price == Decimal("0.496")
    assert e5.confidence == "official"
    assert e5.source == "oci_cetools_api"
