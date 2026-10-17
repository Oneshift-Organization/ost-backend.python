"""OCPU → vCPU da OCI pela regra oficial (PLANO_PRECISAO_OCI P1.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.oci_shape import ler_shape, ocpus_para, vcpus_por_ocpu


@pytest.mark.parametrize(
    ("familia", "vcpus"),
    [("A1", 1), ("a1", 1), ("A2", 2), ("A4", 2), ("E4", 2), ("E5", 2), ("X9", 2)],
)
def test_vcpu_por_ocpu_pela_familia(familia: str, vcpus: int) -> None:
    assert vcpus_por_ocpu(familia) == vcpus


def test_le_o_shape_sintetico() -> None:
    assert ler_shape("VM.Standard.A2.Flex.2c16g") == ("A2", 2, 16)
    assert ler_shape("VM.Standard.E4.Flex.1c4g") == ("E4", 1, 4)
    assert ler_shape("Standard_D2s_v5") is None
    assert ler_shape(None) is None


@pytest.mark.parametrize(
    ("vcpus", "familia", "ocpus"),
    [
        (4, "E4", 2),
        (3, "E4", 2),
        (1, "E4", 1),
        (4, "A1", 4),
        (4, "A2", 2),
        (Decimal("0.5"), "A2", 1),
    ],
)
def test_ocpus_que_cobrem_as_vcpus(vcpus: Decimal | int, familia: str, ocpus: int) -> None:
    assert ocpus_para(vcpus, familia) == ocpus
