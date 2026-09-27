"""Taxas de referência por categoria não-compute (Wave 4).

Preços on-demand publicados (~2026), US-East, por unidade natural da categoria.
São dados de REFERÊNCIA (``confidence="reference"``) com proveniência — melhores
que um balde opaco, honestamente rotulados como não-oficiais. Um fetch das APIs
(Azure/OCI) pode fazer upgrade de linhas específicas para ``official``.

Reusa ``cloud_price_points``: ``service_type`` = categoria, ``unit`` = gb_month/gb.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    ("oci", "storage_ssd", "gb_month", "0.0255"),    # block balanced
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
    ("oci", "storage_hdd", "gb_month", "0.0255"),    # block (mesma tier)
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
)


def category_rate_points() -> list[CloudPricePoint]:
    now = datetime.now(UTC)
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
            captured_at=now,
        )
        for provider, category, unit, rate in _RATES
    ]
