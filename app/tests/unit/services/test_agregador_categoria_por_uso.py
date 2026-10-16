"""Plano de controle, segredos, chaves, serverless e CDN no agregador (PLANO_PRECO_NAO_COMPUTE R4).

A linha vai para a categoria do uso dela, com a quantidade na unidade da categoria, e a
soma de todos os baldes continua o custo das linhas. A classificação errada da origem
(Cloud Run como rede, requisição do KMS como disco) é corrigida na agregação, sem
reenriquecer.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.workload_pool import AuxiliaryCosts
from app.services.billing_aggregator import BillingAggregator


def _linha(
    provider: str,
    servico: str,
    uso: str,
    custo: str,
    componente: str,
    *,
    medida: tuple[str, str | None] | None = None,
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
        component_type=componente,
        mapping_confidence="high",
    )


@pytest.mark.asyncio
async def test_cloud_run_sai_da_rede_e_o_trafego_dele_fica() -> None:
    linhas = [
        _linha(
            "gcp",
            "Cloud Run",
            "Services Min Instance CPU (Request-based billing)",
            "40",
            "network_egress",
            medida=("1000", "second"),
        ),
        _linha("gcp", "Cloud Run", "Services CPU (Request-based billing)", "5", "unclassified"),
        _linha(
            "gcp",
            "Cloud Run",
            "Cloud Run Network Internet Data Transfer Out North America to North America",
            "2",
            "network_egress",
            medida=("10", "gibibyte"),
        ),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "gcp")).auxiliary
    assert aux.por_uso == {"serverless_compute": Decimal("45")}
    assert aux.network_egress == Decimal("2")
    assert aux.network_other == Decimal("0")
    assert aux.orphan == Decimal("0")
    # Serverless fica ao custo atual: sem quantidade medida
    assert "serverless_compute" not in aux.quantidade_por_regiao


@pytest.mark.asyncio
async def test_plano_de_controle_so_a_hora_do_cluster() -> None:
    eks = "Amazon Elastic Container Service for Kubernetes"
    linhas = [
        _linha(
            "aws",
            eks,
            "USE1-AmazonEKS-Hours:perCluster",
            "74.4",
            "gke_control_plane",
            medida=("744", None),
        ),
        _linha(
            "aws",
            eks,
            "USE1-AmazonEKS-Hours:extendedSupport",
            "50",
            "gke_control_plane",
            medida=("100", None),
        ),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "aws")).auxiliary
    assert aux.gke_control_plane == Decimal("74.4")
    assert aux.por_uso == {"kubernetes_other": Decimal("50")}
    assert aux.quantidade_por_regiao["gke_control_plane"] == {"us-east-1": Decimal("744")}


@pytest.mark.asyncio
async def test_segredo_e_chave_saem_do_disco_e_do_orfao() -> None:
    linhas = [
        _linha(
            "aws",
            "AWS Secrets Manager",
            "USE1-AWSSecretsManager-Secrets",
            "3.6",
            "unclassified",
            medida=("9", "Units"),
        ),
        _linha(
            "aws",
            "AWS Secrets Manager",
            "USE1-AWSSecretsManagerAPIRequest",
            "0.10",
            "storage_hdd",
            medida=("20000", "Requests"),
        ),
        _linha(
            "aws",
            "AWS Key Management Service",
            "us-east-1-KMS-Keys",
            "45",
            "unclassified",
            medida=("45", "Units"),
        ),
        _linha(
            "aws",
            "AWS Key Management Service",
            "us-east-1-KMS-Requests",
            "0.42",
            "storage_hdd",
            medida=("140000", "Requests"),
        ),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "aws")).auxiliary
    assert aux.storage_hdd == Decimal("0")
    assert aux.orphan == Decimal("0")
    assert aux.por_uso == {
        "secrets": Decimal("3.6"),
        "secrets_operations": Decimal("0.10"),
        "key_management": Decimal("45"),
        "key_management_operations": Decimal("0.42"),
    }
    assert aux.quantidade_por_regiao["secrets_operations"] == {"us-east-1": Decimal("2")}
    assert aux.quantidade_por_regiao["key_management_operations"] == {"us-east-1": Decimal("14")}


@pytest.mark.asyncio
async def test_cdn_tem_categoria_propria_e_a_requisicao_do_s3_nao() -> None:
    linhas = [
        _linha(
            "aws",
            "Amazon CloudFront",
            "US-DataTransfer-Out-Bytes",
            "8.5",
            "network_egress",
            medida=("100", None),
        ),
        _linha(
            "aws",
            "Amazon CloudFront",
            "US-Requests-Tier2-HTTPS",
            "1",
            "unclassified",
            medida=("10000", "Requests"),
        ),
        _linha(
            "aws",
            "AmazonS3",
            "USE1-Requests-Tier1",
            "0.5",
            "unclassified",
            medida=("100000", "Requests"),
        ),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "aws")).auxiliary
    assert aux.cdn == Decimal("8.5")
    assert aux.cdn_requests == Decimal("1")
    assert aux.network_egress == Decimal("0")
    assert aux.orphan == Decimal("0.5")


@pytest.mark.asyncio
async def test_a_soma_dos_baldes_e_o_custo_das_linhas() -> None:
    linhas = [
        _linha(
            "gcp",
            "Kubernetes Engine",
            "Zonal Kubernetes Clusters",
            "74.4",
            "gke_control_plane",
            medida=("744", "hour"),
        ),
        _linha(
            "gcp", "Kubernetes Engine", "Zonal Kubernetes Clusters", "-74.4", "gke_control_plane"
        ),
        _linha(
            "gcp",
            "Secret Manager",
            "Secret version replica storage",
            "0.72",
            "storage_hdd",
            medida=("12", "month"),
        ),
        _linha(
            "gcp",
            "Cloud Run",
            "Services Min Instance Memory (Request-based billing)",
            "30",
            "network_egress",
            medida=("1000", "gibibyte second"),
        ),
        _linha(
            "gcp",
            "Networking",
            "Networking Cloud Cdn Cache Lookups",
            "0.75",
            "unclassified",
            medida=("1000000", "count"),
        ),
    ]
    resultado = await BillingAggregator().aggregate(linhas, "gcp")
    aux = resultado.auxiliary
    total = (
        aux.gke_control_plane
        + aux.cdn_requests
        + aux.storage_hdd
        + aux.network_egress
        + aux.orphan
        + sum(aux.por_uso.values(), Decimal(0))
    )
    assert total == sum((linha.cost for linha in linhas), Decimal(0))
    # O crédito do GKE abate o custo, mas não a quantidade medida
    assert aux.gke_control_plane == Decimal("0.0")
    assert aux.quantidade_por_regiao["gke_control_plane"] == {"": Decimal("744")}
    assert aux.quantidade_por_regiao["cdn_requests"] == {"": Decimal("100.0000")}


def test_custo_da_categoria_le_o_campo_ou_o_mapa() -> None:
    aux = AuxiliaryCosts(
        gke_control_plane=Decimal("10"),
        cdn=Decimal("2"),
        por_uso={"secrets": Decimal("3")},
    )
    assert aux.custo("gke_control_plane") == Decimal("10")
    assert aux.custo("cdn") == Decimal("2")
    assert aux.custo("secrets") == Decimal("3")
    assert aux.custo("serverless_compute") == Decimal("0")
    assert aux.custo("orphan_skus") == Decimal("0")
    assert aux.any_positive
    assert AuxiliaryCosts(por_uso={"secrets": Decimal("1")}).any_positive
