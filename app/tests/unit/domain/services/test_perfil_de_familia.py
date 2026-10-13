"""Perfil de família: arquitetura e burstable (PLANO_PRECO_REGIONAL R5)."""

from __future__ import annotations

import pytest

from app.domain.services.perfil_de_familia import (
    Arquitetura,
    Perfil,
    aceita,
    mesma_arquitetura,
    perfil,
)

ARM = Arquitetura.ARM
X86 = Arquitetura.X86


@pytest.mark.parametrize(
    ("provedor", "nome", "arquitetura", "burstable"),
    [
        # AWS: família do pool e tipo do candidato
        ("aws", "M7G", ARM, False),
        ("aws", "m7g.2xlarge", ARM, False),
        ("aws", "c7gn.xlarge", ARM, False),
        ("aws", "IS4GEN", ARM, False),
        ("aws", "t4g.nano", ARM, True),
        ("aws", "T3A", X86, True),
        ("aws", "m7a.xlarge", X86, False),
        ("aws", "m7i-flex.large", X86, False),
        ("aws", "C4", X86, False),
        # GCP
        ("gcp", "E2", X86, False),
        ("gcp", "e2-standard-8", X86, False),
        ("gcp", "e2-medium", X86, True),
        ("gcp", "n2d-highmem-4", X86, False),
        ("gcp", "t2a-standard-4", ARM, False),
        ("gcp", "C4A", ARM, False),
        # Azure: SKU do candidato e família do parser
        ("azure", "Standard_D4ps_v5", ARM, False),
        ("azure", "Standard_D48pls_v6", ARM, False),
        ("azure", "Standard_B2pts_v2", ARM, True),
        ("azure", "Standard_B16pls_v2", ARM, True),
        ("azure", "Standard_E8s_v5", X86, False),
        ("azure", "Standard_D4as_v5", X86, False),
        ("azure", "Standard_B1ls", X86, True),
        ("azure", "ESV5", X86, False),
        ("azure", "DPSV5", ARM, False),
        ("azure", "BMS", X86, True),
        # OCI
        ("oci", "VM.Standard.A1.Flex.4c16g", ARM, False),
        ("oci", "VM.Standard.E5.Flex.8c64g", X86, False),
        ("oci", "A1", ARM, False),
        ("oci", "E4", X86, False),
    ],
)
def test_perfil(provedor: str, nome: str, arquitetura: Arquitetura, burstable: bool) -> None:
    assert perfil(provedor, nome) == Perfil(arquitetura, burstable)


@pytest.mark.parametrize(
    ("provedor", "nome"), [("aws", "general"), ("ibm", "bx2-4x16"), ("aws", "")]
)
def test_nome_que_nao_diz_nao_tem_perfil(provedor: str, nome: str) -> None:
    assert perfil(provedor, nome) is None


def test_burstable_so_substitui_burstable() -> None:
    sustentado = Perfil(X86)
    credito = Perfil(X86, burstable=True)
    assert not aceita(sustentado, credito)
    assert aceita(credito, sustentado)
    assert aceita(credito, credito)
    assert aceita(None, credito), "sem perfil da origem, não filtra"


def test_mesma_arquitetura() -> None:
    assert mesma_arquitetura(Perfil(ARM), Perfil(ARM, burstable=True))
    assert not mesma_arquitetura(Perfil(ARM), Perfil(X86))
    assert mesma_arquitetura(Perfil(ARM), None)
