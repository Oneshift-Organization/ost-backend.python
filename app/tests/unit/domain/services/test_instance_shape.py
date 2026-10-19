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
        # medium fora da família T é 1 vCPU (m7g.medium: 1 vCPU, 4 GiB)
        ("BoxUsage:m7g.medium", "1", "4"),
        ("BoxUsage:c7g.medium", "1", "2"),
        ("BoxUsage:r7g.medium", "1", "8"),
        # Família T (burstable): tabela de fábrica, a RAM não segue a letra
        ("BoxUsage:t3.nano", "2", "0.5"),
        ("BoxUsage:t3.micro", "2", "1"),
        ("BoxUsage:t3a.small", "2", "2"),
        ("BoxUsage:t4g.medium", "2", "4"),
        ("BoxUsage:t4g.large", "2", "8"),
        ("BoxUsage:t4g.xlarge", "4", "16"),
        ("BoxUsage:t3.2xlarge", "8", "32"),
        ("BoxUsage:t2.micro", "1", "1"),
        ("BoxUsage:t2.small", "1", "2"),
        ("BoxUsage:t2.medium", "2", "4"),
        ("InstanceUsage:db.t3.micro", "2", "1"),
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


@pytest.mark.parametrize(
    ("tipo", "vcpus", "ram_gib"),
    [
        # Geração anterior: a especificação não segue a regra estrutural
        ("t1.micro", "1", "0.613"),
        ("BoxUsage:m1.small", "1", "1.7"),
        ("db.m1.small", "1", "1.7"),
        ("c1.medium", "2", "1.7"),
        ("m2.xlarge", "2", "17.1"),
        ("m3.medium", "1", "3.75"),
        ("m3.2xlarge", "8", "30"),
        # A geração atual segue a regra, sem a tabela
        ("m5.large", "2", "8"),
        ("m6i.2xlarge", "8", "32"),
    ],
)
def test_geracao_anterior_pela_tabela_oficial(tipo: str, vcpus: str, ram_gib: str) -> None:
    forma = parse_instance_shape(tipo)
    assert forma is not None
    assert (forma.vcpus, forma.ram_gib) == (Decimal(vcpus), Decimal(ram_gib))
