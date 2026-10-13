"""Regiões: zonas, região da linha e escolha do preço (PLANO_PRECO_REGIONAL R1)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from app.domain.services.regioes import (
    REGIAO_DE_REFERENCIA,
    ZONA_PADRAO,
    ZONAS,
    Situacao,
    escolher_regiao_de_preco,
    regiao_canonica,
    regiao_da_linha,
    regiao_dominante,
    zona_da_regiao,
    zona_dominante,
)

_PROVEDORES = ("aws", "gcp", "azure", "oci")


# ── Tabela ───────────────────────────────────────────────────────────────────


def test_toda_zona_tem_canonica_nos_quatro_provedores() -> None:
    for z in ZONAS:
        assert set(z.canonica) == set(_PROVEDORES), z.id


def test_canonica_pertence_a_propria_zona_quando_o_provedor_tem_regiao_nela() -> None:
    for z in ZONAS:
        for provedor, membros in z.membros.items():
            if membros:
                assert z.canonica[provedor] in membros, (z.id, provedor)


def test_canonica_vizinha_pertence_a_outra_zona() -> None:
    """Sem região na metrópole, a canônica é vizinha e está em alguma zona."""
    for z in ZONAS:
        for provedor, membros in z.membros.items():
            if not membros:
                assert zona_da_regiao(provedor, z.canonica[provedor]) not in (None, z.id)


def test_nenhuma_regiao_em_duas_zonas() -> None:
    for provedor in _PROVEDORES:
        todas = Counter(r for z in ZONAS for r in z.membros.get(provedor, ()))
        assert [r for r, n in todas.items() if n > 1] == [], provedor


def test_zona_padrao_tem_os_padroes_antigos() -> None:
    assert regiao_canonica(ZONA_PADRAO, "aws") == "us-east-1"
    assert regiao_canonica(ZONA_PADRAO, "azure") == "eastus"
    assert regiao_canonica(ZONA_PADRAO, "oci") == "us-ashburn-1"


@pytest.mark.parametrize(
    ("provedor", "regiao", "zona"),
    [
        ("aws", "sa-east-1", "sao-paulo"),
        ("gcp", "southamerica-east1", "sao-paulo"),
        ("azure", "Brazil South", "sao-paulo"),
        ("oci", "sa-vinhedo-1", "sao-paulo"),
        ("gcp", "americas", "meio-oeste"),
        ("gcp", "us-south1", "texas"),
        ("aws", "ap-south-1", None),
        ("aws", None, None),
        ("aws", "global", None),
    ],
)
def test_zona_da_regiao(provedor: str, regiao: str | None, zona: str | None) -> None:
    assert zona_da_regiao(provedor, regiao) == zona


def test_sao_paulo_cota_sao_paulo_em_todos() -> None:
    assert {p: regiao_canonica("sao-paulo", p) for p in _PROVEDORES} == {
        "aws": "sa-east-1",
        "gcp": "southamerica-east1",
        "azure": "brazilsouth",
        "oci": "sa-saopaulo-1",
    }


# ── Região da linha ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("usage_type", "regiao"),
    [
        ("BoxUsage:m7g.2xlarge", "us-east-1"),
        ("SAE1-BoxUsage:m7g.2xlarge", "sa-east-1"),
        ("USE2-BoxUsage:c7g.xlarge", "us-east-2"),
        ("APS3-BoxUsage:t3.micro", "ap-south-1"),
        ("VPN-Usage-Hours:ipsec.1", "us-east-1"),
        ("US-DataTransfer-Out-Bytes", None),
        ("EU-Requests-Tier1", None),
        ("Global-DataTransfer-Out-Bytes", None),
        ("ZZZ9-BoxUsage:m5.large", None),
    ],
)
def test_regiao_da_linha_aws(usage_type: str, regiao: str | None) -> None:
    assert regiao_da_linha("aws", None, usage_type) == regiao


@pytest.mark.parametrize(
    ("descricao", "regiao"),
    [
        ("E2 Instance Core running in Americas", "americas"),
        ("E2 Instance Core running in Sao Paulo", "southamerica-east1"),
        ("N1 Predefined Instance Ram running in Dallas", "us-south1"),
        ("Hyperdisk Balanced Capacity in São Paulo", "southamerica-east1"),
        ("Balanced PD Capacity in Iowa", "us-central1"),
        ("Network Internet Data Transfer Out from Sao Paulo to Americas", "southamerica-east1"),
        ("N4D Instance Core running in Northern Virginia", "us-east4"),
        ("Standard Storage Sao Paulo", None),
        ("Commitment v1: E2 Cpu in Americas for 1 Year", None),
        ("Network Inter Zone Data Transfer Out", None),
    ],
)
def test_regiao_da_linha_gcp(descricao: str, regiao: str | None) -> None:
    assert regiao_da_linha("gcp", None, descricao) == regiao


def test_coluna_do_export_vem_primeiro() -> None:
    assert regiao_da_linha("aws", "eu-west-1", "SAE1-BoxUsage:m5.large") == "eu-west-1"
    assert regiao_da_linha("azure", "Brazil South", None) == "brazilsouth"


def test_coluna_global_nao_conta_como_regiao() -> None:
    assert regiao_da_linha("gcp", "global", "E2 Instance Core running in Sao Paulo") == (
        "southamerica-east1"
    )


def test_azure_e_oci_sem_coluna_nao_inventam() -> None:
    assert regiao_da_linha("azure", None, "D4s v5") is None
    assert regiao_da_linha("oci", "", "B93113") is None


def test_zona_dominante_pesa_por_custo() -> None:
    linhas = [
        ("gcp", "americas", Decimal("800")),
        ("gcp", "southamerica-east1", Decimal("150")),
        ("gcp", None, Decimal("5000")),  # sem região não vota
    ]
    assert zona_dominante(linhas) == "meio-oeste"
    assert zona_dominante([("gcp", None, Decimal("1"))]) is None


def test_regiao_dominante_devolve_a_regiao_mais_cara_da_zona() -> None:
    linhas = [
        ("gcp", "americas", Decimal("800")),
        ("gcp", "us-central1", Decimal("100")),
        ("gcp", "southamerica-east1", Decimal("850")),
    ]
    assert regiao_dominante(linhas) == ("meio-oeste", "americas")


def test_provedor_fora_da_tabela_nao_tem_canonica() -> None:
    assert regiao_canonica("virginia", "ibm") is None


def test_zona_dominante_desempata_pelo_id() -> None:
    linhas = [("aws", "us-east-1", Decimal("1")), ("aws", "sa-east-1", Decimal("1"))]
    assert zona_dominante(linhas) == zona_dominante(list(reversed(linhas)))


# ── Escolha do preço ─────────────────────────────────────────────────────────


def test_exata_quando_a_preferida_tem_preco() -> None:
    r = escolher_regiao_de_preco("aws", ["sa-east-1"], ["us-east-1", "sa-east-1"])
    assert r is not None
    assert (r.regiao, r.situacao) == ("sa-east-1", Situacao.EXATA)
    assert not r.aproximada


def test_segunda_preferida_tambem_e_exata() -> None:
    r = escolher_regiao_de_preco("gcp", ["americas", "us-central1"], ["us-central1"])
    assert r is not None
    assert (r.regiao, r.situacao) == ("us-central1", Situacao.EXATA)


def test_oci_e_preco_global() -> None:
    r = escolher_regiao_de_preco("oci", ["sa-saopaulo-1"], ["us-ashburn-1"])
    assert r is not None
    assert (r.regiao, r.situacao) == ("us-ashburn-1", Situacao.PRECO_GLOBAL)
    assert not r.aproximada


def test_egress_da_oci_nao_e_global() -> None:
    r = escolher_regiao_de_preco(
        "oci", ["sa-saopaulo-1"], ["us-ashburn-1"], service_type="network_egress"
    )
    assert r is not None
    assert (r.regiao, r.situacao) == ("us-ashburn-1", Situacao.APROXIMADA)


def test_sem_a_regiao_cai_na_zona_padrao_e_marca_aproximada() -> None:
    r = escolher_regiao_de_preco("aws", ["sa-east-1"], ["us-west-2", "us-east-1"])
    assert r is not None
    assert (r.regiao, r.situacao) == ("us-east-1", Situacao.APROXIMADA)
    assert r.aproximada


def test_taxa_de_referencia_e_o_fallback_seguinte() -> None:
    r = escolher_regiao_de_preco("gcp", ["southamerica-east1"], [REGIAO_DE_REFERENCIA])
    assert r is not None
    assert (r.regiao, r.situacao) == (REGIAO_DE_REFERENCIA, Situacao.APROXIMADA)


def test_ultimo_recurso_e_ordem_alfabetica() -> None:
    r = escolher_regiao_de_preco("azure", ["brazilsouth"], ["westus2", "centralus"])
    assert r is not None
    assert r.regiao == "centralus"


def test_sem_preco_nenhum() -> None:
    assert escolher_regiao_de_preco("aws", ["us-east-1"], []) is None
