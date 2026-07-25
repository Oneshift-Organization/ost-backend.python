"""Tamanho de VM Azure → (vCPU, RAM) pelo esquema de nomenclatura (Wave 3)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.azure_shape import parse_azure_shape


@pytest.mark.parametrize(
    ("arm", "vcpus", "ram_gib"),
    [
        ("Standard_D4s_v5", "4", "16"),    # geral: 4 GiB/vCPU
        ("Standard_E8s_v5", "8", "64"),    # memória: 8 GiB/vCPU
        ("Standard_F2s_v2", "2", "4"),     # compute: 2 GiB/vCPU
        ("Standard_B2ms", "2", "8"),
        ("Standard_D96as_v5", "96", "384"),  # instância grande — sobe o teto
        ("Standard_E8-4as_v5", "4", "32"),   # constrained: vCPU efetivo = 4
    ],
)
def test_azure_shape_matches_specs(arm: str, vcpus: str, ram_gib: str) -> None:
    shape = parse_azure_shape(arm)
    assert shape is not None
    assert shape.vcpus == Decimal(vcpus)
    assert shape.ram_gib == Decimal(ram_gib)


@pytest.mark.parametrize("arm", ["", None, "AmazonEC2", "n2-standard-4"])
def test_non_azure_returns_none(arm: str | None) -> None:
    assert parse_azure_shape(arm) is None


@pytest.mark.parametrize(
    "arm",
    [
        "Standard_D11_v2",  # série índice legada: "11" são 2 vCPU reais
        "Standard_D4_v2",   # Dv2 não-linear: "4" são 8 vCPU reais
        "Standard_D2_v1",
        "Standard_G4",      # G sem geração linear
    ],
)
def test_index_families_below_v3_rejected(arm: str) -> None:
    """Não inventa specs onde o número é índice, não contagem de vCPU."""
    assert parse_azure_shape(arm) is None


def test_f_series_v2_is_linear_and_kept() -> None:
    """F sempre foi linear (número = vCPU), mesmo em v2 — deve ser aceita."""
    shape = parse_azure_shape("Standard_F16s_v2")
    assert shape is not None
    assert shape.vcpus == Decimal("16")
