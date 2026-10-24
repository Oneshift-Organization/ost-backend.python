import pytest

from app.domain.services.reclassificacao_por_uso import (
    ARQUIVOS,
    BASTION,
    ENDPOINT_PRIVADO,
    FUNCOES,
    LINK_DEDICADO,
    SEGREDOS,
    SEGURANCA,
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
            FUNCOES,
        ),
        # O tráfego do Cloud Run continua rede
        (
            "gcp",
            "Cloud Run",
            "Cloud Run Network Internet Data Transfer Out North America to North America",
            "network_egress",
            "network_egress",
        ),
        # Functions têm balde próprio (§8 da conferência)
        ("aws", "AWS Lambda", "USE1-Lambda-GB-Second", "unclassified", FUNCOES),
        ("aws", "AWSLambda", "USE1-Request", "unclassified", FUNCOES),
        ("aws", "AWS Lambda", "USE1-DataTransfer-Out-Bytes", "network_egress", "network_egress"),
        ("azure", "Functions", "Functions | | Standard Execution Time", "unclassified", FUNCOES),
        (
            "azure",
            "Azure Container Apps",
            "Azure Container Apps | Standard | Standard vCPU Active Usage",
            "unclassified",
            SERVERLESS,
        ),
        ("oci", "FUNCTIONS", "Functions - Execution Time", "unclassified", FUNCOES),
        # Link dedicado e endpoint privado (§8 da conferência)
        ("aws", "AWSDirectConnect", "USE1-165HS-PortUsage:1G", "unclassified", LINK_DEDICADO),
        ("aws", "AmazonVPC", "USE1-VpcEndpoint-Hours", "network_egress", ENDPOINT_PRIVADO),
        (
            "azure",
            "ExpressRoute",
            "ExpressRoute | ExpressRoute | Metered Data - Data Transfer Out",
            "network_egress",
            LINK_DEDICADO,
        ),
        (
            "azure",
            "Virtual Network",
            "Virtual Network | Virtual Network Private Link | Standard Private Endpoint",
            "unclassified",
            ENDPOINT_PRIVADO,
        ),
        (
            "gcp",
            "Networking",
            "Cloud Interconnect - Local Data Transfer in UnitedStates",
            "network_egress",
            LINK_DEDICADO,
        ),
        (
            "gcp",
            "Networking",
            "Networking Private Service Connect Consumer End Point",
            "unclassified",
            ENDPOINT_PRIVADO,
        ),
        ("oci", "NETWORK", "OCI - FastConnect 1 Gbps", "network_egress", LINK_DEDICADO),
        # Vizinhos: o endpoint do Container Apps fica com o Container Apps
        (
            "azure",
            "Azure Container Apps",
            "Azure Container Apps | Azure Container Apps | Environment Private Endpoint",
            "unclassified",
            SERVERLESS,
        ),
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
        # Armazenamento de arquivos: nem disco nem objeto (T4)
        (
            "gcp",
            "Cloud Filestore",
            "Filestore Capacity Zonal and High Scale Iowa",
            "storage_hdd",
            ARQUIVOS,
        ),
        ("aws", "AmazonEFS", "USE1-TimedStorage-ByteHrs", "object_storage", ARQUIVOS),
        ("aws", "AmazonEFS", "USE1-IATimedStorage-ET-ByteHrs", "storage_hdd", ARQUIVOS),
        # O backup do EFS é backup, não o sistema de arquivos
        ("aws", "AWS Backup", "USE1-WarmStorage-ByteHrs-EFS", "storage_hdd", "storage_hdd"),
        ("azure", "Storage", "Storage | Files v2 | LRS Data Stored", "storage_hdd", ARQUIVOS),
        (
            "azure",
            "Storage",
            "Storage | Premium SSD Managed Disks | P10 LRS Disk",
            "storage_ssd",
            "storage_ssd",
        ),
        ("oci", "FILE_STORAGE", "File Storage - Storage", "storage_hdd", ARQUIVOS),
        # Requisição da CloudFront e consulta ao cache do Cloud CDN são rede (CDN)
        ("aws", "Amazon CloudFront", "US-Requests-Tier2-HTTPS", "unclassified", "network_egress"),
        ("aws", "Amazon CloudFront", "US-Requests-Tier2-HTTPS", "storage_hdd", "network_egress"),
        # A requisição do S3 não é CDN: vai para requisição de objeto (§7 da conferência)
        ("aws", "AmazonS3", "USE1-Requests-Tier1", "unclassified", "object_requests"),
        (
            "gcp",
            "Networking",
            "Networking Cloud Cdn Cache Lookups",
            "unclassified",
            "network_egress",
        ),
        # R5: o gateway do Bastion é bastion; a saída de dados dele é rede
        ("azure", "Azure Bastion", "Azure Bastion | Standard Gateway", "unclassified", BASTION),
        (
            "azure",
            "Azure Bastion",
            "Azure Bastion | Basic Data Transfer Out",
            "unclassified",
            "network_egress",
        ),
        # R5: postura de segurança, nas quatro nuvens
        (
            "azure",
            "Microsoft Defender for Cloud",
            "Microsoft Defender for Cloud | Azure Container Registry | Standard Image",
            "unclassified",
            SEGURANCA,
        ),
        ("aws", "AmazonGuardDuty", "USE1-PaidEventsAnalyzed", "unclassified", SEGURANCA),
        ("aws", "AmazonInspectorV2", "USE1-EC2-Scanning", "unclassified", SEGURANCA),
        ("aws", "AWSSecurityHub", "USE1-PaidComplianceCheck", "unclassified", SEGURANCA),
        (
            "gcp",
            "Container Registry Vulnerability Scanning",
            "Container Images Scanned",
            "unclassified",
            SEGURANCA,
        ),
        ("gcp", "Security Command Center", "Premium Trial", "unclassified", SEGURANCA),
        ("oci", "Cloud Guard", "Cloud Guard - Targets", "unclassified", SEGURANCA),
        # O registro de imagens continua registro: só a verificação é segurança
        (
            "azure",
            "Container Registry",
            "Container Registry | Basic Registry Unit",
            "x",
            "container_registry",
        ),
    ],
)
def test_reclassificar(provedor, servico, uso, antes, depois) -> None:
    assert reclassificar(provedor, servico, uso, antes) == depois
