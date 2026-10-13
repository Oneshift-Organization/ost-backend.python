"""Coleta de preço por região e taxas oficiais de categoria (PLANO_PRECO_REGIONAL R3).

Sem rede. Os recortes têm o formato e os valores das respostas reais de
12/10/2026 (São Paulo e Virgínia/Iowa), reduzidos ao que cada parser lê.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.infrastructure.pricing import aws_price_client as aws
from app.infrastructure.pricing import azure_price_client as azure
from app.infrastructure.pricing import category_rate_client as crc
from app.infrastructure.pricing import gcp_price_client as gcp
from app.infrastructure.pricing import oci_price_client as oci

_SP = "South America (Sao Paulo)"
_VA = "US East (N. Virginia)"


def _por_chave(pontos: list[Any]) -> dict[tuple[str, str, str], Decimal]:
    return {(p.provider, p.service_type, p.region): p.price for p in pontos}


# ── AWS ──────────────────────────────────────────────────────────────────────


def _bulk(
    produtos: dict[str, dict[str, Any]], faixas: dict[str, list[tuple[str, str]]]
) -> dict[str, Any]:
    return {
        "products": {sku: {"attributes": a} for sku, a in produtos.items()},
        "terms": {
            "OnDemand": {
                sku: {
                    "t": {
                        "priceDimensions": {
                            str(i): {"beginRange": inicio, "pricePerUnit": {"USD": preco}}
                            for i, (inicio, preco) in enumerate(lista)
                        }
                    }
                }
                for sku, lista in faixas.items()
            }
        },
    }


_EBS = {
    "regions": {
        _SP: {
            "Storage General Purpose gp3 GB Mo": {"price": "0.1520000000"},
            "Storage Throughput Optimized HDD st1 GB Mo": {"price": "0.0860000000"},
            "Storage General Purpose gp2 GB Mo": {"price": "0.1900000000"},
        },
        _VA: {
            "Storage General Purpose gp3 GB Mo": {"price": "0.0800000000"},
            "Storage Throughput Optimized HDD st1 GB Mo": {"price": "0.0450000000"},
        },
    }
}


def _s3(regiao: str, preco: str) -> dict[str, Any]:
    return _bulk(
        {
            "a": {
                "storageClass": "General Purpose",
                "volumeType": "Standard",
                "usagetype": f"{regiao}-TimedStorage-ByteHrs",
            },
            "b": {
                "storageClass": "Infrequent Access",
                "volumeType": "Standard - Infrequent Access",
            },
        },
        {
            "a": [("51200", "0.0390000000"), ("0", preco), ("512000", "0.037")],
            "b": [("0", "0.0125")],
        },
    )


def _dt(rotulo: str, preco: str) -> dict[str, Any]:
    return _bulk(
        {
            "out": {
                "transferType": "AWS Outbound",
                "toLocation": "External",
                "fromLocation": rotulo,
            },
            "global": {
                "transferType": "AWS Outbound",
                "toLocation": "External",
                "fromLocation": "Global",
            },
            "inter": {
                "transferType": "InterRegion Outbound",
                "toLocation": "External",
                "fromLocation": rotulo,
            },
        },
        {
            "out": [("10240", "0.138"), ("0", preco)],
            "global": [("0", "0")],
            "inter": [("0", "0.02")],
        },
    )


def test_aws_categorias_por_regiao(monkeypatch: pytest.MonkeyPatch) -> None:
    respostas = {
        crc._AWS_EBS: _EBS,
        crc._AWS_BULK.format(servico="AmazonS3", regiao="sa-east-1"): _s3("SAE1", "0.0405"),
        crc._AWS_BULK.format(servico="AmazonS3", regiao="us-east-1"): _s3("USE1", "0.023"),
        crc._AWS_BULK.format(servico="AWSDataTransfer", regiao="sa-east-1"): _dt(_SP, "0.15"),
        crc._AWS_BULK.format(servico="AWSDataTransfer", regiao="us-east-1"): _dt(_VA, "0.09"),
    }
    monkeypatch.setattr(aws, "get_json", lambda url, timeout: respostas[url])

    taxas = _por_chave(crc.fetch_aws_category_rates(["sa-east-1", "us-east-1"]))

    assert taxas == {
        ("aws", "storage_ssd", "sa-east-1"): Decimal("0.1520000000"),
        ("aws", "storage_hdd", "sa-east-1"): Decimal("0.0860000000"),
        ("aws", "object_storage", "sa-east-1"): Decimal("0.0405"),
        ("aws", "network_egress", "sa-east-1"): Decimal("0.15"),
        ("aws", "storage_ssd", "us-east-1"): Decimal("0.0800000000"),
        ("aws", "storage_hdd", "us-east-1"): Decimal("0.0450000000"),
        ("aws", "object_storage", "us-east-1"): Decimal("0.023"),
        ("aws", "network_egress", "us-east-1"): Decimal("0.09"),
    }


def test_aws_regiao_nova_tem_rotulo() -> None:
    assert aws.rotulo_da_regiao("eu-west-2") == "EU (London)"
    with pytest.raises(ValueError, match="não mapeada"):
        aws.rotulo_da_regiao("marte-1")


# ── GCP: compute ─────────────────────────────────────────────────────────────


def _sku(
    desc: str, grupo: str, regioes: list[str], *precos: str, familia: str = "Compute"
) -> dict[str, Any]:
    def _preco(v: str) -> dict[str, Any]:
        unidades, _, frac = v.partition(".")
        return {"units": unidades, "nanos": int((frac + "000000000")[:9])}

    return {
        "description": desc,
        "serviceRegions": regioes,
        "category": {"resourceFamily": familia, "resourceGroup": grupo, "usageType": "OnDemand"},
        "pricingInfo": [
            {
                "pricingExpression": {
                    "tieredRates": [{"unitPrice": _preco(p)} for p in precos],
                }
            }
        ],
    }


_AMERICAS = ["us-central1", "us-east1", "us-west1"]
_SKUS_COMPUTE = [
    _sku("E2 Instance Core running in Americas", "CPU", _AMERICAS, "0.02181159"),
    _sku("E2 Instance Ram running in Americas", "RAM", _AMERICAS, "0.00292353"),
    # Custom é mais caro e vinha depois: o prefixo solto deixava ele vencer.
    _sku("E2 Custom Instance Core running in Americas", "CPU", _AMERICAS, "0.0229"),
    _sku("E2 Instance Core running in Sao Paulo", "CPU", ["southamerica-east1"], "0.0346242"),
    _sku("E2 Instance Ram running in Sao Paulo", "RAM", ["southamerica-east1"], "0.00464025"),
    _sku("T2A Arm Instance Core running in Americas", "CPU", ["us-central1"], "0.0249"),
    _sku("T2A Arm Instance Ram running in Americas", "RAM", ["us-central1"], "0.0034"),
    _sku("N2 Sole Tenancy Instance Core running in Americas", "CPU", _AMERICAS, "0.9"),
    _sku("Balanced PD Capacity", "SSD", [*_AMERICAS, "us-east4"], "0.1", familia="Storage"),
    _sku(
        "Balanced PD Capacity in Sao Paulo",
        "SSD",
        ["southamerica-east1"],
        "0.15",
        familia="Storage",
    ),
    _sku("Storage PD Capacity", "PDStandard", _AMERICAS, "0", "0.04", familia="Storage"),
    _sku(
        "Storage PD Capacity in Sao Paulo",
        "PDStandard",
        ["southamerica-east1"],
        "0.06",
        familia="Storage",
    ),
    _sku(
        "Network Internet Data Transfer Out from Americas to Americas",
        "PremiumInternetEgress",
        _AMERICAS,
        "0",
        "0.12",
        familia="Network",
    ),
    _sku(
        "Network Internet Data Transfer Out from Americas to South America",
        "PremiumInternetEgress",
        _AMERICAS,
        "0",
        "0.19",
        familia="Network",
    ),
    _sku(
        "Network Internet Data Transfer Out from Sao Paulo to South America",
        "PremiumInternetEgress",
        ["southamerica-east1"],
        "0.19",
        familia="Network",
    ),
]
_SKUS_STORAGE = [
    _sku(
        "Standard Storage US Regional", "RegionalStorage", _AMERICAS, "0", "0.02", familia="Storage"
    ),
    _sku(
        "Standard Storage Iowa Dual-region",
        "MultiRegionalStorage",
        ["us-central1"],
        "0.022",
        familia="Storage",
    ),
    _sku(
        "Standard Storage Sao Paulo",
        "RegionalStorage",
        ["southamerica-east1"],
        "0.035",
        familia="Storage",
    ),
]


@pytest.fixture
def _catalogo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resposta(url: str, *, timeout: float) -> dict[str, Any]:
        if gcp.STORAGE_SERVICE in url:
            return {"skus": _SKUS_STORAGE}
        return {"skus": _SKUS_COMPUTE}

    monkeypatch.setattr(gcp, "_get_json", _resposta)


def _maquinas(regiao: str) -> dict[str, Any]:
    pontos = gcp.fetch_gcp_compute_prices(
        api_key="k", regioes=["us-central1", "southamerica-east1"]
    )
    return {p.sku_key: p for p in pontos if p.region == regiao}


@pytest.mark.usefixtures("_catalogo")
def test_gcp_so_gera_maquina_que_existe() -> None:
    iowa = _maquinas("us-central1")
    assert "e2-standard-32" in iowa
    assert "e2-standard-64" not in iowa, "o E2 vai até 32 vCPU"
    assert "e2-highmem-16" in iowa and "e2-highmem-32" not in iowa
    assert iowa["e2-highmem-8"].ram_gib == Decimal("64")
    assert iowa["e2-highcpu-8"].ram_gib == Decimal("8")


@pytest.mark.usefixtures("_catalogo")
def test_gcp_preco_da_taxa_base_nao_da_custom() -> None:
    e2 = _maquinas("us-central1")["e2-standard-2"]
    assert e2.price == Decimal("0.02181159") * 2 + Decimal("0.00292353") * 8


@pytest.mark.usefixtures("_catalogo")
def test_gcp_cada_regiao_com_o_proprio_preco() -> None:
    iowa = _maquinas("us-central1")["e2-standard-4"]
    sp = _maquinas("southamerica-east1")["e2-standard-4"]
    assert sp.price / iowa.price > Decimal("1.5")


@pytest.mark.usefixtures("_catalogo")
def test_gcp_familia_ausente_na_regiao_nao_gera_maquina() -> None:
    assert "t2a-standard-4" in _maquinas("us-central1")
    assert not [k for k in _maquinas("southamerica-east1") if k.startswith("t2a")]


def test_gcp_sem_chave_nao_busca() -> None:
    assert gcp.fetch_gcp_compute_prices(api_key="", regioes=["us-central1"]) == []
    assert crc.fetch_gcp_category_rates(api_key="", regioes=["us-central1"]) == []


# ── GCP: categorias ──────────────────────────────────────────────────────────


@pytest.mark.usefixtures("_catalogo")
def test_gcp_categorias_por_regiao() -> None:
    taxas = _por_chave(
        crc.fetch_gcp_category_rates(api_key="k", regioes=["us-central1", "southamerica-east1"])
    )
    assert taxas == {
        ("gcp", "storage_ssd", "us-central1"): Decimal("0.1"),
        ("gcp", "storage_hdd", "us-central1"): Decimal("0.04"),
        ("gcp", "object_storage", "us-central1"): Decimal("0.02"),
        ("gcp", "network_egress", "us-central1"): Decimal("0.12"),
        ("gcp", "storage_ssd", "southamerica-east1"): Decimal("0.15"),
        ("gcp", "storage_hdd", "southamerica-east1"): Decimal("0.06"),
        ("gcp", "object_storage", "southamerica-east1"): Decimal("0.035"),
        ("gcp", "network_egress", "southamerica-east1"): Decimal("0.19"),
    }


# ── Azure ────────────────────────────────────────────────────────────────────


def test_azure_blob_e_egress_com_regiao(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resposta(url: str, timeout: float) -> dict[str, Any]:
        if "Bandwidth" in url:
            return {
                "Items": [
                    {
                        "meterName": "Standard Data Transfer Out",
                        "unitOfMeasure": "1 GB",
                        "retailPrice": 0.181,
                    },
                    {
                        "meterName": "Standard Data Transfer Out",
                        "unitOfMeasure": "1 GB",
                        "retailPrice": 0.0,
                    },
                ]
            }
        return {
            "Items": [
                {
                    "productName": "Files v2",
                    "unitOfMeasure": "1 GB/Month",
                    "tierMinimumUnits": 0.0,
                    "retailPrice": 0.0489,
                },
                {
                    "productName": "General Block Blob v2",
                    "unitOfMeasure": "1 GB/Month",
                    "tierMinimumUnits": 51200.0,
                    "retailPrice": 0.03146,
                },
                {
                    "productName": "General Block Blob v2",
                    "unitOfMeasure": "1 GB/Month",
                    "tierMinimumUnits": 0.0,
                    "retailPrice": 0.0326,
                },
            ]
        }

    monkeypatch.setattr(crc, "_get", _resposta)
    taxas = _por_chave(crc.fetch_azure_category_rates("brazilsouth"))
    assert taxas == {
        ("azure", "network_egress", "brazilsouth"): Decimal("0.181"),
        ("azure", "object_storage", "brazilsouth"): Decimal("0.0326"),
    }


# ── OCI ──────────────────────────────────────────────────────────────────────


def _oci(nome: str, *faixas: float, metrica: str = "") -> dict[str, Any]:
    return {
        "displayName": nome,
        "metricName": metrica,
        "currencyCodeLocalizations": [{"prices": [{"value": v} for v in faixas]}],
    }


def test_oci_egress_depende_do_continente(monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [
        _oci("Storage - Block Volume - Storage", 0.0255),
        _oci("Object Storage - Storage", 0, 0.0255),
        _oci("Outbound Data Transfer - Originating in North America, Europe, and UK", 0, 0.0085),
        _oci("Outbound Data Transfer - Originating in APAC, Japan, and South America", 0, 0.025),
    ]
    monkeypatch.setattr(crc, "_get", lambda url, timeout: {"items": itens})
    taxas = _por_chave(crc.fetch_oci_category_rates(["us-ashburn-1", "sa-saopaulo-1"]))
    assert taxas[("oci", "network_egress", "us-ashburn-1")] == Decimal("0.0085")
    assert taxas[("oci", "network_egress", "sa-saopaulo-1")] == Decimal("0.025")
    assert taxas[("oci", "object_storage", "sa-saopaulo-1")] == Decimal("0.0255")
    assert taxas[("oci", "storage_ssd", "sa-saopaulo-1")] == Decimal("0.0255")


def test_oci_compute_gravado_em_cada_regiao(monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [
        _oci("Compute - Standard - E5 - OCPU", 0.03, metrica="OCPU Per Hour"),
        _oci("Compute - Standard - E5 - Memory", 0.002, metrica="Gigabytes Per Hour"),
    ]
    monkeypatch.setattr(oci, "_fetch", lambda timeout: itens)
    pontos = oci.fetch_oci_compute_prices(regioes=["us-ashburn-1", "sa-saopaulo-1"])
    por_regiao = {
        r: {p.sku_key: p.price for p in pontos if p.region == r}
        for r in ("us-ashburn-1", "sa-saopaulo-1")
    }
    assert por_regiao["us-ashburn-1"] == por_regiao["sa-saopaulo-1"]
    # 4 GB por OCPU: 2 GB/vCPU no x86, a classe de compute.
    assert por_regiao["us-ashburn-1"]["VM.Standard.E5.Flex.8c32g"] == Decimal("0.304")


# ── Script de sync ───────────────────────────────────────────────────────────


def test_sync_pede_uma_regiao_por_zona_sem_repetir() -> None:
    caminho = Path(__file__).parents[4] / "scripts" / "sync_prices.py"
    spec = importlib.util.spec_from_file_location("sync_prices", caminho)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    regioes = modulo.regioes_por_provedor(["sao-paulo", "meio-oeste", "texas"])
    assert regioes["aws"] == ["sa-east-1", "us-east-2"], "texas usa a vizinha us-east-2"
    assert regioes["gcp"] == ["southamerica-east1", "us-central1", "us-south1"]
    assert regioes["oci"] == ["sa-saopaulo-1", "us-chicago-1"]


# ── Azure: computação confidencial fora ──────────────────────────────────────


@pytest.mark.parametrize(
    ("arm", "serie"),
    [
        ("Standard_DC2s_v3", "dc"),
        ("Standard_EC4as_v5", "ec"),
        ("Standard_D4s_v5", "d"),
        ("Standard_DS2_v2", "ds"),
        ("Standard_B2pts_v2", "b"),
    ],
)
def test_serie_da_azure(arm: str, serie: str) -> None:
    assert azure._serie(arm) == serie
    assert (serie in azure._CONFIDENCIAIS) is (serie in {"dc", "ec"})
