from decimal import Decimal

import pytest

from app.domain.services.rede_por_uso import (
    BALANCEADOR,
    BALANCEADOR_DADOS,
    EGRESS,
    ENTRE_ZONAS,
    IP_PUBLICO,
    NAT,
    NAT_DADOS,
    OUTROS,
    UNIDADE,
    VPN,
    quantidade_na_unidade,
    tipo_de_rede,
)


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "categoria", "fator"),
    [
        # AWS: o UsageType diz o tipo
        ("aws", "AmazonEC2", "SAE1-NatGateway-Hours", NAT, "1"),
        ("aws", "AmazonEC2", "NatGateway-Bytes", NAT_DADOS, "1"),
        ("aws", "AWSELB", "LoadBalancerUsage", BALANCEADOR, "1"),
        ("aws", "Amazon Elastic Load Balancing", "LCUUsage", BALANCEADOR_DADOS, "1"),
        ("aws", "AWSELB", "USE1-DataProcessing-Bytes", BALANCEADOR_DADOS, "1"),
        ("aws", "AmazonVPC", "USE1-PublicIPv4:InUseAddress", IP_PUBLICO, "1"),
        ("aws", "AmazonVPC", "VPN-Usage-Hours:ipsec.1", VPN, "1"),
        ("aws", "AmazonEC2", "DataTransfer-Regional-Bytes", ENTRE_ZONAS, "1"),
        ("aws", "AWSELB", "DataTransfer-Out-Bytes", EGRESS, "1"),
        ("aws", "AmazonEC2", "USE1-SAE1-AWS-Out-Bytes", EGRESS, "1"),
        ("aws", "AmazonVPC", "USE1-VpcEndpoint-Hours", OUTROS, "1"),
        ("aws", "AmazonRoute53", "DNS-Queries", OUTROS, "1"),
        ("aws", "AmazonCloudWatch", "USE1-VendedLog-Bytes", OUTROS, "1"),
        # Lattice também processa dados, mas não é balanceador
        ("aws", "AmazonVPC", "USE1-VPCLattice-DataProcessing-Bytes", OUTROS, "1"),
        # GCP: a descrição do SKU
        (
            "gcp",
            "Networking",
            "Cloud Load Balancer Forwarding Rule Minimum Global",
            BALANCEADOR,
            "1",
        ),
        (
            "gcp",
            "Networking",
            "Cloud Load Balancer Forwarding Rule Additional for Americas (multi-region)",
            BALANCEADOR,
            "0.4",
        ),
        (
            "gcp",
            "Networking",
            "Regional Internal Passthrough Network Load Balancer Outbound Data Processing for Iowa",
            BALANCEADOR_DADOS,
            "1",
        ),
        ("gcp", "Networking", "Networking Cloud Nat Data Processing", NAT_DADOS, "1"),
        ("gcp", "Networking", "Networking Cloud Nat Gateway Uptime", NAT, "0.0318"),
        ("gcp", "Networking", "Networking Private Nat Gateway Uptime", NAT, "1"),
        ("gcp", "Networking", "Networking Cloud NAT IP Usage", IP_PUBLICO, "1"),
        ("gcp", "Compute Engine", "Static Ip Charge", IP_PUBLICO, "1"),
        ("gcp", "Compute Engine", "External IP Charge on a Standard VM", IP_PUBLICO, "1"),
        ("gcp", "Networking", "Networking Cloud VPN Tunnel Uscentral1", VPN, "0.5"),
        # O egress da VPN é egress, não conexão
        (
            "gcp",
            "Networking",
            "Network Vpn Internet Data Transfer Out from Americas to Americas",
            EGRESS,
            "1",
        ),
        ("gcp", "Compute Engine", "Network Inter Zone Data Transfer Out", ENTRE_ZONAS, "1"),
        ("gcp", "Cloud Run", "Services Min Instance CPU (Request-based billing)", OUTROS, "1"),
        ("gcp", "Cloud DNS", "DNS Query (port 53)", OUTROS, "1"),
        # Azure: categoria | subcategoria | medidor
        (
            "azure",
            "Load Balancer",
            "Load Balancer | | Standard Included LB Rules and Outbound Rules",
            BALANCEADOR,
            "1",
        ),
        (
            "azure",
            "Load Balancer",
            "Load Balancer | | Standard Overage LB Rules and Outbound Rules",
            BALANCEADOR,
            "0.4",
        ),
        (
            "azure",
            "Load Balancer",
            "Load Balancer | | Standard Data Processed",
            BALANCEADOR_DADOS,
            "1",
        ),
        (
            "azure",
            "Application Gateway",
            "Application Gateway | Application Gateway Standard v2 | Standard Fixed Cost",
            BALANCEADOR,
            "1",
        ),
        (
            "azure",
            "Application Gateway",
            "Application Gateway | Application Gateway Standard v2 | Standard Capacity Units",
            BALANCEADOR_DADOS,
            "1",
        ),
        ("azure", "NAT Gateway", "NAT Gateway | | Standard Gateway", NAT, "1"),
        ("azure", "NAT Gateway", "NAT Gateway | | Standard Data Processed", NAT_DADOS, "1"),
        (
            "azure",
            "Virtual Network",
            "Virtual Network | IP Addresses | Standard IPv4 Static Public IP",
            IP_PUBLICO,
            "1",
        ),
        ("azure", "VPN Gateway", "VPN Gateway | VPN Gateway | VpnGw1", VPN, "1"),
        ("azure", "VPN Gateway", "VPN Gateway | VPN Gateway | P2S Connection", OUTROS, "1"),
        (
            "azure",
            "Bandwidth",
            "Bandwidth | Rtn Preference: MGN | Standard Inter-Availability Zone Data Transfer Out",
            ENTRE_ZONAS,
            "1",
        ),
        (
            "azure",
            "Bandwidth",
            "Bandwidth | Rtn Preference: MGN | Standard Data Transfer Out",
            EGRESS,
            "1",
        ),
        ("azure", "Azure DNS", "Azure DNS | | Public Queries", OUTROS, "1"),
        # OCI: nome do SKU
        ("oci", "LOAD_BALANCER", "Load Balancer - Flexible Base", BALANCEADOR, "1"),
        ("oci", "LOAD_BALANCER", "Load Balancer - Flexible Bandwidth", BALANCEADOR_DADOS, "0.45"),
        ("oci", "NETWORK", "Outbound Data Transfer Zone 1", EGRESS, "1"),
        ("oci", "NETWORK", "FastConnect 1 Gbps", OUTROS, "1"),
    ],
)
def test_tipo_de_rede(provedor, servico, uso, categoria, fator):
    tipo = tipo_de_rede(provedor, servico, uso)
    assert tipo.categoria == categoria
    assert tipo.fator == Decimal(fator)


def test_sem_uso_nem_servico_vai_para_outros():
    assert tipo_de_rede("aws", None, None).categoria == OUTROS
    assert tipo_de_rede("nuvem-nova", "x", "y").categoria == OUTROS


def test_unidade_de_cada_categoria_cotada():
    assert UNIDADE == {
        BALANCEADOR: "hour",
        BALANCEADOR_DADOS: "gb",
        NAT: "hour",
        NAT_DADOS: "gb",
        IP_PUBLICO: "hour",
        VPN: "hour",
        ENTRE_ZONAS: "gb",
    }
    # Egress segue pela razão de taxas; o resto da rede não é cotado
    assert EGRESS not in UNIDADE
    assert OUTROS not in UNIDADE


@pytest.mark.parametrize(
    ("provedor", "uso", "quantidade", "unidade", "esperado"),
    [
        # A unidade da AWS vem do UsageType: qualquer rótulo vale
        ("aws", "NatGateway-Hours", "730", "Units", "730"),
        ("aws", "NatGateway-Bytes", "120", None, "120"),
        # Fator aplicado: regra adicional do GCP vale 0,4 de um balanceador
        ("gcp", "Cloud Load Balancer Forwarding Rule Additional Global", "100", "hour", "40.0"),
        ("gcp", "Networking Cloud Nat Data Processing", "10", "gibibyte", "10"),
        ("azure", "NAT Gateway | | Standard Gateway", "730", "1 Hour", "730"),
        ("azure", "NAT Gateway | | Standard Data Processed", "50", "1 GB", "50"),
        ("oci", "Load Balancer - Flexible Bandwidth", "100", "Mbps Per Hour", "45.00"),
        # Unidade de outra dimensão: não é quantidade desta categoria
        ("gcp", "Networking Cloud Nat Data Processing", "10", "hour", None),
        ("azure", "NAT Gateway | | Standard Gateway", "50", "1 GB", None),
        # Sem quantidade, ou quantidade não positiva
        ("azure", "NAT Gateway | | Standard Gateway", None, "1 Hour", None),
        ("azure", "NAT Gateway | | Standard Gateway", "0", "1 Hour", None),
    ],
)
def test_quantidade_na_unidade(provedor, uso, quantidade, unidade, esperado):
    servico = {
        "aws": "AmazonEC2",
        "gcp": "Networking",
        "azure": "NAT Gateway",
        "oci": "LOAD_BALANCER",
    }
    tipo = tipo_de_rede(provedor, servico[provedor], uso)
    valor = quantidade_na_unidade(
        provedor, tipo, Decimal(quantidade) if quantidade is not None else None, unidade
    )
    assert valor == (Decimal(esperado) if esperado is not None else None)


def test_categoria_nao_cotada_nao_tem_quantidade():
    tipo = tipo_de_rede("aws", "AmazonVPC", "USE1-VpcEndpoint-Hours")
    assert quantidade_na_unidade("aws", tipo, Decimal(730), "Hrs") is None
