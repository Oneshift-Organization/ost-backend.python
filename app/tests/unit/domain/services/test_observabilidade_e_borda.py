"""Logs, DNS, WAF, registro de imagens e métricas pela quantidade (PLANO_EQUIVALENTES_E_TARIFAS §3).

A linha vai para o balde pelo nome do serviço (`reclassificacao_por_uso`), a regra do uso
dá a categoria e o fator, e a unidade do export converte a quantidade. O que não casa
fica no resto do balde, ao custo atual.
"""

from decimal import Decimal

import pytest

from app.domain.services.categoria_por_uso import (
    DNS_CONSULTAS,
    DNS_OUTROS,
    DNS_ZONAS,
    LOGS_INGESTAO,
    LOGS_INGESTAO_BASICA,
    LOGS_OUTROS,
    LOGS_RETENCAO,
    METRICAS,
    METRICAS_OUTROS,
    REGISTRO,
    REGISTRO_OUTROS,
    WAF_OUTROS,
    WAF_POLITICAS,
    WAF_REGRAS,
    WAF_REQUISICOES,
    quantidade_por_uso,
    tipo_por_uso,
)
from app.domain.services.reclassificacao_por_uso import reclassificar


def _categoria(provedor: str, servico: str, uso: str) -> str:
    balde = reclassificar(provedor, servico, uso, "unclassified")
    return tipo_por_uso(balde, provedor, servico, uso).categoria


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "esperado"),
    [
        # Logs: ingestão padrão e básica, retenção; a consulta (GB varrido) fica
        ("aws", "AmazonCloudWatch", "USE1-DataProcessing-Bytes", LOGS_INGESTAO),
        ("aws", "AmazonCloudWatch", "USE1-VendedLog-Bytes", LOGS_INGESTAO),
        ("aws", "AmazonCloudWatch", "USE1-VendedLogIA-Bytes-WAFLogs", LOGS_INGESTAO_BASICA),
        ("aws", "AmazonCloudWatch", "USE1-DataProcessingIA-Bytes", LOGS_INGESTAO_BASICA),
        ("aws", "AmazonCloudWatch", "USE1-TimedStorage-ByteHrs", LOGS_RETENCAO),
        ("aws", "AmazonCloudWatch", "USE1-DataScanned-Bytes", LOGS_OUTROS),
        ("azure", "Log Analytics", "Log Analytics | Basic Logs | Basic Logs Data Ingestion",
         LOGS_INGESTAO_BASICA),
        ("azure", "Log Analytics",
         "Log Analytics | Analytics Logs | Analytics Logs Data Ingestion", LOGS_INGESTAO),
        ("azure", "Log Analytics",
         "Log Analytics | Analytics Logs | Analytics Logs Data Retention", LOGS_RETENCAO),
        ("azure", "Azure Monitor", "Azure Monitor | Basic Logs | Basic Logs Data Ingestion",
         LOGS_INGESTAO_BASICA),
        ("gcp", "Cloud Logging", "Log Storage cost", LOGS_INGESTAO),
        ("gcp", "Cloud Logging", "Vended Logs Storage", LOGS_INGESTAO_BASICA),
        ("gcp", "Cloud Logging", "Log Retention cost", LOGS_RETENCAO),
        ("oci", "LOGGING", "OCI - Logging - Storage", LOGS_INGESTAO),
        # DNS: consulta e zona; a consulta interna da AWS (grátis) fica
        ("aws", "AmazonRoute53", "DNS-Queries", DNS_CONSULTAS),
        ("aws", "Amazon Route 53", "HostedZone", DNS_ZONAS),
        ("aws", "AmazonRoute53", "Intra-AWS-DNS-Queries", DNS_OUTROS),
        ("azure", "Azure DNS", "Azure DNS | Public Zone | DNS Queries", DNS_CONSULTAS),
        ("azure", "Azure DNS", "Azure DNS | Public | Public Zone", DNS_ZONAS),
        ("gcp", "Cloud DNS", "DNS Query (port 53)", DNS_CONSULTAS),
        ("gcp", "Cloud DNS", "ManagedZone", DNS_ZONAS),
        ("oci", "DNS", "Networking - DNS", DNS_CONSULTAS),
        # WAF: requisição, política e regra; Bot Control é complemento e fica
        ("aws", "AWS WAF", "USE1-RequestV2-Tier1", WAF_REQUISICOES),
        ("aws", "awswaf", "USE1-ShieldProtected-RequestV2-Tier6-4000WCU", WAF_REQUISICOES),
        ("aws", "AWS WAF", "USE1-AMR-BotControl-Request", WAF_OUTROS),
        ("aws", "AWS WAF", "USE1-WebACLV2", WAF_POLITICAS),
        ("aws", "AWS WAF", "USE1-RuleV2", WAF_REGRAS),
        ("azure", "Azure Front Door Service",
         "Azure Front Door Service | Standard | Standard Policy", WAF_POLITICAS),
        ("gcp", "Networking", "Networking Cloud Armor Requests", WAF_REQUISICOES),
        ("gcp", "Networking", "Networking Cloud Armor Rule", WAF_REGRAS),
        ("oci", "WAF", "Web Application Firewall - Requests", WAF_REQUISICOES),
        ("oci", "WAF", "Web Application Firewall - Instance", WAF_POLITICAS),
        # Registro de imagens: o armazenamento; a taxa diária do ACR fica
        ("aws", "AmazonECR", "USE1-TimedStorage-ByteHrs", REGISTRO),
        ("azure", "Container Registry", "Container Registry | Basic | Data Stored", REGISTRO),
        ("azure", "Container Registry", "Container Registry | Basic | Basic Registry Unit",
         REGISTRO_OUTROS),
        ("gcp", "Artifact Registry", "Artifact Registry Storage", REGISTRO),
        # Métricas: amostra de Prometheus gerenciado; métrica customizada fica
        ("aws", "AmazonPrometheus", "USE1-AMP:MetricSampleCount", METRICAS),
        ("aws", "AmazonCloudWatch", "CW:GMD-Metrics", METRICAS_OUTROS),
        ("azure", "Azure Monitor", "Azure Monitor | Managed Prometheus | Metrics Ingestion",
         METRICAS),
        ("gcp", "Cloud Monitoring", "Prometheus Samples Ingested", METRICAS),
        ("gcp", "Cloud Monitoring", "Time series billed count", METRICAS_OUTROS),
        ("oci", "MONITORING", "Monitoring - Ingestion", METRICAS),
    ],
)
def test_categoria_de_cada_linha(provedor, servico, uso, esperado):
    assert _categoria(provedor, servico, uso) == esperado


@pytest.mark.parametrize(
    ("provedor", "servico", "uso"),
    [
        # O tráfego do registro continua rede; o egress da OCI também
        ("aws", "AmazonECR", "USE1-DataTransfer-Out-Bytes"),
        ("oci", "NETWORK", "Outbound Data Transfer Zone 1"),
        # Regra de roteamento do Front Door não é regra de WAF
        ("azure", "Azure Front Door Service",
         "Azure Front Door Service | Standard | Standard Included Routing Rules"),
    ],
)
def test_linha_de_fora_nao_muda_de_balde(provedor, servico, uso):
    assert reclassificar(provedor, servico, uso, "network_egress") == "network_egress"


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "linha", "esperado"),
    [
        # AWS: o UsageType diz a unidade; requisição e consulta contadas uma a uma
        ("aws", "AWS WAF", "USE1-RequestV2-Tier1", ("4578000000", "Requests"), "457800"),
        ("aws", "AmazonRoute53", "DNS-Queries", ("1000000", ""), "100"),
        ("aws", "AmazonCloudWatch", "USE1-DataProcessing-Bytes", ("773.5", "GB"), "773.5"),
        ("aws", "AmazonPrometheus", "USE1-AMP:MetricSampleCount", ("20000", ""), "2"),
        # Azure: a escala vem no rótulo
        ("azure", "Azure DNS", "Azure DNS | Public Zone | DNS Queries", ("5502.38", "1M"),
         "550238"),
        ("azure", "Azure Monitor", "Azure Monitor | Managed Prometheus | Metrics Ingestion",
         ("2", "10M Samples"), "2000"),
        ("azure", "Log Analytics",
         "Log Analytics | Analytics Logs | Analytics Logs Data Retention",
         ("38930", "gibibyte month"), "38930"),
        # GCP: contagem vira 10 mil
        ("gcp", "Cloud DNS", "DNS Query (port 53)", ("30000", "count"), "3"),
        ("gcp", "Artifact Registry", "Artifact Registry Storage", ("12", "gibibyte month"), "12"),
        # OCI: o GB-mês do log vale como GB ingerido
        ("oci", "LOGGING", "OCI - Logging - Storage", ("120", "Gigabyte Log Storage Per Month"),
         "120"),
        ("oci", "DNS", "Networking - DNS", ("3", "1,000,000 Queries"), "300"),
        # Unidade de outra dimensão: sem quantidade (cai na razão de taxas)
        ("gcp", "Cloud Logging", "Log Retention cost", ("5", "gibibyte"), None),
    ],
)
def test_quantidade_na_unidade_da_categoria(provedor, servico, uso, linha, esperado):
    balde = reclassificar(provedor, servico, uso, "unclassified")
    tipo = tipo_por_uso(balde, provedor, servico, uso)
    quantidade = quantidade_por_uso(provedor, tipo, Decimal(linha[0]), linha[1])
    assert quantidade == (Decimal(esperado) if esperado else None)
