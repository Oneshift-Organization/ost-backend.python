"""Taxas de referência por categoria não-compute (Wave 4).

Preços on-demand publicados (~2026), US-East, por unidade natural da categoria.
São dados de REFERÊNCIA (``confidence="reference"``) com proveniência — melhores
que um balde opaco, honestamente rotulados como não-oficiais. Um fetch das APIs
(Azure/OCI) pode fazer upgrade de linhas específicas para ``official``.

Reusa ``cloud_price_points``: ``service_type`` = categoria, ``unit`` = gb_month/gb.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from app.domain.entities.cloud_price_point import CloudPricePoint

_SOURCE = "published_reference_2026"

# (provider, category, unit, rate USD)
_RATES: tuple[tuple[str, str, str, str], ...] = (
    # Block SSD de propósito geral ($/GB-mês). No GCP é o pd-balanced, o disco
    # padrão das VMs e o par do gp3; o pd-ssd (US$ 0,170) é o tier de desempenho.
    # Cotar pela taxa dele tratava todo SSD como o mais caro: o GB saía ~40% menor
    # na origem e o GCP, ~2x mais caro como destino.
    ("aws", "storage_ssd", "gb_month", "0.080"),     # gp3
    ("gcp", "storage_ssd", "gb_month", "0.100"),     # pd-balanced
    ("azure", "storage_ssd", "gb_month", "0.088"),   # premium ssd
    ("oci", "storage_ssd", "gb_month", "0.0425"),    # block balanced: 0,0255 + 10 VPU x 0,0017
    # Armazenamento de objetos, tier padrão ($/GB-mês). Cerca de metade da taxa
    # de bloco — é essa diferença que tornava a classificação errada cara: a
    # linha de S3 era reprecificada como disco e o GB estimado saía pela metade.
    ("aws", "object_storage", "gb_month", "0.023"),    # S3 Standard
    ("gcp", "object_storage", "gb_month", "0.020"),    # Cloud Storage Standard
    ("azure", "object_storage", "gb_month", "0.018"),  # Blob Hot (LRS)
    ("oci", "object_storage", "gb_month", "0.0255"),   # Object Storage Standard
    # Storage frio/HDD ($/GB-mês)
    ("aws", "storage_hdd", "gb_month", "0.045"),     # st1
    ("gcp", "storage_hdd", "gb_month", "0.040"),     # pd-standard
    ("azure", "storage_hdd", "gb_month", "0.045"),   # standard hdd
    ("oci", "storage_hdd", "gb_month", "0.0255"),    # block lower cost (0 VPU)
    # Egress de internet, primeira faixa ($/GB)
    ("aws", "network_egress", "gb", "0.090"),
    ("gcp", "network_egress", "gb", "0.120"),
    ("azure", "network_egress", "gb", "0.087"),
    ("oci", "network_egress", "gb", "0.0085"),       # OCI egress ~10x mais barato
    # Banco gerenciado por vCPU-hora de instância, uso geral x86, sem alta
    # disponibilidade, leste dos EUA (PLANO_PRECISAO_OCI P1.6; fontes oficiais lidas em
    # 17/10/2026): AWS, Price List do RDS (db.m6i.large: MySQL 0,171/h, PostgreSQL
    # 0,178/h, 2 vCPU); Azure, Retail Prices (Flexible Server Ddsv5 por vCore: MySQL
    # 0,0855, PostgreSQL 0,089); GCP, preço do Cloud SQL Enterprise (vCPU 0,0413 +
    # 4 GiB x 0,007); OCI, lista da Oracle (MySQL 0,0366 por ECPU com memória; PostgreSQL
    # 0,098 por OCPU = 2 vCPU: a calculadora oficial lista só OCPU e storage, sem
    # memória à parte — oracle.com/cloud/postgresql/pricing, lida em 17/10/2026).
    # `database` vale para fatura sem motor: a maior das duas, premissa declarada.
    ("aws", "database", "vcpu_hour", "0.089"),
    ("azure", "database", "vcpu_hour", "0.089"),
    ("gcp", "database", "vcpu_hour", "0.0693"),
    ("oci", "database", "vcpu_hour", "0.049"),
    ("aws", "database_mysql", "vcpu_hour", "0.0855"),
    ("azure", "database_mysql", "vcpu_hour", "0.0855"),
    ("gcp", "database_mysql", "vcpu_hour", "0.0693"),
    ("oci", "database_mysql", "vcpu_hour", "0.0366"),
    ("aws", "database_postgresql", "vcpu_hour", "0.089"),
    ("azure", "database_postgresql", "vcpu_hour", "0.089"),
    ("gcp", "database_postgresql", "vcpu_hour", "0.0693"),
    ("oci", "database_postgresql", "vcpu_hour", "0.049"),
    # Storage de banco por GB-mês (o resto do custo de banco: storage, IO, backup):
    # RDS gp3 0,115; Azure Flexible Server 0,115; Cloud SQL SSD 0,17 (0,000232877 por
    # GiB-hora); OCI, o maior entre MySQL (0,04) e PostgreSQL (0,072)
    ("aws", "database_storage", "gb_month", "0.115"),
    ("azure", "database_storage", "gb_month", "0.115"),
    ("gcp", "database_storage", "gb_month", "0.17"),
    ("oci", "database_storage", "gb_month", "0.072"),
    # Por motor (PLANO_PRECISAO_OCI B2): só a OCI difere — MySQL 0,04, PostgreSQL 0,072
    *(
        (p, f"database_storage_{m}", "gb_month", v)
        for m in ("mysql", "postgresql")
        for p, v in (("aws", "0.115"), ("azure", "0.115"), ("gcp", "0.17"))
    ),
    ("oci", "database_storage_mysql", "gb_month", "0.04"),
    ("oci", "database_storage_postgresql", "gb_month", "0.072"),
    # Backup de banco por GB-mês: RDS 0,095 (Price List), Azure Flexible Server 0,095
    # (Retail Prices), Cloud SQL 0,08 (0,000109589 por GiB-hora); OCI, backup do MySQL
    # 0,04 e, no PostgreSQL, o Object Storage (0,0255) — lista da Oracle, 17/10/2026
    *(
        (p, c, "gb_month", v)
        for c in ("database_backup", "database_backup_mysql", "database_backup_postgresql")
        for p, v in (("aws", "0.095"), ("azure", "0.095"), ("gcp", "0.08"))
    ),
    ("oci", "database_backup", "gb_month", "0.04"),
    ("oci", "database_backup_mysql", "gb_month", "0.04"),
    ("oci", "database_backup_postgresql", "gb_month", "0.0255"),
    # Cache (Redis) por GB-hora da memória do nó, por faixa de tamanho do nó (B1). AWS:
    # ElastiCache on-demand (t4g.medium, m7g.large, r7g.large, Price List); Azure: nó
    # Standard de Azure Cache for Redis (C2, C3, C4; Retail Prices); GCP: Memorystore
    # Basic (M1, M2, M3); OCI Cache: até 10 GB por nó 0,0194, acima 0,0136. 17/10/2026
    ("aws", "cache_ate_4gb", "gb_hour", "0.02104"),
    ("aws", "cache_de_4_a_10gb", "gb_hour", "0.02476"),
    ("aws", "cache_acima_10gb", "gb_hour", "0.01676"),
    ("azure", "cache_ate_4gb", "gb_hour", "0.0448"),
    ("azure", "cache_de_4_a_10gb", "gb_hour", "0.0375"),
    ("azure", "cache_acima_10gb", "gb_hour", "0.02015"),
    ("gcp", "cache_ate_4gb", "gb_hour", "0.049"),
    ("gcp", "cache_de_4_a_10gb", "gb_hour", "0.027"),
    ("gcp", "cache_acima_10gb", "gb_hour", "0.023"),
    ("oci", "cache_ate_4gb", "gb_hour", "0.0194"),
    ("oci", "cache_de_4_a_10gb", "gb_hour", "0.0194"),
    ("oci", "cache_acima_10gb", "gb_hour", "0.0136"),
    # Rede por tipo (PLANO_PRECO_NAO_COMPUTE), cotada pela quantidade medida. Valores
    # das APIs oficiais em 16/10/2026: AWS `AWSELB`/`AmazonVPC` us-east-1, Azure
    # Retail Prices (balanceador e NAT são globais), catálogo do GCP, OCI cetools.
    # Zero é serviço grátis no destino; provedor sem linha fica sem cotação.
    # Balanceador, por hora: ALB/NLB, Std LB (5 regras), regra mínima, Flexible LB
    ("aws", "load_balancer", "hour", "0.0225"),
    ("azure", "load_balancer", "hour", "0.025"),
    ("gcp", "load_balancer", "hour", "0.025"),
    ("oci", "load_balancer", "hour", "0.0113"),
    # Processamento do balanceador, por GB. LCU-hora como 1 GB (o teto de bytes de
    # uma LCU); na OCI, US$ 0,0001 por Mbps-hora, e 1 Mbps-hora = 0,45 GB
    ("aws", "load_balancer_data", "gb", "0.008"),
    ("azure", "load_balancer_data", "gb", "0.005"),
    ("gcp", "load_balancer_data", "gb", "0.008"),
    ("oci", "load_balancer_data", "gb", "0.0002"),
    # NAT por hora. GCP: teto do Cloud NAT (32 VMs). OCI: sem cobrança
    ("aws", "nat_gateway", "hour", "0.045"),
    ("azure", "nat_gateway", "hour", "0.045"),
    ("gcp", "nat_gateway", "hour", "0.044"),
    ("oci", "nat_gateway", "hour", "0"),
    ("aws", "nat_gateway_data", "gb", "0.045"),
    ("azure", "nat_gateway_data", "gb", "0.045"),
    ("gcp", "nat_gateway_data", "gb", "0.045"),
    ("oci", "nat_gateway_data", "gb", "0"),
    # IP público por hora. OCI: sem cobrança
    ("aws", "public_ip", "hour", "0.005"),
    ("azure", "public_ip", "hour", "0.005"),
    ("gcp", "public_ip", "hour", "0.005"),
    ("oci", "public_ip", "hour", "0"),
    # VPN site a site por conexão-hora (2 túneis). Azure cobra por gateway: sem taxa
    ("aws", "vpn", "hour", "0.05"),
    ("gcp", "vpn", "hour", "0.10"),
    ("oci", "vpn", "hour", "0"),
    # Tráfego entre zonas da mesma região, por GB. OCI: grátis entre ADs
    ("aws", "inter_zone_transfer", "gb", "0.01"),
    ("azure", "inter_zone_transfer", "gb", "0.01"),
    ("gcp", "inter_zone_transfer", "gb", "0.01"),
    ("oci", "inter_zone_transfer", "gb", "0"),
    # R4 (PLANO_PRECO_NAO_COMPUTE §6), conferidos nas APIs oficiais em 16/10/2026: AWS
    # offer files (AmazonEKS, AWSSecretsManager, awskms, AmazonCloudFront), Azure Retail
    # Prices, catálogo do GCP, OCI cetools. Primeira faixa, América do Norte e Europa.
    # Plano de controle por cluster-hora: EKS, GKE, AKS Standard (com SLA), OKE Enhanced
    ("aws", "gke_control_plane", "hour", "0.10"),
    ("azure", "gke_control_plane", "hour", "0.10"),
    ("gcp", "gke_control_plane", "hour", "0.10"),
    ("oci", "gke_control_plane", "hour", "0.10"),
    # Segredo por mês. Azure cobra só a operação; a OCI não cobra segredo
    ("aws", "secrets", "month", "0.40"),
    ("azure", "secrets", "month", "0"),
    ("gcp", "secrets", "month", "0.06"),
    ("oci", "secrets", "month", "0"),
    ("aws", "secrets_operations", "10k", "0.05"),
    ("azure", "secrets_operations", "10k", "0.03"),
    ("gcp", "secrets_operations", "10k", "0.03"),
    ("oci", "secrets_operations", "10k", "0"),
    # Chave por mês, a proteção padrão de cada um: KMS da AWS (HSM), software no GCP e
    # na Azure (Key Vault Standard: só operação), HSM na OCI (20 versões grátis; acima,
    # US$ 0,5334)
    ("aws", "key_management", "month", "1.00"),
    ("azure", "key_management", "month", "0"),
    ("gcp", "key_management", "month", "0.06"),
    # Acima das 20 versões grátis (franquia na cotação, `FRANQUIA_DE_CHAVES`)
    ("oci", "key_management", "month", "0.5334"),
    ("aws", "key_management_operations", "10k", "0.03"),
    ("azure", "key_management_operations", "10k", "0.03"),
    ("gcp", "key_management_operations", "10k", "0.03"),
    ("oci", "key_management_operations", "10k", "0"),
    # CDN: CloudFront, Cloud CDN, Front Door Standard. A OCI não tem CDN própria: sem
    # linha, fica sem cotação
    ("aws", "cdn", "gb", "0.085"),
    ("azure", "cdn", "gb", "0.0825"),
    ("gcp", "cdn", "gb", "0.08"),
    ("aws", "cdn_requests", "10k", "0.01"),
    ("azure", "cdn_requests", "10k", "0.009"),
    ("gcp", "cdn_requests", "10k", "0.0075"),
    # Observabilidade e borda (PLANO_EQUIVALENTES_E_TARIFAS §3), conferidos nas fontes
    # oficiais em 18/10/2026, primeira faixa paga: AWS Price List bulk (AmazonCloudWatch,
    # AmazonRoute53, awswaf, AmazonECR, AmazonPrometheus, us-east-1); Azure Retail Prices
    # (eastus e global); Cloud Billing Catalog (5490-F7B7-8DF6, FA26-5236-B8B5,
    # E505-1604-58F8, 149C-F9EC-3994, 58CD-E7C3-72CA); OCI cetools (B92593, B88525,
    # B94277, B94579, B90925).
    # Ingestão de log por GB: CloudWatch Standard, Log Analytics (Analytics Logs), Cloud
    # Logging. A OCI cobra o armazenamento (GB-mês, 10 GB grátis), não a ingestão: como
    # destino, com a retenção padrão dela (30 dias), 1 GB ingerido é 1 GB-mês (premissa
    # declarada). Como origem, o log da OCI fica ao custo atual (`categoria_por_uso`)
    ("aws", "logs_ingestion", "gb", "0.50"),
    ("azure", "logs_ingestion", "gb", "2.30"),
    ("gcp", "logs_ingestion", "gb", "0.50"),
    ("oci", "logs_ingestion", "gb", "0.05"),
    # Camada básica: CloudWatch Infrequent Access, Basic Logs. A GCP e a OCI não têm
    ("aws", "logs_ingestion_basic", "gb", "0.25"),
    ("azure", "logs_ingestion_basic", "gb", "0.50"),
    ("gcp", "logs_ingestion_basic", "gb", "0.50"),
    ("oci", "logs_ingestion_basic", "gb", "0.05"),
    # Retenção além da incluída, por GB-mês
    ("aws", "logs_retention", "gb_month", "0.03"),
    ("azure", "logs_retention", "gb_month", "0.10"),
    ("gcp", "logs_retention", "gb_month", "0.01"),
    ("oci", "logs_retention", "gb_month", "0.05"),
    # DNS: consulta (US$ 0,40 por milhão; OCI 0,85) e zona-mês (a OCI não cobra zona)
    ("aws", "dns_queries", "10k", "0.004"),
    ("azure", "dns_queries", "10k", "0.004"),
    ("gcp", "dns_queries", "10k", "0.004"),
    ("oci", "dns_queries", "10k", "0.0085"),
    ("aws", "dns_zones", "month", "0.50"),
    ("azure", "dns_zones", "month", "0.50"),
    ("gcp", "dns_zones", "month", "0.20"),
    ("oci", "dns_zones", "month", "0"),
    # WAF: requisição (por milhão: AWS, WAF do Front Door e OCI 0,60; Cloud Armor 0,75),
    # política-mês e regra-mês (a OCI não cobra regra)
    ("aws", "waf_requests", "10k", "0.006"),
    ("azure", "waf_requests", "10k", "0.006"),
    ("gcp", "waf_requests", "10k", "0.0075"),
    ("oci", "waf_requests", "10k", "0.006"),
    ("aws", "waf_policies", "month", "5"),
    ("azure", "waf_policies", "month", "5"),
    ("gcp", "waf_policies", "month", "5"),
    ("oci", "waf_policies", "month", "5"),
    ("aws", "waf_rules", "month", "1"),
    ("azure", "waf_rules", "month", "1"),
    ("gcp", "waf_rules", "month", "1"),
    ("oci", "waf_rules", "month", "0"),
    # Imagem de contêiner por GB-mês. O registro da OCI é grátis: a imagem paga o Object
    # Storage. A taxa diária do ACR fica ao custo atual
    ("aws", "container_registry_storage", "gb_month", "0.10"),
    ("azure", "container_registry_storage", "gb_month", "0.10"),
    ("gcp", "container_registry_storage", "gb_month", "0.10"),
    ("oci", "container_registry_storage", "gb_month", "0.0255"),
    # Amostra de Prometheus gerenciado, por 10 mil (AWS 0,90 e GCP 0,60 por 10 milhões;
    # Azure 0,11). A OCI não tem Prometheus gerenciado: sem linha, fica sem cotação (o
    # ponto do Monitoring dela custa 1/36 da amostra da AWS e não é o mesmo produto)
    ("aws", "metric_samples", "10k", "0.0009"),
    ("azure", "metric_samples", "10k", "0.00011"),
    ("gcp", "metric_samples", "10k", "0.0006"),
)


# Quando cada valor foi conferido na fonte oficial (o commit que o trouxe). É a data
# de captura da referência: o ciclo de vida envelhece o valor a partir dela, não do
# dia em que o seed rodou (PLANO_CICLO_DE_VIDA_DO_PRECO). Valor revisto = data nova.
_CONFERIDO_EM: dict[tuple[str, str], date] = {
    **{(p, c): date(2026, 7, 25) for p in ("aws", "gcp", "azure", "oci")
       for c in ("storage_ssd", "storage_hdd", "network_egress", "database")},
    **{(p, "object_storage"): date(2026, 9, 13) for p in ("aws", "gcp", "azure", "oci")},
    ("gcp", "storage_ssd"): date(2026, 9, 27),
    ("oci", "storage_ssd"): date(2026, 10, 17),
    **{(p, c): date(2026, 10, 17) for p in ("aws", "gcp", "azure", "oci")
       for c in (
           "database", "database_mysql", "database_postgresql", "database_storage",
           "database_storage_mysql", "database_storage_postgresql", "database_backup",
           "database_backup_mysql", "database_backup_postgresql", "cache_ate_4gb",
           "cache_de_4_a_10gb", "cache_acima_10gb",
       )},
    **{(p, c): date(2026, 10, 18) for p in ("aws", "gcp", "azure", "oci")
       for c in (
           "logs_ingestion", "logs_ingestion_basic", "logs_retention", "dns_queries",
           "dns_zones", "waf_requests", "waf_policies", "waf_rules",
           "container_registry_storage", "metric_samples",
       )},
}
_REDE_CONFERIDA_EM = date(2026, 10, 16)


def _conferido_em(provider: str, category: str) -> datetime:
    dia = _CONFERIDO_EM.get((provider, category), _REDE_CONFERIDA_EM)
    return datetime.combine(dia, time(), tzinfo=UTC)


def category_rate_points() -> list[CloudPricePoint]:
    return [
        CloudPricePoint(
            provider=provider,
            sku_key=f"{provider}-{category}-ref",
            service_name=f"{provider} {category}",
            service_type=category,
            region="us-east",
            price=Decimal(rate),
            currency="USD",
            unit=unit,
            vcpus=None,
            ram_gib=None,
            source=_SOURCE,
            confidence="reference",
            captured_at=_conferido_em(provider, category),
        )
        for provider, category, unit, rate in _RATES
    ]
