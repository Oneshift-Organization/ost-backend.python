from decimal import Decimal

import pytest

from app.domain.services.categoria_por_uso import (
    ARQUIVOS,
    BALDES,
    CHAVE,
    CHAVE_NO_PAYLOAD,
    CHAVE_OPERACOES,
    DNS_CONSULTAS,
    DNS_OUTROS,
    DNS_ZONAS,
    KUBERNETES_OUTROS,
    LOGS_INGESTAO,
    LOGS_INGESTAO_BASICA,
    LOGS_OUTROS,
    LOGS_RETENCAO,
    METRICAS,
    METRICAS_OUTROS,
    OBSERVABILIDADE_E_BORDA,
    PLANO_DE_CONTROLE,
    REGISTRO,
    REGISTRO_OUTROS,
    SEGREDO,
    SEGREDO_OPERACOES,
    SEGURANCA_OUTROS,
    SERVERLESS,
    UNIDADE,
    WAF_OUTROS,
    WAF_POLITICAS,
    WAF_REGRAS,
    WAF_REQUISICOES,
    quantidade_por_uso,
    tipo_por_uso,
)

_EKS = "Amazon Elastic Container Service for Kubernetes"
_AKS = "Azure Kubernetes Service"
_KMS_GCP = "Cloud Key Management Service (KMS)"


@pytest.mark.parametrize(
    ("balde", "provedor", "servico", "uso", "esperado"),
    [
        # Plano de controle: só a hora do cluster é cotada
        ("gke_control_plane", "aws", _EKS, "USE1-AmazonEKS-Hours:perCluster", PLANO_DE_CONTROLE),
        ("gke_control_plane", "aws", _EKS, "USE1-AmazonEKS-Hours:extendedSupport",
         KUBERNETES_OUTROS),
        ("gke_control_plane", "aws", "AmazonEKS", "USE1-AmazonEKS-Hours:XL-ProvisionedTier",
         KUBERNETES_OUTROS),
        ("gke_control_plane", "gcp", "Kubernetes Engine", "Zonal Kubernetes Clusters",
         PLANO_DE_CONTROLE),
        ("gke_control_plane", "gcp", "Kubernetes Engine", "Regional Kubernetes Clusters",
         PLANO_DE_CONTROLE),
        ("gke_control_plane", "gcp", "Kubernetes Engine", "Autopilot Kubernetes Clusters",
         PLANO_DE_CONTROLE),
        ("gke_control_plane", "gcp", "Kubernetes Engine", "Kubernetes Clusters Management Fee",
         PLANO_DE_CONTROLE),
        ("gke_control_plane", "gcp", "Kubernetes Engine", "Extended Period Kubernetes Clusters",
         KUBERNETES_OUTROS),
        ("gke_control_plane", "azure", _AKS, f"{_AKS} | Standard | Standard Uptime SLA",
         PLANO_DE_CONTROLE),
        ("gke_control_plane", "azure", _AKS, f"{_AKS} | Standard | Standard Long Term Support",
         KUBERNETES_OUTROS),
        ("gke_control_plane", "oci", "CONTAINER_ENGINE",
         "OCI Kubernetes Engine - Enhanced Cluster", PLANO_DE_CONTROLE),
        ("gke_control_plane", "oci", "CONTAINER_ENGINE", "OCI Kubernetes Engine - Virtual Node",
         KUBERNETES_OUTROS),
        # Segredos e chaves
        ("secrets", "aws", "AWS Secrets Manager", "USE1-AWSSecretsManager-Secrets", SEGREDO),
        ("secrets", "aws", "AWS Secrets Manager", "USE1-AWSSecretsManagerAPIRequest",
         SEGREDO_OPERACOES),
        ("secrets", "aws", "AWS Secrets Manager", "EU-AWSSecretsManager-APIRequests",
         SEGREDO_OPERACOES),
        ("secrets", "aws", "AWS Key Management Service", "sa-east-1-KMS-Keys", CHAVE),
        ("secrets", "aws", "AWS Key Management Service", "us-east-1-KMS-Requests",
         CHAVE_OPERACOES),
        ("secrets", "gcp", "Secret Manager", "Secret version replica storage", SEGREDO),
        ("secrets", "gcp", "Secret Manager", "Secret access operations", SEGREDO_OPERACOES),
        ("secrets", "gcp", "Secret Manager", "Secret rotate operations", SEGURANCA_OUTROS),
        ("secrets", "gcp", _KMS_GCP, "Active software symmetric key versions", CHAVE),
        ("secrets", "gcp", _KMS_GCP, "Cryptographic operations with a software symmetric key",
         CHAVE_OPERACOES),
        ("secrets", "azure", "Key Vault", "Key Vault | Standard | Operations", SEGREDO_OPERACOES),
        ("secrets", "azure", "Key Vault", "Key Vault | Premium | Advanced Key Operations",
         CHAVE_OPERACOES),
        ("secrets", "azure", "Key Vault",
         "Key Vault | Premium | Premium HSM-protected RSA 2048-bit key", CHAVE),
        ("secrets", "azure", "Key Vault", "Key Vault | Standard | Certificate Renewal Request",
         SEGURANCA_OUTROS),
        ("secrets", "oci", "KEY_MANAGEMENT", "Key Management Service - Key Versions", CHAVE),
        ("secrets", "oci", "KEY_MANAGEMENT", "OCI - Key Management - Private Vault",
         SEGURANCA_OUTROS),
        # Serverless: um balde só, ao custo atual
        ("serverless", "gcp", "Cloud Run", "Services Min Instance CPU (Request-based billing)",
         SERVERLESS),
        ("serverless", "aws", "AWS Lambda", "USE1-Lambda-GB-Second", SERVERLESS),
    ],
)
def test_tipo_por_uso(balde, provedor, servico, uso, esperado):
    assert tipo_por_uso(balde, provedor, servico, uso).categoria == esperado


@pytest.mark.parametrize(
    ("uso", "fator"),
    [
        # Requisição da AWS contada uma a uma: a categoria é em 10 mil
        ("USE1-AWSSecretsManagerAPIRequest", "0.0001"),
        ("us-east-1-KMS-Requests", "0.0001"),
        ("USE1-AWSSecretsManager-Secrets", "1"),
        ("us-east-1-KMS-Keys", "1"),
    ],
)
def test_fator_da_aws(uso, fator):
    assert tipo_por_uso("secrets", "aws", "x", uso).fator == Decimal(fator)


def test_balde_desconhecido_ou_sem_uso_vai_para_o_resto_do_balde():
    assert tipo_por_uso("gke_control_plane", "aws", None, None).categoria == KUBERNETES_OUTROS
    assert tipo_por_uso("secrets", "nuvem-nova", "x", "y").categoria == SEGURANCA_OUTROS
    assert BALDES == {
        "gke_control_plane": KUBERNETES_OUTROS,
        "secrets": SEGURANCA_OUTROS,
        "serverless": SERVERLESS,
        "file_storage": ARQUIVOS,
        "logging": LOGS_OUTROS,
        "dns": DNS_OUTROS,
        "waf": WAF_OUTROS,
        "container_registry": REGISTRO_OUTROS,
        "metrics": METRICAS_OUTROS,
    }


def test_unidade_de_cada_categoria_cotada():
    assert UNIDADE == {
        PLANO_DE_CONTROLE: "hour",
        SEGREDO: "month",
        SEGREDO_OPERACOES: "10k",
        CHAVE: "month",
        CHAVE_OPERACOES: "10k",
        LOGS_INGESTAO: "gb",
        LOGS_INGESTAO_BASICA: "gb",
        LOGS_RETENCAO: "gb_month",
        DNS_CONSULTAS: "10k",
        DNS_ZONAS: "month",
        WAF_REQUISICOES: "10k",
        WAF_POLITICAS: "month",
        WAF_REGRAS: "month",
        REGISTRO: "gb_month",
        METRICAS: "10k",
    }
    # O resto de cada balde fica ao custo atual
    for categoria in (
        KUBERNETES_OUTROS, SEGURANCA_OUTROS, SERVERLESS, ARQUIVOS, LOGS_OUTROS, DNS_OUTROS,
        WAF_OUTROS, REGISTRO_OUTROS, METRICAS_OUTROS,
    ):
        assert categoria not in UNIDADE


def test_chave_no_payload():
    # O plano de controle mantém a chave de sempre: o agente e o Terraform já a leem
    assert CHAVE_NO_PAYLOAD[PLANO_DE_CONTROLE] == "gke_plano_controle"
    assert set(CHAVE_NO_PAYLOAD) == {
        PLANO_DE_CONTROLE,
        KUBERNETES_OUTROS,
        SEGREDO,
        SEGREDO_OPERACOES,
        CHAVE,
        CHAVE_OPERACOES,
        SEGURANCA_OUTROS,
        SERVERLESS,
        ARQUIVOS,
        *OBSERVABILIDADE_E_BORDA,
    }


@pytest.mark.parametrize(
    ("balde", "provedor", "uso", "linha", "esperado"),
    [
        # AWS: o UsageType diz a unidade; o fator converte requisição em 10 mil
        ("gke_control_plane", "aws", "USE1-AmazonEKS-Hours:perCluster", ("744", None), "744"),
        ("secrets", "aws", "USE1-AWSSecretsManagerAPIRequest", ("20000", "Requests"), "2"),
        ("secrets", "aws", "USE1-AWSSecretsManager-Secrets", ("9", "Units"), "9"),
        # GCP: contagem vira 10 mil pela escala da unidade
        ("secrets", "gcp", "Secret access operations", ("30000", "count"), "3"),
        ("secrets", "gcp", "Secret version replica storage", ("12", "month"), "12"),
        ("gke_control_plane", "gcp", "Zonal Kubernetes Clusters", ("744", "hour"), "744"),
        # Azure: a operação já vem em 10 mil
        ("secrets", "azure", "Key Vault | Standard | Operations", ("5", "10K"), "5"),
        ("gke_control_plane", "azure", f"{_AKS} | Standard | Standard Uptime SLA",
         ("744", "1 Hour"), "744"),
        # Unidade de outra dimensão, ou desconhecida: sem quantidade
        ("secrets", "gcp", "Secret version replica storage", ("12", "hour"), None),
        ("gke_control_plane", "gcp", "Zonal Kubernetes Clusters", ("744", ""), None),
        # Categoria não cotada e quantidade não positiva
        ("gke_control_plane", "gcp", "Extended Period Kubernetes Clusters", ("10", "hour"), None),
        ("gke_control_plane", "gcp", "Zonal Kubernetes Clusters", ("0", "hour"), None),
    ],
)
def test_quantidade_por_uso(balde, provedor, uso, linha, esperado):
    quantidade, unidade = linha
    tipo = tipo_por_uso(balde, provedor, "x", uso)
    valor = quantidade_por_uso(provedor, tipo, Decimal(quantidade), unidade)
    assert valor == (Decimal(esperado) if esperado else None)
