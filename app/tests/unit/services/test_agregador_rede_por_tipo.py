"""Balde de rede separado por tipo no agregador (PLANO_PRECO_NAO_COMPUTE R1).

Antes, balanceador, NAT, IP e VPN iam inteiros para `network_egress` e eram
cotados pela taxa de egress. Agora cada linha vai para o tipo dela, com a
quantidade medida na unidade da categoria, e a soma dos baldes não muda.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.services.billing_aggregator import BillingAggregator


def _rede(
    provider: str,
    servico: str,
    uso: str,
    custo: str,
    *,
    medida: tuple[str, str | None] | None = None,
    regiao: str | None = None,
) -> BillingEnriched:
    quantidade, unidade = medida or (None, None)
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider=provider,
        billing_period="2026-03",
        service_name=servico,
        cost=Decimal(custo),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type=uso,
        usage_amount=Decimal(quantidade) if quantidade is not None else None,
        usage_unit=unidade,
        project_id="p1",
        component_type="network_egress",
        mapping_confidence="high",
        region=regiao,
    )


@pytest.mark.asyncio
async def test_aws_separa_nat_balanceador_ip_e_egress() -> None:
    linhas = [
        _rede("aws", "AmazonEC2", "SAE1-NatGateway-Hours", "67.89", medida=("730", None)),
        _rede("aws", "AmazonEC2", "NatGateway-Bytes", "45", medida=("1000", None)),
        _rede("aws", "AWSELB", "LoadBalancerUsage", "16.43", medida=("730", None)),
        _rede("aws", "AWSELB", "LCUUsage", "8", medida=("1000", "Units")),
        _rede("aws", "AmazonVPC", "USE1-PublicIPv4:InUseAddress", "3.65", medida=("730", None)),
        _rede("aws", "AmazonEC2", "DataTransfer-Out-Bytes", "90", medida=("1000", None)),
        _rede("aws", "AmazonVPC", "USE1-VpcEndpoint-Hours", "7.30", medida=("730", None)),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "aws")).auxiliary

    assert aux.nat_gateway == Decimal("67.89")
    assert aux.nat_gateway_data == Decimal("45")
    assert aux.load_balancer == Decimal("16.43")
    assert aux.load_balancer_data == Decimal("8")
    assert aux.public_ip == Decimal("3.65")
    assert aux.network_egress == Decimal("90")
    assert aux.network_other == Decimal("7.30")
    # A quantidade vai para a região da linha (prefixo do UsageType)
    assert aux.quantidade_por_regiao["nat_gateway"] == {"sa-east-1": Decimal("730")}
    assert aux.medido_por_regiao["nat_gateway"] == {"sa-east-1": Decimal("67.89")}
    # Egress segue pela razão de taxas e a rede sem cotação não mede quantidade
    assert "network_egress" not in aux.quantidade_por_regiao
    assert "network_other" not in aux.quantidade_por_regiao


@pytest.mark.asyncio
async def test_a_soma_dos_baldes_de_rede_e_o_custo_das_linhas() -> None:
    linhas = [
        _rede(
            "gcp",
            "Networking",
            "Networking Cloud VPN Tunnel Uscentral1",
            "36.50",
            medida=("730", "hour"),
            regiao="us-central1",
        ),
        _rede(
            "gcp",
            "Networking",
            "Cloud Load Balancer Forwarding Rule Minimum Global",
            "18.25",
            medida=("730", "hour"),
        ),
        _rede("gcp", "Compute Engine", "Static Ip Charge", "7.30", medida=("730", "hour")),
        _rede(
            "gcp",
            "Networking",
            "Network Internet Data Transfer Out from Americas to Americas",
            "12",
            medida=("100", "gibibyte"),
        ),
        _rede("gcp", "Cloud DNS", "DNS Query (port 53)", "0.40", medida=("1000000", "count")),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "gcp")).auxiliary
    rede = (
        aux.network_egress
        + aux.inter_zone_transfer
        + aux.load_balancer
        + aux.load_balancer_data
        + aux.nat_gateway
        + aux.nat_gateway_data
        + aux.public_ip
        + aux.vpn
        + aux.network_other
    )
    assert rede == Decimal("74.45")
    # Túnel vale meia conexão
    assert aux.quantidade_por_regiao["vpn"] == {"us-central1": Decimal("365.0")}


@pytest.mark.asyncio
async def test_linha_sem_custo_nao_da_quantidade() -> None:
    # Faixa gratuita (primeiro balanceador da OCI): a origem não paga, e o destino
    # é cotado sem faixa gratuita nas outras categorias também
    linhas = [
        _rede(
            "oci", "LOAD_BALANCER", "Load Balancer - Flexible Base", "0", medida=("744", "hours")
        ),
        _rede(
            "oci", "LOAD_BALANCER", "Load Balancer - Flexible Base", "8.25", medida=("730", "hours")
        ),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "oci")).auxiliary
    assert aux.load_balancer == Decimal("8.25")
    assert sum(aux.quantidade_por_regiao["load_balancer"].values()) == Decimal("730")
