"""Prefixo de região da AWS não é família de máquina (achado na E6 do PLANO_CUR_BIGQUERY)."""

from __future__ import annotations

import pytest

from app.domain.services.sku_matcher import extract_family


@pytest.mark.parametrize(
    "descritor",
    [
        "USE1-CW:DatabaseInsights-vCPU-Hours",
        "USE1-VendedLog-Bytes-WAFLogs",
        "SAE1-DataTransfer-Out-Bytes",
        "APN1-Requests-Tier1",
    ],
)
def test_prefixo_de_regiao_nao_vira_familia(descritor: str) -> None:
    assert extract_family(descritor) is None


@pytest.mark.parametrize(
    ("descritor", "familia"),
    [
        # Família de verdade continua saindo — com ou sem região na frente.
        ("SAE1-BoxUsage:t3a.medium", "T3A"),
        ("USE2-SpotUsage:c7g.2xlarge", "C7G"),
        ("BoxUsage:m7g.2xlarge", "M7G"),
        ("InstanceUsage:db.r8g.4xl", "R8G"),
        ("E2 Instance Core running in Americas", "E2"),
        ("c3d-standard-8", "C3D"),
    ],
)
def test_familia_real_continua_extraida(descritor: str, familia: str) -> None:
    assert extract_family(descritor) == familia
