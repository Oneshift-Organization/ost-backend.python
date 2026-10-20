"""Perfil de família: arquitetura e burstable (PLANO_PRECO_REGIONAL R5)."""

from __future__ import annotations

from decimal import Decimal

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
    p = perfil(provedor, nome)
    assert p is not None
    assert (p.arquitetura, p.burstable) == (arquitetura, burstable)


@pytest.mark.parametrize(
    ("provedor", "nome", "base"),
    [
        ("aws", "t4g.xlarge", "0.40"),
        ("aws", "t3.micro", "0.10"),
        ("aws", "T3A", "0.40"),  # família sem tamanho: a maior base usual
        ("gcp", "e2-medium", "0.50"),
        ("aws", "m7g.large", None),
        ("azure", "Standard_B2s", "0.20"),
        ("azure", "Standard_B4ms", "0.225"),
        ("azure", "Standard_B16ms", "0.17"),
        ("azure", "BMS", "0.30"),  # família sem tamanho: a maior base dela
        ("azure", "BLS", "0.05"),
        ("azure", "Standard_B2ts_v2", "0.20"),
        ("azure", "Standard_B4ls_v2", "0.30"),
        ("azure", "Standard_B2pls_v2", "0.30"),  # Ampere, mesma tabela
        ("azure", "BSV2", "0.40"),
        ("azure", "BLSV2", "0.30"),  # v2: não é a BLS da v1
        ("azure", "Standard_B2s_v3", None),  # versão sem tabela: não reduz
        ("azure", "Standard_D4s_v5", None),
    ],
)
def test_base_de_cpu_do_burstable(provedor: str, nome: str, base: str | None) -> None:
    p = perfil(provedor, nome)
    assert p is not None
    assert p.base_cpu == (Decimal(base) if base else None)


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
