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
    # Índice de managed relational DB ($/vCPU-mês, general-purpose single-AZ). O
    # custo de DB inclui instância+storage+IO; reprecificar o total pela razão dos
    # índices é uma 1ª aproximação direcional (compute domina o custo de managed DB).
    ("aws", "database", "db_vcpu_month", "62"),      # RDS m6g
    ("gcp", "database", "db_vcpu_month", "60"),      # Cloud SQL
    ("azure", "database", "db_vcpu_month", "73"),    # Azure SQL / MySQL Flex
    ("oci", "database", "db_vcpu_month", "30"),      # OCI DB/MySQL (mais barato)
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
    ("oci", "key_management", "month", "0"),
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
)


# Quando cada valor foi conferido na fonte oficial (o commit que o trouxe). É a data
# de captura da referência: o ciclo de vida envelhece o valor a partir dela, não do
# dia em que o seed rodou (PLANO_CICLO_DE_VIDA_DO_PRECO). Valor revisto = data nova.
_CONFERIDO_EM: dict[tuple[str, str], date] = {
    **{(p, c): date(2026, 7, 25) for p in ("aws", "gcp", "azure", "oci")
       for c in ("storage_ssd", "storage_hdd", "network_egress", "database")},
    **{(p, "object_storage"): date(2026, 9, 13) for p in ("aws", "gcp", "azure", "oci")},
    ("gcp", "storage_ssd"): date(2026, 9, 27),
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
