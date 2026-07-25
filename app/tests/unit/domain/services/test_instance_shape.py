"""Expansão instance-hours → vCPU/RAM pelo esquema de nomenclatura (Wave 2).

A modelagem é estrutural (tamanho→vCPU, letra de classe→GiB/vCPU), não uma tabela
por instância. Os testes conferem que casos reais batem com as specs de fábrica e
que descritores por-vCPU (GCP) e de compromisso não são tocados.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.instance_shape import InstanceShape, parse_instance_shape


@pytest.mark.parametrize(
    ("text", "vcpus", "ram_gib"),
    [
        # (vCPU, RAM) reais de fábrica — a regra estrutural precisa reproduzi-los.
        ("BoxUsage:m7g.2xlarge", "8", "32"),   # geral: 4 GiB/vCPU
        ("SpotUsage:c7g.2xlarge", "8", "16"),  # compute: 2 GiB/vCPU
        ("BoxUsage:r8g.4xlarge", "16", "128"),  # memória: 8 GiB/vCPU
        ("BoxUsage:r7g.xlarge", "4", "32"),
        ("BoxUsage:m7g.4xlarge", "16", "64"),
        ("BoxUsage:is4gen.xlarge", "4", "24"),  # storage: 6 GiB/vCPU
        ("BoxUsage:c4.large", "2", "4"),
        ("BoxUsage:m7g.medium", "2", "8"),      # sub-large achatado em 2
    ],
)
def test_shape_matches_factory_specs(text: str, vcpus: str, ram_gib: str) -> None:
    shape = parse_instance_shape(text)
    assert shape is not None
    assert shape.vcpus == Decimal(vcpus)
    assert shape.ram_gib == Decimal(ram_gib)
    assert shape.confidence == "estimado"


def test_nxlarge_scales_by_four() -> None:
    """Regra geral: Nxlarge = N*4 vCPU — vale para tamanhos nunca tabelados."""
    assert parse_instance_shape("BoxUsage:m9z.48xlarge") == InstanceShape(
        vcpus=Decimal("192"), ram_gib=Decimal("768")
    )


@pytest.mark.parametrize(
    "text",
    [
        "N2 Instance Core running in Americas",  # GCP: já por-vCPU, sem shape
        "ComputeSP:3yrNoUpfront",                # compromisso, sem instância
        "EBS:VolumeUsage.gp3",                   # storage, sem tamanho de instância
        "",
        None,
    ],
)
def test_no_shape_returns_none(text: str | None) -> None:
    assert parse_instance_shape(text) is None
