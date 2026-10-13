"""Escolha da taxa de categoria por região (PLANO_PRECO_REGIONAL D7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.use_cases.analysis.build_migration_context import _taxas_por_provedor

_AGORA = datetime.now(UTC)


def _taxa(
    provider: str,
    region: str,
    price: str,
    *,
    tipo: str = "storage_ssd",
    confidence: str = "official",
    idade_dias: int = 0,
) -> CloudPricePoint:
    return CloudPricePoint(
        provider=provider,
        sku_key=f"{provider}-{tipo}-{region}-{confidence}",
        service_name="taxa",
        service_type=tipo,
        region=region,
        price=Decimal(price),
        currency="USD",
        unit="gb_month",
        source="api" if confidence == "official" else "seed",
        confidence=confidence,
        captured_at=_AGORA - timedelta(days=idade_dias),
    )


def test_origem_usa_a_propria_regiao_ou_a_canonica_da_zona() -> None:
    taxas = _taxas_por_provedor(
        [_taxa("gcp", "us-central1", "0.10"), _taxa("gcp", "southamerica-east1", "0.15")],
        "storage_ssd",
        source_provider="gcp",
        zona="meio-oeste",
        regiao_origem="americas",
    )
    gcp = taxas["gcp"]
    assert (gcp.rate, gcp.region, gcp.approximate) == (Decimal("0.10"), "us-central1", False)


def test_destino_na_zona_da_carga() -> None:
    taxas = _taxas_por_provedor(
        [
            _taxa("aws", "us-east-1", "0.08"),
            _taxa("aws", "sa-east-1", "0.152"),
            _taxa("gcp", "southamerica-east1", "0.15"),
        ],
        "storage_ssd",
        source_provider="gcp",
        zona="sao-paulo",
        regiao_origem="southamerica-east1",
    )
    assert taxas["aws"].rate == Decimal("0.152")
    assert taxas["aws"].confidence == "official"


def test_sem_a_regiao_aproxima_e_rebaixa() -> None:
    taxas = _taxas_por_provedor(
        [
            _taxa("azure", "eastus", "0.088"),
            _taxa("azure", "us-east", "0.09", confidence="reference"),
            _taxa("gcp", "southamerica-east1", "0.15"),
        ],
        "storage_ssd",
        source_provider="gcp",
        zona="sao-paulo",
        regiao_origem="southamerica-east1",
    )
    azure = taxas["azure"]
    assert (azure.region, azure.approximate) == ("eastus", True)
    assert azure.confidence == "reference"


def test_oficial_vence_referencia_na_mesma_regiao() -> None:
    taxas = _taxas_por_provedor(
        [
            _taxa("aws", "us-east", "0.080", confidence="reference"),
            _taxa("aws", "us-east", "0.081", idade_dias=30),
            _taxa("aws", "us-east", "0.079", idade_dias=1),
        ],
        "storage_ssd",
        source_provider="gcp",
        zona="virginia",
        regiao_origem=None,
    )
    assert taxas["aws"].rate == Decimal("0.079"), "oficial e mais recente"


def test_armazenamento_da_oci_e_global_mas_egress_nao() -> None:
    pontos = [
        _taxa("oci", "us-ashburn-1", "0.0255"),
        _taxa("oci", "us-ashburn-1", "0.0085", tipo="network_egress"),
    ]
    disco = _taxas_por_provedor(
        pontos, "storage_ssd", source_provider="gcp", zona="sao-paulo", regiao_origem=None
    )["oci"]
    egress = _taxas_por_provedor(
        [p for p in pontos if p.service_type == "network_egress"],
        "network_egress",
        source_provider="gcp",
        zona="sao-paulo",
        regiao_origem=None,
    )["oci"]
    assert (disco.approximate, disco.confidence) == (False, "official")
    assert (egress.approximate, egress.confidence) == (True, "reference")


def test_moeda_diferente_fica_de_fora() -> None:
    taxas = _taxas_por_provedor(
        [
            CloudPricePoint(
                provider="aws",
                sku_key="x",
                service_name="t",
                service_type="storage_ssd",
                region="sa-east-1",
                price=Decimal("0.5"),
                currency="BRL",
                unit="gb_month",
            )
        ],
        "storage_ssd",
        source_provider="gcp",
        zona="sao-paulo",
        regiao_origem=None,
    )
    assert taxas == {}
