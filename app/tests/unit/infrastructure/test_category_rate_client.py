"""Fetch de taxas de categoria oficiais (Wave 4b) — sem rede, via monkeypatch."""
from __future__ import annotations

from decimal import Decimal

from app.infrastructure.pricing import category_rate_client as crc


def _az(meter: str, price: float) -> dict:
    return {"meterName": meter, "unitOfMeasure": "1 GB", "retailPrice": price}


def test_azure_egress_takes_first_tier_max(monkeypatch) -> None:
    out = "Standard Data Transfer Out"
    payload = {
        "Items": [
            # Vários tiers de egress; o inicial (mais caro) é o que queremos.
            _az(out, 0.087),
            _az(out, 0.04),
            _az(out, 0.0),
            _az("Inter-Region", 0.02),  # meter diferente -> ignora
        ]
    }
    monkeypatch.setattr(crc, "_get", lambda url, timeout: payload)
    pts = crc.fetch_azure_category_rates()
    assert len(pts) == 1
    assert pts[0].service_type == "network_egress"
    assert pts[0].price == Decimal("0.087")
    assert pts[0].confidence == "official"


def _oci_item(name: str, *values: float) -> dict:
    return {
        "displayName": name,
        "currencyCodeLocalizations": [{"prices": [{"value": v} for v in values]}],
    }


def test_oci_block_storage_and_egress(monkeypatch) -> None:
    payload = {
        "items": [
            _oci_item("Storage - Block Volume - Storage", 0.0255),
            _oci_item("Storage - Block Volume - Performance Units", 0.0017),
            _oci_item("Storage - Block Volume - Free 200GB", 0.0),  # free tier ignorado
            # 10 TB grátis, depois a taxa: a primeira faixa paga é a que vale.
            _oci_item(
                "Outbound Data Transfer - Originating in North America, Europe, and UK", 0, 0.0085
            ),
        ]
    }
    monkeypatch.setattr(crc, "_get", lambda url, timeout: payload)
    pts = {p.service_type: p for p in crc.fetch_oci_category_rates()}
    # SSD = Balanced: armazenamento + 10 VPU (0,0255 + 10 x 0,0017); HDD = Lower Cost
    assert pts["storage_ssd"].price == Decimal("0.0425")
    assert pts["storage_hdd"].price == Decimal("0.0255")
    assert pts["network_egress"].price == Decimal("0.0085")
    assert all(p.confidence == "official" for p in pts.values())
    assert {p.region for p in pts.values()} == {"us-ashburn-1"}
