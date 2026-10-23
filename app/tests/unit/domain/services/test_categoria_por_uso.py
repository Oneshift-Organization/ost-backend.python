from decimal import Decimal

import pytest

from app.domain.services.categoria_por_uso import (
    ARQUIVOS,
    BALDES,
    BASTION_HORAS,
    BASTION_OUTROS,
    BASTION_SEGURANCA_E_SERVERLESS,
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
    SEGURANCA_EVENTOS,
    SEGURANCA_IMAGENS,
    SEGURANCA_OUTROS,
    SERVERLESS,
    SERVERLESS_MEMORIA,
    SERVERLESS_MEMORIA_OCIOSA,
    SERVERLESS_REQUISICOES,
    SERVERLESS_VCPU,
    SERVERLESS_VCPU_OCIOSA,
    UNIDADE,
    WAF_OUTROS,
    WAF_POLITICAS,
    WAF_REGRAS,
    WAF_REQUISICOES,
    quantidade_por_uso,
    tipo_por_uso,
)
from app.infrastructure.database.seed_category_rates import category_rate_points

_EKS = "Amazon Elastic Container Service for Kubernetes"
_AKS = "Azure Kubernetes Service"
_KMS_GCP = "Cloud Key Management Service (KMS)"
_DEFENDER = "Microsoft Defender for Cloud"
_ACA = "Azure Container Apps"


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
        # Contêiner serverless (R5): vCPU e memória, ativa e ociosa, e requisição
        ("serverless", "gcp", "Cloud Run", "Services Min Instance CPU (Request-based billing)",
         SERVERLESS_VCPU_OCIOSA),
        ("serverless", "gcp", "Cloud Run",
         "Services Min Instance Memory (Request-based billing)", SERVERLESS_MEMORIA_OCIOSA),
        ("serverless", "gcp", "Cloud Run", "Services CPU (Request-based billing)",
         SERVERLESS_VCPU),
        ("serverless", "gcp", "Cloud Run", "Services CPU (Instance-based billing) in us-east4",
         SERVERLESS_VCPU),
        ("serverless", "gcp", "Cloud Run", "Services Memory (Request-based billing)",
         SERVERLESS_MEMORIA),
        ("serverless", "gcp", "Cloud Run", "Requests", SERVERLESS_REQUISICOES),
        ("serverless", "azure", _ACA, f"{_ACA} | Standard vCPU Active Usage", SERVERLESS_VCPU),
        ("serverless", "azure", _ACA, f"{_ACA} | Standard vCPU Idle Usage",
         SERVERLESS_VCPU_OCIOSA),
        ("serverless", "azure", _ACA, f"{_ACA} | Standard Memory Active Usage",
         SERVERLESS_MEMORIA),
        ("serverless", "azure", _ACA, f"{_ACA} | Standard Memory Idle Usage",
         SERVERLESS_MEMORIA_OCIOSA),
        ("serverless", "azure", _ACA, f"{_ACA} | Standard Requests", SERVERLESS_REQUISICOES),
        # Functions, Lambda, Jobs e o plano Dedicated: ao custo atual
        ("serverless", "azure", _ACA, f"{_ACA} | Dedicated vCPU Usage", SERVERLESS),
        ("serverless", "azure", "Functions", "Functions | Standard Execution Time", SERVERLESS),
        ("serverless", "gcp", "Cloud Run", "Jobs CPU in us-central1", SERVERLESS),
        ("serverless", "aws", "AWS Lambda", "USE1-Lambda-GB-Second", SERVERLESS),
        # Bastion (R5): a hora do gateway; só a Azure cobra
        ("bastion", "azure", "Azure Bastion", "Azure Bastion | Standard Gateway", BASTION_HORAS),
        ("bastion", "azure", "Azure Bastion", "Azure Bastion | Premium Additional Gateway",
         BASTION_HORAS),
        ("bastion", "azure", "Azure Bastion", "Azure Bastion | Developer", BASTION_OUTROS),
        # Postura de segurança (R5): imagem verificada e evento do plano de controle
        ("security_posture", "azure", _DEFENDER,
         f"{_DEFENDER} | Azure Container Registry | Standard Image", SEGURANCA_IMAGENS),
        ("security_posture", "azure", _DEFENDER,
         f"{_DEFENDER} | Microsoft Defender for Containers | Standard Images", SEGURANCA_IMAGENS),
        ("security_posture", "azure", _DEFENDER,
         f"{_DEFENDER} | Azure ARM service layers | Standard Events", SEGURANCA_EVENTOS),
        ("security_posture", "azure", _DEFENDER, f"{_DEFENDER} | Servers | Standard P2 Node",
         SEGURANCA_OUTROS),
        ("security_posture", "azure", _DEFENDER, f"{_DEFENDER} | Free Images", SEGURANCA_OUTROS),
        ("security_posture", "aws", "AmazonInspectorV2", "USE1-container-image-initial-scan",
         SEGURANCA_IMAGENS),
        ("security_posture", "aws", "AmazonInspectorV2", "USE1-container-image-re-scan",
         SEGURANCA_OUTROS),
        ("security_posture", "aws", "AmazonGuardDuty", "USE1-PaidEventsAnalyzed",
         SEGURANCA_EVENTOS),
        ("security_posture", "aws", "AmazonGuardDuty", "USE1-PaidEventsAnalyzed-Bytes",
         SEGURANCA_OUTROS),
        ("security_posture", "gcp", "Container Registry Vulnerability Scanning",
         "Container Images Scanned", SEGURANCA_IMAGENS),
        ("security_posture", "gcp", "On-Demand Scanning", "Successful Scans", SEGURANCA_IMAGENS),
        ("security_posture", "gcp", "Security Command Center",
         "Security Command Center Premium for Compute Engine - Core Running", SEGURANCA_OUTROS),
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
        "bastion": BASTION_OUTROS,
        "security_posture": SEGURANCA_OUTROS,
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
        BASTION_HORAS: "hour",
        SEGURANCA_IMAGENS: "image",
        SEGURANCA_EVENTOS: "10k",
        SERVERLESS_VCPU: "hour",
        SERVERLESS_VCPU_OCIOSA: "hour",
        SERVERLESS_MEMORIA: "gib_hour",
        SERVERLESS_MEMORIA_OCIOSA: "gib_hour",
        SERVERLESS_REQUISICOES: "10k",
    }
    # O resto de cada balde fica ao custo atual
    for categoria in (
        KUBERNETES_OUTROS, SEGURANCA_OUTROS, SERVERLESS, ARQUIVOS, LOGS_OUTROS, DNS_OUTROS,
        WAF_OUTROS, REGISTRO_OUTROS, METRICAS_OUTROS, BASTION_OUTROS,
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
        *BASTION_SEGURANCA_E_SERVERLESS,
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
        # R5, Azure: a quantidade vem na unidade da Retail Prices API e o rótulo do export
        # não serve (export real de 23/10). Segundo vira hora; milhão vira 10 mil
        ("serverless", "azure", "Standard vCPU Idle Usage", ("384493", "Seconds"),
         str(Decimal(384493) / Decimal(3600))),
        ("serverless", "azure", "Standard Memory Idle Usage", ("769168", "PiB Seconds"),
         str(Decimal(769168) / Decimal(3600))),
        ("bastion", "azure", "Azure Bastion | Standard Gateway", ("47.383056", "hours"),
         "47.383056"),
        ("security_posture", "azure", "Azure Container Registry | Standard Image",
         ("53", "100"), "53"),
        ("security_posture", "azure", "Azure ARM service layers | Standard Events",
         ("0.026881", "10000000"), "2.6881"),
        # R5, GCP: o rótulo do catálogo diz a escala; a imagem vem em "count"
        ("serverless", "gcp", "Services Min Instance CPU (Request-based billing)",
         ("7200", "second"), "2"),
        ("serverless", "gcp", "Services Memory (Request-based billing)",
         ("3600", "gibibyte second"), "1"),
        ("serverless", "gcp", "Services CPU (Request-based billing)", ("3", "hours"), "3"),
        ("serverless", "gcp", "Requests", ("2000000", "count"), "200"),
        ("security_posture", "gcp", "Container Images Scanned", ("40", "count"), "40"),
        # R5, AWS: o evento contado um a um vira 10 mil
        ("security_posture", "aws", "USE1-PaidEventsAnalyzed", ("26881", "Events"), "2.6881"),
        ("security_posture", "aws", "USE1-container-image-initial-scan", ("12", None), "12"),
    ],
)
def test_quantidade_por_uso(balde, provedor, uso, linha, esperado):
    quantidade, unidade = linha
    tipo = tipo_por_uso(balde, provedor, "x", uso)
    valor = quantidade_por_uso(provedor, tipo, Decimal(quantidade), unidade)
    assert valor == (Decimal(esperado) if esperado else None)


def test_taxas_do_r5_na_unidade_da_categoria():
    taxas = {(p.provider, p.service_type): p for p in category_rate_points()}
    for categoria in BASTION_SEGURANCA_E_SERVERLESS:
        if categoria not in UNIDADE:
            continue
        provedores = {prov for prov, cat in taxas if cat == categoria}
        # O GCP não cobra evento avulso (vem na assinatura do Security Command Center)
        esperado = {"aws", "azure", "oci"} if categoria == SEGURANCA_EVENTOS else {
            "aws", "azure", "gcp", "oci"
        }
        assert provedores == esperado, categoria
        for prov in provedores:
            assert taxas[(prov, categoria)].unit == UNIDADE[categoria], (prov, categoria)
    # O destino que não cobra o acesso sem IP público vale zero, não fica sem cotação
    assert taxas[("aws", BASTION_HORAS)].price == 0
    assert taxas[("azure", SEGURANCA_IMAGENS)].price == Decimal("0.29")
