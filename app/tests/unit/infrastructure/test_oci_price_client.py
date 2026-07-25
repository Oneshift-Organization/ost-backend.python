"""Geração de configs flex OCI a partir das taxas reais (Wave 3b).

Testa a lógica (parse das taxas + geração de configs + conversão OCPU→vCPU) sem
rede, injetando itens canônicos no lugar do fetch.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.infrastructure.pricing import oci_price_client


def _item(display: str, metric: str, value: float) -> dict:
    return {
        "displayName": display,
        "metricName": metric,
        "currencyCodeLocalizations": [{"prices": [{"value": value}]}],
    }


_SAMPLE = [
    _item("Compute - Standard - E5 - OCPU", "OCPU Per Hour", 0.03),
    _item("Compute - Standard - E5 - Memory", "Gigabytes Per Hour", 0.002),
    _item("OCI - Compute - Standard - A4 - OCPU", "OCPU Per Hour", 0.0138),
    _item("OCI - Compute - Standard - A4 - Memory", "Gigabytes Per Hour", 0.0027),
    # Ruído que deve ser ignorado:
    _item("Compute - Bare Metal Standard - X7", "OCPU Per Hour", 0.0638),
    _item("Compute - Standard - E5 Ax - OCPU", "OCPU Per Hour", 0.99),
    _item("Compute - Standard - A1 - OCPU", "OCPU Per Hour", 0.0),  # zero -> fora
    _item("Compute - Standard - A1 - Memory", "Gigabytes Per Hour", 0.0),
]


@pytest.fixture
def _patched(monkeypatch):
    monkeypatch.setattr(oci_price_client, "_fetch", lambda timeout: _SAMPLE)


def test_generates_only_flex_families(_patched) -> None:
    pts = oci_price_client.fetch_oci_compute_prices()
    fams = {p.sku_key.split(".")[2] for p in pts}
    assert fams == {"E5", "A4"}  # Bare Metal, Ax e A1($0) fora


def test_x86_ocpu_is_two_vcpu_arm_is_one(_patched) -> None:
    pts = {p.sku_key: p for p in oci_price_client.fetch_oci_compute_prices()}
    e5 = pts["VM.Standard.E5.Flex.8c64g"]   # 8 OCPU x86
    a4 = pts["VM.Standard.A4.Flex.8c64g"]   # 8 OCPU ARM
    assert e5.vcpus == Decimal("16")        # x86: OCPU*2
    assert a4.vcpus == Decimal("8")         # ARM: OCPU*1
    assert e5.ram_gib == Decimal("64")


def test_price_is_ocpu_plus_memory(_patched) -> None:
    pts = {p.sku_key: p for p in oci_price_client.fetch_oci_compute_prices()}
    e5 = pts["VM.Standard.E5.Flex.8c128g"]  # 8 OCPU, 128 GB
    # 8*0.03 + 128*0.002 = 0.24 + 0.256 = 0.496
    assert e5.price == Decimal("0.496")
    assert e5.confidence == "official"
    assert e5.source == "oci_cetools_api"
