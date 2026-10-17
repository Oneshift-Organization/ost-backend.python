"""Coleta de preço por região e taxas oficiais de categoria (PLANO_PRECO_REGIONAL R3).

Sem rede. Os recortes têm o formato e os valores das respostas reais de
12/10/2026 (São Paulo e Virgínia/Iowa), reduzidos ao que cada parser lê.
"""

from __future__ import annotations

import importlib.util
import urllib.parse
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


def _com_familia(
    produtos: dict[str, tuple[str | None, str]], faixas: dict[str, list[tuple[str, str]]]
) -> dict[str, Any]:
    """Offer com `productFamily`: (família, UsageType) por SKU."""
    dados = _bulk({sku: {"usagetype": uso} for sku, (_, uso) in produtos.items()}, faixas)
    for sku, (familia, _) in produtos.items():
        if familia is not None:
            dados["products"][sku]["productFamily"] = familia
    return dados


def _elb(prefixo: str, hora: str, lcu: str) -> dict[str, Any]:
    alb = "Load Balancer-Application"
    return _com_familia(
        {
            "h": (alb, f"{prefixo}LoadBalancerUsage"),
            "lcu": (alb, f"{prefixo}LCUUsage"),
            # Iscas: outros produtos com o mesmo sufixo, mais baratos
            "ts": (alb, f"{prefixo}TS-LoadBalancerUsage"),
            "out": (alb, "Outposts-LCUUsage"),
            "res": (alb, f"{prefixo}ReservedLCUUsage"),
            "nlb": ("Load Balancer-Network", f"{prefixo}LoadBalancerUsage"),
        },
        {
            "h": [("0", hora)],
            "lcu": [("0", lcu)],
            "ts": [("0", "0.005")],
            "out": [("0", "0")],
            "res": [("0", "0.001")],
            "nlb": [("0", "0.001")],
        },
    )


def _vpc(prefixo: str) -> dict[str, Any]:
    return _com_familia(
        {
            "ip": (None, f"{prefixo}PublicIPv4:InUseAddress"),
            "vpn": ("Cloud Connectivity", f"{prefixo}VPN-Usage-Hours:ipsec.1"),
        },
        {"ip": [("0", "0.005")], "vpn": [("0", "0.05")]},
    )


def _com_entre_zonas(dados: dict[str, Any], prefixo: str) -> dict[str, Any]:
    dados["products"]["az"] = {"attributes": {"usagetype": f"{prefixo}DataTransfer-Regional-Bytes"}}
    dados["terms"]["OnDemand"]["az"] = {
        "t": {"priceDimensions": {"0": {"beginRange": "0", "pricePerUnit": {"USD": "0.01"}}}}
    }
    return dados


def _por_uso(prefixo: str, regiao: str) -> dict[str, dict[str, Any]]:
    """EKS, Secrets Manager e KMS da região (R4), com iscas de mesmo fim."""
    return {
        "AmazonEKS": _bulk(
            {
                "pc": {"usagetype": f"{prefixo}AmazonEKS-Hours:perCluster"},
                "ext": {"usagetype": f"{prefixo}AmazonEKS-Hours:extendedSupport"},
                "out": {"usagetype": f"{prefixo}AmazonEKS-Local-Outposts-Hours:perCluster"},
            },
            {"pc": [("0", "0.10")], "ext": [("0", "0.50")], "out": [("0", "0.05")]},
        ),
        "AWSSecretsManager": _bulk(
            {
                "s": {"usagetype": f"{prefixo}AWSSecretsManager-Secrets"},
                "api": {"usagetype": f"{prefixo}AWSSecretsManagerAPIRequest"},
            },
            {"s": [("0", "0.40")], "api": [("0", "0.0000050000")]},
        ),
        # O KMS escreve a região por extenso
        "awskms": _bulk(
            {
                "k": {"usagetype": f"{regiao}-KMS-Keys"},
                "r": {"usagetype": f"{regiao}-KMS-Requests"},
                "a": {"usagetype": f"{regiao}-KMS-Requests-Asymmetric"},
            },
            {"k": [("0", "1")], "r": [("0", "0.0000030000")], "a": [("0", "0.0000150000")]},
        ),
    }


# A CloudFront tem um arquivo só, com a borda no prefixo
_CLOUDFRONT = _bulk(
    {
        "us": {"usagetype": "US-DataTransfer-Out-Bytes"},
        "usr": {"usagetype": "US-Requests-Tier2-HTTPS"},
        "sa": {"usagetype": "SA-DataTransfer-Out-Bytes"},
        "sar": {"usagetype": "SA-Requests-Tier2-HTTPS"},
    },
    {
        "us": [("10240", "0.080"), ("0", "0.085")],
        "usr": [("0", "0.0000010000")],
        "sa": [("0", "0.110")],
        "sar": [("0", "0.0000022000")],
    },
)


def test_aws_categorias_por_regiao(monkeypatch: pytest.MonkeyPatch) -> None:
    respostas = {
        crc._AWS_CLOUDFRONT: _CLOUDFRONT,
        **{
            crc._AWS_BULK.format(servico=servico, regiao=regiao): dados
            for prefixo, regiao in (("SAE1-", "sa-east-1"), ("USE1-", "us-east-1"))
            for servico, dados in _por_uso(prefixo, regiao).items()
        },
        crc._AWS_EBS: _EBS,
        crc._AWS_BULK.format(servico="AmazonS3", regiao="sa-east-1"): _s3("SAE1", "0.0405"),
        crc._AWS_BULK.format(servico="AmazonS3", regiao="us-east-1"): _s3("USE1", "0.023"),
        crc._AWS_BULK.format(servico="AWSDataTransfer", regiao="sa-east-1"): _com_entre_zonas(
            _dt(_SP, "0.15"), "SAE1-"
        ),
        crc._AWS_BULK.format(servico="AWSDataTransfer", regiao="us-east-1"): _com_entre_zonas(
            _dt(_VA, "0.09"), ""
        ),
        crc._AWS_BULK.format(servico="AWSELB", regiao="sa-east-1"): _elb("SAE1-", "0.034", "0.011"),
        crc._AWS_BULK.format(servico="AWSELB", regiao="us-east-1"): _elb("", "0.0225", "0.008"),
        crc._AWS_BULK.format(servico="AmazonVPC", regiao="sa-east-1"): _vpc("SAE1-"),
        crc._AWS_BULK.format(servico="AmazonVPC", regiao="us-east-1"): _vpc("USE1-"),
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
        # Rede por tipo: o ALB, não o NLB nem as iscas de mesmo sufixo
        ("aws", "load_balancer", "sa-east-1"): Decimal("0.034"),
        ("aws", "load_balancer_data", "sa-east-1"): Decimal("0.011"),
        ("aws", "public_ip", "sa-east-1"): Decimal("0.005"),
        ("aws", "vpn", "sa-east-1"): Decimal("0.05"),
        ("aws", "inter_zone_transfer", "sa-east-1"): Decimal("0.01"),
        ("aws", "load_balancer", "us-east-1"): Decimal("0.0225"),
        ("aws", "load_balancer_data", "us-east-1"): Decimal("0.008"),
        ("aws", "public_ip", "us-east-1"): Decimal("0.005"),
        ("aws", "vpn", "us-east-1"): Decimal("0.05"),
        ("aws", "inter_zone_transfer", "us-east-1"): Decimal("0.01"),
        # R4: plano de controle, segredos, chaves e CDN; requisição em 10 mil
        ("aws", "gke_control_plane", "sa-east-1"): Decimal("0.10"),
        ("aws", "secrets", "sa-east-1"): Decimal("0.40"),
        ("aws", "secrets_operations", "sa-east-1"): Decimal("0.05"),
        ("aws", "key_management", "sa-east-1"): Decimal("1"),
        ("aws", "key_management_operations", "sa-east-1"): Decimal("0.03"),
        ("aws", "cdn", "sa-east-1"): Decimal("0.110"),
        ("aws", "cdn_requests", "sa-east-1"): Decimal("0.022"),
        ("aws", "gke_control_plane", "us-east-1"): Decimal("0.10"),
        ("aws", "secrets", "us-east-1"): Decimal("0.40"),
        ("aws", "secrets_operations", "us-east-1"): Decimal("0.05"),
        ("aws", "key_management", "us-east-1"): Decimal("1"),
        ("aws", "key_management_operations", "us-east-1"): Decimal("0.03"),
        ("aws", "cdn", "us-east-1"): Decimal("0.085"),
        ("aws", "cdn_requests", "us-east-1"): Decimal("0.01"),
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
        _oci("Storage - Block Volume - Performance Units", 0.0017),
        _oci("Object Storage - Storage", 0, 0.0255),
        _oci("Outbound Data Transfer - Originating in North America, Europe, and UK", 0, 0.0085),
        _oci("Outbound Data Transfer - Originating in APAC, Japan, and South America", 0, 0.025),
    ]
    monkeypatch.setattr(crc, "_get", lambda url, timeout: {"items": itens})
    taxas = _por_chave(crc.fetch_oci_category_rates(["us-ashburn-1", "sa-saopaulo-1"]))
    assert taxas[("oci", "network_egress", "us-ashburn-1")] == Decimal("0.0085")
    assert taxas[("oci", "network_egress", "sa-saopaulo-1")] == Decimal("0.025")
    assert taxas[("oci", "object_storage", "sa-saopaulo-1")] == Decimal("0.0255")
    # Balanced: armazenamento + 10 VPU
    assert taxas[("oci", "storage_ssd", "sa-saopaulo-1")] == Decimal("0.0425")


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


# ── Rede por tipo (PLANO_PRECO_NAO_COMPUTE) ──────────────────────────────────


def test_azure_rede_global_e_regional(monkeypatch: pytest.MonkeyPatch) -> None:
    def _item(servico: str, medidor: str, unidade: str, preco: float) -> dict[str, Any]:
        return {
            "serviceName": servico,
            "meterName": medidor,
            "unitOfMeasure": unidade,
            "retailPrice": preco,
        }

    def _resposta(url: str, timeout: float) -> dict[str, Any]:
        if "Load+Balancer" in url or "Load%20Balancer" in url:
            return {
                "Items": [
                    _item(
                        "Load Balancer",
                        "Standard Included LB Rules and Outbound Rules",
                        "1 Hour",
                        0.025,
                    ),
                    _item(
                        "Load Balancer",
                        "Standard Included LB Rules and Outbound Rules",
                        "1 Hour",
                        0.0,
                    ),
                    _item("Load Balancer", "Standard Data Processed", "1 GB", 0.005),
                ]
            }
        if "NAT+Gateway" in url or "NAT%20Gateway" in url:
            return {
                "Items": [
                    _item("NAT Gateway", "Standard Gateway", "1 Hour", 0.045),
                    _item("NAT Gateway", "Standard Data Processed", "1 GB", 0.045),
                ]
            }
        if "Virtual+Network" in url or "Virtual%20Network" in url:
            return {
                "Items": [
                    _item("Virtual Network", "Standard IPv4 Static Public IP", "1 Hour", 0.005)
                ]
            }
        if "Bandwidth" in url:
            return {
                "Items": [
                    _item(
                        "Bandwidth",
                        "Standard Inter-Availability Zone Data Transfer Out",
                        "1 GB",
                        0.01,
                    )
                ]
            }
        return {"Items": []}

    monkeypatch.setattr(crc, "_get", _resposta)
    rede = crc.fetch_azure_network_global()
    taxas = _por_chave(crc.fetch_azure_category_rates("brazilsouth", rede_global=rede))
    assert taxas == {
        ("azure", "load_balancer", "brazilsouth"): Decimal("0.025"),
        ("azure", "load_balancer_data", "brazilsouth"): Decimal("0.005"),
        ("azure", "nat_gateway", "brazilsouth"): Decimal("0.045"),
        ("azure", "nat_gateway_data", "brazilsouth"): Decimal("0.045"),
        ("azure", "public_ip", "brazilsouth"): Decimal("0.005"),
        ("azure", "inter_zone_transfer", "brazilsouth"): Decimal("0.01"),
    }


def _gcp_sku(descricao: str, regioes: list[str], *precos: float) -> dict[str, Any]:
    return {
        "description": descricao,
        "serviceRegions": regioes,
        "category": {"usageType": "OnDemand"},
        "pricingInfo": [
            {
                "pricingExpression": {
                    "tieredRates": [
                        {"unitPrice": {"units": str(int(p)), "nanos": round((p % 1) * 1e9)}}
                        for p in precos
                    ]
                }
            }
        ],
    }


def test_gcp_rede_da_regiao() -> None:
    rede = [
        _gcp_sku("Cloud Load Balancer Forwarding Rule Minimum Global", ["global"], 0.025),
        _gcp_sku(
            "Global External Application Load Balancer Inbound Data Processing for Sao Paulo",
            ["southamerica-east1"],
            0.012,
        ),
        _gcp_sku("Networking Cloud Nat Data Processing", ["global"], 0.045),
        _gcp_sku("Networking Cloud VPN Tunnel Southamericaeast1", ["southamerica-east1"], 0.075),
    ]
    compute = [
        # Faixa gratuita antes da paga: vale a paga
        _gcp_sku("External IP Charge on a Standard VM", ["global"], 0.0, 0.005),
        # A genérica não cobre São Paulo; a de lá tem nome próprio
        _gcp_sku("Network Inter Zone Data Transfer Out", ["us-central1", "us-east1"], 0.01),
        _gcp_sku("Network Inter Zone Data Transfer Out in Sao Paulo", ["southamerica-east1"], 0.02),
    ]
    taxas = {
        (c, u): p for c, u, _, p in crc._gcp_rede_da_regiao(compute, rede, "southamerica-east1")
    }
    assert taxas == {
        ("load_balancer", "hour"): Decimal("0.025"),
        ("load_balancer_data", "gb"): Decimal("0.012"),
        ("nat_gateway_data", "gb"): Decimal("0.045"),
        # A conexão tem dois túneis
        ("vpn", "hour"): Decimal("0.150"),
        ("public_ip", "hour"): Decimal("0.005"),
        ("inter_zone_transfer", "gb"): Decimal("0.02"),
    }


def test_oci_balanceador_e_banda_convertida(monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [_oci("Load Balancer Base", 0, 0.0113), _oci("Load Balancer Bandwidth", 0, 0.0001)]
    monkeypatch.setattr(crc, "_get", lambda url, timeout: {"items": itens})
    taxas = _por_chave(crc.fetch_oci_category_rates(["sa-saopaulo-1"]))
    assert taxas == {
        ("oci", "load_balancer", "sa-saopaulo-1"): Decimal("0.0113"),
        # US$ 0,0001 por Mbps-hora; 1 Mbps-hora = 0,45 GB
        ("oci", "load_balancer_data", "sa-saopaulo-1"): Decimal("0.00022222"),
    }


# ── R4: plano de controle, segredos, chaves e CDN (PLANO_PRECO_NAO_COMPUTE §6) ─────


def test_gcp_por_uso_da_regiao() -> None:
    por_uso = {
        crc._GCP_KUBERNETES: [
            _gcp_sku("Zonal Kubernetes Clusters", ["global"], 0.1),
            _gcp_sku("Extended Period Kubernetes Clusters", ["global"], 0.5),
        ],
        # Faixa gratuita antes da paga: vale a paga
        crc._GCP_SERVICO_DE_SEGREDOS: [
            _gcp_sku("Secret version replica storage", ["southamerica-east1"], 0.0, 0.06),
            _gcp_sku("Secret access operations", ["southamerica-east1"], 0.0, 0.000003),
        ],
        crc._GCP_KMS: [
            _gcp_sku("Active software symmetric key versions", ["southamerica-east1"], 0.06),
            _gcp_sku(
                "Cryptographic operations with a software symmetric key",
                ["southamerica-east1"],
                0.000003,
            ),
            _gcp_sku("Active HSM symmetric key versions", ["southamerica-east1"], 1.0),
        ],
    }
    rede = [
        _gcp_sku(
            "Networking Cloud CDN Traffic Cache Data Transfer to Latin America", ["global"], 0.09
        ),
        _gcp_sku(
            "Networking Cloud CDN Traffic Cache Data Transfer to North America", ["global"], 0.08
        ),
        _gcp_sku("Networking Cloud Cdn Cache Lookups", ["global"], 0.00000075),
    ]
    taxas = {
        (c, u): p for c, u, _, p in crc._gcp_por_uso_da_regiao(por_uso, rede, "southamerica-east1")
    }
    assert taxas == {
        ("gke_control_plane", "hour"): Decimal("0.1"),
        ("secrets", "month"): Decimal("0.06"),
        ("secrets_operations", "10k"): Decimal("0.03"),
        ("key_management", "month"): Decimal("0.06"),
        ("key_management_operations", "10k"): Decimal("0.03"),
        # Quem a CDN de São Paulo atende está na América Latina
        ("cdn", "gb"): Decimal("0.09"),
        ("cdn_requests", "10k"): Decimal("0.0075"),
    }


def test_gcp_sem_o_servico_fica_na_referencia() -> None:
    taxas = {(c, u): p for c, u, _, p in crc._gcp_por_uso_da_regiao({}, [], "us-central1")}
    assert set(taxas.values()) == {None}


def test_azure_plano_de_controle_operacoes_e_front_door(monkeypatch: pytest.MonkeyPatch) -> None:
    def _item(medidor: str, unidade: str, preco: float, **extra: Any) -> dict[str, Any]:
        return {"meterName": medidor, "unitOfMeasure": unidade, "retailPrice": preco, **extra}

    def _resposta(url: str, timeout: float) -> dict[str, Any]:
        texto = urllib.parse.unquote_plus(url)
        if "Azure Kubernetes Service" in texto:
            itens = [
                _item("Standard Uptime SLA", "1 Hour", 0.1),
                _item("Standard Long Term Support", "1 Hour", 0.6),
            ]
            return {"Items": [{"serviceName": "Azure Kubernetes Service", **i} for i in itens]}
        if "Key Vault" in texto:
            itens = [
                _item("Operations", "10K", 0.03),
                _item("Advanced Key Operations", "10K", 0.15),
            ]
            return {"Items": [{"serviceName": "Key Vault", **i} for i in itens]}
        if "Front Door" in texto and "Zone 3" in texto:
            novo = {"productName": "Azure Front Door", "tierMinimumUnits": 0.0}
            return {
                "Items": [
                    _item("Standard Data Transfer Out", "1 GB", 0.1, **novo),
                    _item("Standard Requests", "10K", 0.009, **novo),
                    # A faixa de volume e o produto clássico não entram
                    _item(
                        "Standard Data Transfer Out",
                        "1 GB",
                        0.05,
                        productName="Azure Front Door",
                        tierMinimumUnits=10000.0,
                    ),
                    _item(
                        "Standard Data Transfer Out",
                        "1 GB",
                        0.25,
                        productName="Azure Front Door Service",
                        tierMinimumUnits=0.0,
                    ),
                ]
            }
        return {"Items": []}

    monkeypatch.setattr(crc, "_get", _resposta)
    taxas = _por_chave(crc.fetch_azure_category_rates("brazilsouth", rede_global=()))
    assert taxas == {
        ("azure", "gke_control_plane", "brazilsouth"): Decimal("0.1"),
        ("azure", "secrets_operations", "brazilsouth"): Decimal("0.03"),
        ("azure", "key_management_operations", "brazilsouth"): Decimal("0.03"),
        ("azure", "cdn", "brazilsouth"): Decimal("0.1"),
        ("azure", "cdn_requests", "brazilsouth"): Decimal("0.009"),
    }


def test_oci_plano_de_controle_enhanced(monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [
        _oci("OCI Kubernetes Engine - Enhanced Cluster", 0.1),
        _oci("OCI Kubernetes Engine - Virtual Node", 0.015),
    ]
    monkeypatch.setattr(crc, "_get", lambda url, timeout: {"items": itens})
    taxas = _por_chave(crc.fetch_oci_category_rates(["sa-saopaulo-1"]))
    assert taxas == {("oci", "gke_control_plane", "sa-saopaulo-1"): Decimal("0.1")}
