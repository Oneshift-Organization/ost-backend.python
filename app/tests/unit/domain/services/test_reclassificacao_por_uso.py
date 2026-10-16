import pytest

from app.domain.services.reclassificacao_por_uso import (
    SEGREDOS,
    SEM_COTACAO,
    SERVERLESS,
    reclassificar,
)


@pytest.mark.parametrize(
    ("provedor", "servico", "uso", "antes", "depois"),
    [
        ("aws", "AmazonRDS", "HeavyUsage:db.r8g.2xl", "core", "database"),
        ("aws", "AmazonElastiCache", "HeavyUsage:cache.t4g.medium", "core", "database"),
        ("aws", "AmazonRDS", "HeavyUsage:db.t4g.medium", "database", "database"),
        (
            "aws",
            "Amazon Managed Streaming for Apache Kafka",
            "USE1-Kafka.m5.large",
            "database",
            SEM_COTACAO,
        ),
        ("aws", "AmazonMSK", "USE1-Kafka.Storage.GP2", "database", SEM_COTACAO),
        # O tráfego do MSK segue a rede
        (
            "aws",
            "Amazon Managed Streaming for Apache Kafka",
            "DataTransfer-Regional-Bytes",
            "network_egress",
            "network_egress",
        ),
        ("gcp", "Source Repository", "Storage", "storage_hdd", "object_storage"),
        # O mesmo SKU em outro serviço não muda
        ("gcp", "Cloud Storage", "Storage", "object_storage", "object_storage"),
        ("aws", "AmazonEC2", "BoxUsage:c7g.large", "core", "core"),
        ("azure", "Virtual Machines", "D4s v5", "core", "core"),
        # Cloud Run e afins: computação serverless, não rede nem órfão (R4)
        (
            "gcp",
            "Cloud Run",
            "Services Min Instance CPU (Request-based billing)",
            "network_egress",
            SERVERLESS,
        ),
        ("gcp", "Cloud Run", "Services CPU (Request-based billing)", "unclassified", SERVERLESS),
        ("gcp", "Cloud Run", "Requests", "unclassified", SERVERLESS),
        (
            "gcp",
            "Cloud Run Functions",
            "Cloud Run functions Min-Instance Memory (Request-based billing) in us-central1",
            "network_egress",
            SERVERLESS,
        ),
        # O tráfego do Cloud Run continua rede
        (
            "gcp",
            "Cloud Run",
            "Cloud Run Network Internet Data Transfer Out North America to North America",
            "network_egress",
            "network_egress",
        ),
        ("aws", "AWS Lambda", "USE1-Lambda-GB-Second", "unclassified", SERVERLESS),
        ("aws", "AWSLambda", "USE1-Request", "unclassified", SERVERLESS),
        ("aws", "AWS Lambda", "USE1-DataTransfer-Out-Bytes", "network_egress", "network_egress"),
        ("azure", "Functions", "Functions | | Standard Execution Time", "unclassified", SERVERLESS),
        (
            "azure",
            "Azure Container Apps",
            "Azure Container Apps | Standard | Standard vCPU Active Usage",
            "unclassified",
            SERVERLESS,
        ),
        ("oci", "FUNCTIONS", "Functions - Execution Time", "unclassified", SERVERLESS),
        # Segredos e chaves: nem disco nem órfão
        ("aws", "AWS Secrets Manager", "USE1-AWSSecretsManager-Secrets", "unclassified", SEGREDOS),
        (
            "aws",
            "AWS Secrets Manager",
            "USE1-AWSSecretsManagerAPIRequest",
            "storage_hdd",
            SEGREDOS,
        ),
        ("aws", "AWS Key Management Service", "us-east-1-KMS-Requests", "storage_hdd", SEGREDOS),
        ("aws", "awskms", "sa-east-1-KMS-Keys", "unclassified", SEGREDOS),
        ("gcp", "Secret Manager", "Secret version replica storage", "storage_hdd", SEGREDOS),
        (
            "gcp",
            "Cloud Key Management Service (KMS)",
            "Active software symmetric key versions",
            "unclassified",
            SEGREDOS,
        ),
        ("azure", "Key Vault", "Key Vault | Standard | Operations", "unclassified", SEGREDOS),
        (
            "oci",
            "KEY_MANAGEMENT",
            "Key Management Service - Key Versions",
            "unclassified",
            SEGREDOS,
        ),
        # Requisição da CloudFront e consulta ao cache do Cloud CDN são rede (CDN)
        ("aws", "Amazon CloudFront", "US-Requests-Tier2-HTTPS", "unclassified", "network_egress"),
        ("aws", "Amazon CloudFront", "US-Requests-Tier2-HTTPS", "storage_hdd", "network_egress"),
        # A requisição do S3 não muda
        ("aws", "AmazonS3", "USE1-Requests-Tier1", "unclassified", "unclassified"),
        (
            "gcp",
            "Networking",
            "Networking Cloud Cdn Cache Lookups",
            "unclassified",
            "network_egress",
        ),
    ],
)
def test_reclassificar(provedor, servico, uso, antes, depois) -> None:
    assert reclassificar(provedor, servico, uso, antes) == depois
