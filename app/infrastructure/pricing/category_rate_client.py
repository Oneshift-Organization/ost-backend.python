"""Taxas de categoria (disco, objeto, egress) das APIs oficiais, por região.

Sobe as taxas de `reference` (digitadas em `seed_category_rates`, região
fictícia `us-east`) para `official`, com a região de verdade. O motivo de ser
por região: em São Paulo o gp3 custa 1,9x a Virgínia e o S3, 1,8x.

| provedor | SSD | HDD | objeto | egress |
|---|---|---|---|---|
| AWS | gp3 (`ebs.json`) | st1 | S3 Standard (Bulk) | internet (Bulk) |
| GCP | pd-balanced | pd-standard | Standard regional | Premium, mesmo continente |
| Azure | — | — | Blob Hot LRS | Standard Data Transfer Out |
| OCI | block volume | block volume | Object Storage | por continente de origem |

Disco da Azure é por tamanho de disco (P10, P20…), não por GB: segue
`reference`. Banco gerenciado segue `reference` nos quatro.

Rede por tipo (PLANO_PRECO_NAO_COMPUTE): balanceador, processamento do balanceador,
NAT, IP público, VPN e tráfego entre zonas, nas fontes acima e no serviço Networking
do GCP. Fica em `reference`: o NAT da AWS (o offer do EC2 passa de 480 MB por região),
o NAT por hora do GCP (o catálogo cobra por VM; a referência usa o teto do gateway),
a VPN da Azure (cobra por gateway) e os serviços grátis da OCI (sem SKU no cetools).

Em todas, a **primeira faixa paga**: a que a maior parte dos custos encontra
primeiro, e a mesma regra para os quatro (a faixa gratuita de 100 GB da AWS,
de 200 GB do GCP e de 10 TB da OCI fica de fora de todos). urllib da stdlib.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.infrastructure.pricing import aws_price_client as aws
from app.infrastructure.pricing import azure_price_client as azure
from app.infrastructure.pricing import gcp_price_client as gcp

_AZURE = "https://prices.azure.com/api/retail/prices"
_OCI = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD"
_AWS_EBS = "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ebs.json"
_AWS_BULK = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{servico}/current/{regiao}/index.json"
)
# A CloudFront tem um arquivo só: o preço é da borda que atende, não da região
_AWS_CLOUDFRONT = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudFront/current/index.json"
)

_MBPS_HORA_EM_GB = Decimal("0.45")

_AWS_EBS_TIPOS = {
    "Storage General Purpose gp3 GB Mo": ("storage_ssd", "gp3"),
    "Storage Throughput Optimized HDD st1 GB Mo": ("storage_hdd", "st1"),
}


def _get(url: str, timeout: float) -> dict[str, Any]:
    """GET com espera em 429 e queda de conexão, como a coleta de compute da Azure.

    Sem isso o `categories-live` caía no primeiro 429: a Retail Prices API limita cedo,
    e cada região pede rede, plano de controle, Key Vault e Front Door."""
    return azure.get_json_com_espera(url, timeout=timeout)


def _point(
    provider: str,
    category: str,
    unit: str,
    rate: Decimal,
    source: str,
    *,
    region: str,
    detalhe: str = "live",
) -> CloudPricePoint:
    return CloudPricePoint(
        provider=provider,
        sku_key=f"{provider}-{category}-{detalhe}",
        service_name=f"{provider} {category} ({detalhe})",
        service_type=category,
        region=region,
        price=rate,
        currency="USD",
        unit=unit,
        vcpus=None,
        ram_gib=None,
        source=source,
        confidence="official",
        captured_at=datetime.now(UTC),
    )


# ── AWS ──────────────────────────────────────────────────────────────────────


def _aws_primeira_faixa_paga(
    dados: dict[str, Any], filtro: Callable[[dict[str, Any]], bool]
) -> Decimal | None:
    """Preço da faixa paga de menor início entre os produtos que passam no filtro."""
    termos = (dados.get("terms") or {}).get("OnDemand") or {}
    faixas: list[tuple[float, Decimal]] = []
    for sku, produto in (dados.get("products") or {}).items():
        if not filtro(produto.get("attributes") or {}):
            continue
        for termo in (termos.get(sku) or {}).values():
            for dim in (termo.get("priceDimensions") or {}).values():
                preco = aws._decimal((dim.get("pricePerUnit") or {}).get("USD"))
                if preco is not None:
                    faixas.append((float(dim.get("beginRange") or 0), preco))
    return min(faixas)[1] if faixas else None


def _aws_s3_standard(dados: dict[str, Any]) -> Decimal | None:
    return _aws_primeira_faixa_paga(
        dados,
        lambda a: (
            a.get("storageClass") == "General Purpose"
            and a.get("volumeType") == "Standard"
            and str(a.get("usagetype", "")).endswith("TimedStorage-ByteHrs")
        ),
    )


def _aws_egress(dados: dict[str, Any], rotulo: str) -> Decimal | None:
    return _aws_primeira_faixa_paga(
        dados,
        lambda a: (
            a.get("transferType") == "AWS Outbound"
            and a.get("toLocation") == "External"
            and a.get("fromLocation") == rotulo
        ),
    )


# (offer, UsageType sem o prefixo da região, família, categoria, unidade, detalhe)
_AWS_REDE = (
    ("AWSELB", "LoadBalancerUsage", "Load Balancer-Application", "load_balancer", "hour", "alb"),
    # LCU-hora vale 1 GB: o teto de bytes processados de uma LCU
    ("AWSELB", "LCUUsage", "Load Balancer-Application", "load_balancer_data", "gb", "alb-lcu"),
    ("AmazonVPC", "PublicIPv4:InUseAddress", None, "public_ip", "hour", "ipv4"),
    ("AmazonVPC", "VPN-Usage-Hours:ipsec.1", None, "vpn", "hour", "site-to-site"),
)
# Prefixo de região do UsageType (`SAE1-`, `EU-`); us-east-1 vem sem prefixo.
# `Outposts-`, `TS-` e `Reserved` são outros produtos e ficam de fora.
_AWS_PREFIXO_DA_REGIAO = r"(?:(?:[A-Z]{2,3}\d|EU)-)?"


def _aws_preco_do_uso(dados: dict[str, Any], uso: str, familia: str | None) -> Decimal | None:
    """Primeira faixa paga do UsageType na região do offer."""
    padrao = re.compile(_AWS_PREFIXO_DA_REGIAO + re.escape(uso) + "$")
    termos = (dados.get("terms") or {}).get("OnDemand") or {}
    faixas: list[tuple[float, Decimal]] = []
    for sku, produto in (dados.get("products") or {}).items():
        if familia is not None and produto.get("productFamily") != familia:
            continue
        if not padrao.match(str((produto.get("attributes") or {}).get("usagetype", ""))):
            continue
        for termo in (termos.get(sku) or {}).values():
            for dim in (termo.get("priceDimensions") or {}).values():
                preco = aws._decimal((dim.get("pricePerUnit") or {}).get("USD"))
                if preco is not None and preco > 0:
                    faixas.append((float(dim.get("beginRange") or 0), preco))
    return min(faixas)[1] if faixas else None


# R4 (PLANO_PRECO_NAO_COMPUTE §6): (offer, fim do UsageType, categoria, unidade, detalhe,
# escala). O KMS escreve a região por extenso (`us-east-1-KMS-Keys`), então casa pelo fim
# do UsageType; o arquivo já é da região. Requisição tem preço unitário: a escala leva a
# 10 mil, a unidade da categoria.
_AWS_POR_USO = (
    ("AmazonEKS", "AmazonEKS-Hours:perCluster", "gke_control_plane", "hour", "eks", 1),
    ("AWSSecretsManager", "AWSSecretsManager-Secrets", "secrets", "month", "secret", 1),
    (
        "AWSSecretsManager",
        "AWSSecretsManagerAPIRequest",
        "secrets_operations",
        "10k",
        "api-request",
        10_000,
    ),
    ("awskms", "KMS-Keys", "key_management", "month", "kms-key", 1),
    ("awskms", "KMS-Requests", "key_management_operations", "10k", "kms-request", 10_000),
)


def _aws_preco_pelo_fim(dados: dict[str, Any], fim: str) -> Decimal | None:
    """Primeira faixa paga do UsageType que termina em `fim`, com ou sem prefixo."""
    padrao = re.compile(r"(?:.*-)?" + re.escape(fim) + "$")
    termos = (dados.get("terms") or {}).get("OnDemand") or {}
    faixas: list[tuple[float, Decimal]] = []
    for sku, produto in (dados.get("products") or {}).items():
        if not padrao.match(str((produto.get("attributes") or {}).get("usagetype", ""))):
            continue
        for termo in (termos.get(sku) or {}).values():
            for dim in (termo.get("priceDimensions") or {}).values():
                preco = aws._decimal((dim.get("pricePerUnit") or {}).get("USD"))
                if preco is not None and preco > 0:
                    faixas.append((float(dim.get("beginRange") or 0), preco))
    return min(faixas)[1] if faixas else None


def _aws_por_uso_da_regiao(regiao: str, timeout: float) -> list[CloudPricePoint]:
    offers = {
        servico: aws.get_json(_AWS_BULK.format(servico=servico, regiao=regiao), timeout=timeout)
        for servico in sorted({o for o, *_ in _AWS_POR_USO})
    }
    out: list[CloudPricePoint] = []
    for offer, fim, categoria, unidade, detalhe, escala in _AWS_POR_USO:
        preco = _aws_preco_pelo_fim(offers[offer], fim)
        if preco is not None:
            out.append(
                _point(
                    "aws",
                    categoria,
                    unidade,
                    preco * escala,
                    "aws_price_list_bulk",
                    region=regiao,
                    detalhe=detalhe,
                )
            )
    return out


def _aws_borda_da_cloudfront(regiao: str) -> str:
    """Prefixo da borda da CloudFront que atende quem está perto da região."""
    for prefixo, borda in (
        ("sa-", "SA"),
        ("eu-", "EU"),
        ("ca-", "CA"),
        ("ap-northeast-1", "JP"),
        ("ap-south-", "IN"),
        ("ap-southeast-2", "AU"),
        ("ap-", "AP"),
        ("af-", "ZA"),
        ("me-", "ME"),
        ("il-", "ME"),
    ):
        if regiao.startswith(prefixo):
            return borda
    return "US"


def _aws_cdn_da_regiao(cloudfront: dict[str, Any], regiao: str) -> list[CloudPricePoint]:
    borda = _aws_borda_da_cloudfront(regiao)
    precos = (
        ("cdn", "gb", _aws_preco_do_uso(cloudfront, f"{borda}-DataTransfer-Out-Bytes", None), 1),
        (
            "cdn_requests",
            "10k",
            _aws_preco_do_uso(cloudfront, f"{borda}-Requests-Tier2-HTTPS", None),
            10_000,
        ),
    )
    return [
        _point(
            "aws",
            categoria,
            unidade,
            preco * escala,
            "aws_price_list_bulk",
            region=regiao,
            detalhe=f"cloudfront-{borda.lower()}",
        )
        for categoria, unidade, preco, escala in precos
        if preco is not None
    ]


def _aws_rede_da_regiao(
    regiao: str, transferencia: dict[str, Any], timeout: float
) -> list[CloudPricePoint]:
    offers = {
        servico: aws.get_json(_AWS_BULK.format(servico=servico, regiao=regiao), timeout=timeout)
        for servico in sorted({o for o, *_ in _AWS_REDE})
    }
    precos = [
        (categoria, unidade, detalhe, _aws_preco_do_uso(offers[offer], uso, familia))
        for offer, uso, familia, categoria, unidade, detalhe in _AWS_REDE
    ]
    precos.append(
        (
            "inter_zone_transfer",
            "gb",
            "regional",
            _aws_preco_do_uso(transferencia, "DataTransfer-Regional-Bytes", None),
        )
    )
    return [
        _point("aws", categoria, unidade, preco, "aws_price_list_bulk", region=regiao, detalhe=d)
        for categoria, unidade, d, preco in precos
        if preco is not None
    ]


def fetch_aws_category_rates(
    regioes: Sequence[str], *, timeout: float = 60.0
) -> list[CloudPricePoint]:
    """gp3, st1, S3 Standard e egress de internet, por região.

    O EBS vem da mesma família de endpoint do compute (calculadora, um arquivo
    para todas as regiões). S3 e transferência vêm da Price List Bulk API por
    região — o arquivo do EC2 inteiro passa de 290 MB, o do S3 tem 500 KB.
    """
    ebs = aws.get_json(_AWS_EBS, timeout=timeout)
    cloudfront = aws.get_json(_AWS_CLOUDFRONT, timeout=timeout)
    out: list[CloudPricePoint] = []
    for regiao in regioes:
        rotulo = aws.rotulo_da_regiao(regiao)
        por_nome = (ebs.get("regions") or {}).get(rotulo) or {}
        for nome, (categoria, detalhe) in _AWS_EBS_TIPOS.items():
            preco = aws._decimal((por_nome.get(nome) or {}).get("price"))
            if preco is not None:
                out.append(
                    _point(
                        "aws",
                        categoria,
                        "gb_month",
                        preco,
                        "aws_pricing_calculator",
                        region=regiao,
                        detalhe=detalhe,
                    )
                )
        s3 = _aws_s3_standard(
            aws.get_json(_AWS_BULK.format(servico="AmazonS3", regiao=regiao), timeout=timeout)
        )
        if s3 is not None:
            out.append(
                _point(
                    "aws",
                    "object_storage",
                    "gb_month",
                    s3,
                    "aws_price_list_bulk",
                    region=regiao,
                    detalhe="s3-standard",
                )
            )
        transferencia = aws.get_json(
            _AWS_BULK.format(servico="AWSDataTransfer", regiao=regiao), timeout=timeout
        )
        egress = _aws_egress(transferencia, rotulo)
        if egress is not None:
            out.append(
                _point(
                    "aws",
                    "network_egress",
                    "gb",
                    egress,
                    "aws_price_list_bulk",
                    region=regiao,
                    detalhe="internet",
                )
            )
        out += _aws_rede_da_regiao(regiao, transferencia, timeout)
        out += _aws_por_uso_da_regiao(regiao, timeout)
        out += _aws_cdn_da_regiao(cloudfront, regiao)
    return out


# ── GCP ──────────────────────────────────────────────────────────────────────


def _gcp_destino_do_egress(regiao: str) -> str | None:
    """Destino do egress Premium cotado: o próprio continente da região."""
    if regiao.startswith("southamerica-"):
        return "South America"
    if regiao.startswith(("us-", "northamerica-")):
        return "Americas"
    if regiao.startswith("europe-"):
        return "Western Europe"
    return None


def _gcp_taxa(
    skus: list[dict[str, Any]],
    regiao: str,
    grupo: str,
    casa: Callable[[str], bool],
) -> Decimal | None:
    """Primeira faixa paga do SKU on-demand da região; o mais específico vence.

    Mais específico = menos regiões em `serviceRegions`: "Balanced PD Capacity
    in Sao Paulo" vale só lá; "Balanced PD Capacity" vale em seis.
    """
    candidatos = [
        s
        for s in skus
        if regiao in (s.get("serviceRegions") or [])
        and (s.get("category") or {}).get("usageType") == "OnDemand"
        and (s.get("category") or {}).get("resourceGroup") == grupo
        and casa(str(s.get("description") or ""))
    ]
    for sku in sorted(
        candidatos, key=lambda s: (len(s.get("serviceRegions") or []), s.get("description"))
    ):
        preco = gcp.preco_unitario(sku)
        if preco is not None:
            return preco
    return None


def fetch_gcp_category_rates(
    *, api_key: str = "", regioes: Sequence[str], timeout: float = 60.0
) -> list[CloudPricePoint]:
    """pd-balanced, pd-standard, egress Premium e Cloud Storage Standard."""
    if not api_key:
        return []
    compute = gcp.skus_do_servico(gcp.COMPUTE_SERVICE, api_key=api_key, timeout=timeout)
    storage = gcp.skus_do_servico(gcp.STORAGE_SERVICE, api_key=api_key, timeout=timeout)
    if compute is None or storage is None:
        return []
    # Sem o serviço de rede, as categorias dele ficam na referência; o resto segue
    rede = gcp.skus_do_servico(_GCP_NETWORKING, api_key=api_key, timeout=timeout) or []
    # R4: plano de controle, segredos e chaves; sem o serviço, ficam na referência
    por_uso = {
        servico: gcp.skus_do_servico(servico, api_key=api_key, timeout=timeout) or []
        for servico in (_GCP_KUBERNETES, _GCP_SERVICO_DE_SEGREDOS, _GCP_KMS)
    }
    out: list[CloudPricePoint] = []
    for regiao in regioes:
        taxas = (
            _gcp_taxas_da_regiao(compute, storage, regiao)
            + _gcp_rede_da_regiao(compute, rede, regiao)
            + _gcp_por_uso_da_regiao(por_uso, rede, regiao)
        )
        for categoria, unidade, detalhe, preco in taxas:
            if preco is not None:
                out.append(
                    _point(
                        "gcp", categoria, unidade, preco, gcp.SOURCE, region=regiao, detalhe=detalhe
                    )
                )
    return out


def _gcp_taxas_da_regiao(
    compute: list[dict[str, Any]], storage: list[dict[str, Any]], regiao: str
) -> list[tuple[str, str, str, Decimal | None]]:
    destino = _gcp_destino_do_egress(regiao)

    def _egress(d: str) -> bool:
        return destino is not None and d.endswith(f" to {destino}")

    return [
        (
            "storage_ssd",
            "gb_month",
            "pd-balanced",
            _gcp_taxa(compute, regiao, "SSD", lambda d: d.startswith("Balanced PD Capacity")),
        ),
        (
            "storage_hdd",
            "gb_month",
            "pd-standard",
            _gcp_taxa(compute, regiao, "PDStandard", lambda d: d.startswith("Storage PD Capacity")),
        ),
        (
            "object_storage",
            "gb_month",
            "standard",
            _gcp_taxa(
                storage, regiao, "RegionalStorage", lambda d: d.startswith("Standard Storage")
            ),
        ),
        (
            "network_egress",
            "gb",
            "premium",
            _gcp_taxa(compute, regiao, "PremiumInternetEgress", _egress),
        ),
    ]


_GCP_NETWORKING = "E505-1604-58F8"
_GCP_KUBERNETES = "CCD8-9BF1-090E"
_GCP_SERVICO_DE_SEGREDOS = "EE82-7A5E-871C"
_GCP_KMS = "EE2F-D110-890C"
_DEZ_MIL = Decimal(10_000)


def _gcp_destino_da_cdn(regiao: str) -> str:
    """Continente de quem a CDN atende, como o SKU do Cloud CDN escreve."""
    if regiao.startswith("southamerica-"):
        return "Latin America"
    if regiao.startswith("europe-"):
        return "Europe"
    if regiao.startswith(("asia-", "australia-")):
        return "Asia" if regiao.startswith("asia-") else "Oceania"
    return "North America"


def _gcp_por_uso_da_regiao(
    por_uso: dict[str, list[dict[str, Any]]], rede: list[dict[str, Any]], regiao: str
) -> list[tuple[str, str, str, Decimal | None]]:
    """Plano de controle, segredos, chaves e CDN (R4). Operação tem preço unitário:
    vai a 10 mil, a unidade da categoria."""

    def _por_dez_mil(preco: Decimal | None) -> Decimal | None:
        return preco * _DEZ_MIL if preco is not None else None

    kubernetes = por_uso.get(_GCP_KUBERNETES) or []
    segredos = por_uso.get(_GCP_SERVICO_DE_SEGREDOS) or []
    chaves = por_uso.get(_GCP_KMS) or []
    destino = _gcp_destino_da_cdn(regiao)
    return [
        (
            "gke_control_plane",
            "hour",
            "zonal-cluster",
            _gcp_na_regiao(kubernetes, regiao, lambda d: d == "Zonal Kubernetes Clusters"),
        ),
        (
            "secrets",
            "month",
            "secret-version",
            _gcp_na_regiao(segredos, regiao, lambda d: d == "Secret version replica storage"),
        ),
        (
            "secrets_operations",
            "10k",
            "access",
            _por_dez_mil(
                _gcp_na_regiao(segredos, regiao, lambda d: d == "Secret access operations")
            ),
        ),
        (
            "key_management",
            "month",
            "software-key",
            _gcp_na_regiao(chaves, regiao, lambda d: d == "Active software symmetric key versions"),
        ),
        (
            "key_management_operations",
            "10k",
            "software-key",
            _por_dez_mil(
                _gcp_na_regiao(
                    chaves,
                    regiao,
                    lambda d: d == "Cryptographic operations with a software symmetric key",
                )
            ),
        ),
        (
            "cdn",
            "gb",
            "cloud-cdn",
            _gcp_na_regiao(
                rede,
                regiao,
                lambda d: d == f"Networking Cloud CDN Traffic Cache Data Transfer to {destino}",
            ),
        ),
        (
            "cdn_requests",
            "10k",
            "cloud-cdn",
            _por_dez_mil(
                _gcp_na_regiao(rede, regiao, lambda d: d == "Networking Cloud Cdn Cache Lookups")
            ),
        ),
    ]


def _gcp_na_regiao(
    skus: list[dict[str, Any]], regiao: str, casa: Callable[[str], bool]
) -> Decimal | None:
    """Primeira faixa paga do SKU da região, ou do global; o mais específico vence.

    Os SKUs de rede não têm `resourceGroup` estável, e vários valem só como
    `global` (IP externo, dados do Cloud NAT)."""
    candidatos = [
        s
        for s in skus
        if {regiao, "global"} & set(s.get("serviceRegions") or [])
        and (s.get("category") or {}).get("usageType") == "OnDemand"
        and casa(str(s.get("description") or ""))
    ]
    for sku in sorted(
        candidatos, key=lambda s: (len(s.get("serviceRegions") or []), s.get("description"))
    ):
        preco = gcp.preco_unitario(sku)
        if preco is not None:
            return preco
    return None


def _gcp_rede_da_regiao(
    compute: list[dict[str, Any]], rede: list[dict[str, Any]], regiao: str
) -> list[tuple[str, str, str, Decimal | None]]:
    tunel = _gcp_na_regiao(rede, regiao, lambda d: d.startswith("Networking Cloud VPN Tunnel"))
    return [
        (
            "load_balancer",
            "hour",
            "forwarding-rule",
            _gcp_na_regiao(rede, regiao, lambda d: "Forwarding Rule Minimum" in d),
        ),
        (
            "load_balancer_data",
            "gb",
            "data-processing",
            _gcp_na_regiao(
                rede,
                regiao,
                lambda d: "Application Load Balancer" in d and "Data Processing" in d,
            ),
        ),
        (
            "nat_gateway_data",
            "gb",
            "cloud-nat",
            _gcp_na_regiao(rede, regiao, lambda d: d == "Networking Cloud Nat Data Processing"),
        ),
        # A conexão tem dois túneis, como a VPN site a site da AWS
        ("vpn", "hour", "ha-vpn", tunel * 2 if tunel is not None else None),
        (
            "public_ip",
            "hour",
            "external-ip",
            _gcp_na_regiao(compute, regiao, lambda d: d == "External IP Charge on a Standard VM"),
        ),
        (
            "inter_zone_transfer",
            "gb",
            "inter-zone",
            # A genérica vale em 5 regiões; São Paulo tem a sua ("... in Sao Paulo")
            _gcp_na_regiao(
                compute, regiao, lambda d: d.startswith("Network Inter Zone Data Transfer Out")
            ),
        ),
    ]


# ── Azure ────────────────────────────────────────────────────────────────────


def _azure_itens(filtro: str, timeout: float) -> list[dict[str, Any]]:
    url = f"{_AZURE}?{urllib.parse.urlencode({'$filter': filtro})}"
    itens: list[dict[str, Any]] = _get(url, timeout).get("Items", [])
    return itens


# Balanceador e NAT têm preço global na Azure: (serviço, medidor, unidade) → categoria
_AZURE_GLOBAL = {
    ("Load Balancer", "Standard Included LB Rules and Outbound Rules", "1 Hour"): (
        "load_balancer",
        "hour",
    ),
    ("Load Balancer", "Standard Data Processed", "1 GB"): ("load_balancer_data", "gb"),
    ("NAT Gateway", "Standard Gateway", "1 Hour"): ("nat_gateway", "hour"),
    ("NAT Gateway", "Standard Data Processed", "1 GB"): ("nat_gateway_data", "gb"),
}


def fetch_azure_network_global(*, timeout: float = 30.0) -> tuple[tuple[str, str, Decimal], ...]:
    """Balanceador e NAT da Azure, (categoria, unidade, preço). Preço global: o sync
    busca uma vez e repassa para cada região (a API devolve 429 cedo)."""
    precos: dict[tuple[str, str], Decimal] = {}
    for servico in sorted({s for s, *_ in _AZURE_GLOBAL}):
        filtro = (
            f"serviceName eq '{servico}' and armRegionName eq 'Global' "
            "and priceType eq 'Consumption'"
        )
        for item in _azure_itens(filtro, timeout):
            # O filtro é do servidor; a conferência aqui é a do item
            chave = (
                str(item.get("serviceName")),
                str(item.get("meterName")),
                str(item.get("unitOfMeasure")),
            )
            alvo = _AZURE_GLOBAL.get(chave)
            price = Decimal(str(item.get("retailPrice") or 0))
            if alvo is not None and price > 0:
                precos[alvo] = max(price, precos.get(alvo, Decimal(0)))
    return tuple((categoria, unidade, preco) for (categoria, unidade), preco in precos.items())


def _azure_rede_regional(
    region: str,
    banda: list[dict[str, Any]],
    rede_global: tuple[tuple[str, str, Decimal], ...],
    timeout: float,
) -> list[CloudPricePoint]:
    precos: list[tuple[str, str, str, Decimal]] = list(
        (categoria, unidade, "global", preco) for categoria, unidade, preco in rede_global
    )
    entre_zonas = [
        Decimal(str(i.get("retailPrice") or 0))
        for i in banda
        if i.get("meterName") == "Standard Inter-Availability Zone Data Transfer Out"
        and i.get("unitOfMeasure") == "1 GB"
    ]
    if entre_zonas and max(entre_zonas) > 0:
        precos.append(("inter_zone_transfer", "gb", "inter-az", max(entre_zonas)))
    filtro = (
        f"serviceName eq 'Virtual Network' and armRegionName eq '{region}' "
        "and priceType eq 'Consumption' and meterName eq 'Standard IPv4 Static Public IP'"
    )
    ip = [
        Decimal(str(i.get("retailPrice") or 0))
        for i in _azure_itens(filtro, timeout)
        if i.get("meterName") == "Standard IPv4 Static Public IP"
        and i.get("unitOfMeasure") == "1 Hour"
    ]
    if ip and max(ip) > 0:
        precos.append(("public_ip", "hour", "standard-ipv4", max(ip)))
    precos += _azure_por_uso_regional(region, timeout)
    return [
        _point("azure", c, u, preco, "azure_retail_api", region=region, detalhe=d)
        for c, u, d, preco in precos
    ]


# R4: (serviço, medidor, unidade) → (categoria, unidade da categoria, detalhe). O plano
# de controle é o tier Standard, com SLA, como os do EKS, do GKE e do OKE Enhanced.
_AZURE_POR_USO: dict[tuple[str, str, str], tuple[tuple[str, str, str], ...]] = {
    ("Azure Kubernetes Service", "Standard Uptime SLA", "1 Hour"): (
        ("gke_control_plane", "hour", "aks-standard"),
    ),
    # A mesma operação serve a segredo e a chave de software (Standard)
    ("Key Vault", "Operations", "10K"): (
        ("secrets_operations", "10k", "key-vault"),
        ("key_management_operations", "10k", "key-vault"),
    ),
}


def _azure_zona_da_front_door(region: str) -> str:
    """Zona de preço da Front Door de quem a região atende."""
    if region.startswith("brazil"):
        return "Zone 3"
    if region.startswith("australia"):
        return "Zone 4"
    if region.endswith("india") or region.startswith(("centralindia", "southindia", "westindia")):
        return "Zone 5"
    if region.startswith(("eastasia", "southeastasia", "japan", "korea")):
        return "Zone 2"
    return "Zone 1"


def _azure_por_uso_regional(region: str, timeout: float) -> list[tuple[str, str, str, Decimal]]:
    """Plano de controle e operações do Key Vault (por região), e Front Door (por zona)."""
    precos: list[tuple[str, str, str, Decimal]] = []
    for servico in ("Azure Kubernetes Service", "Key Vault"):
        filtro = (
            f"serviceName eq '{servico}' and armRegionName eq '{region}' "
            "and priceType eq 'Consumption'"
        )
        achados: dict[tuple[str, str, str], Decimal] = {}
        for item in _azure_itens(filtro, timeout):
            chave = (
                str(item.get("serviceName")),
                str(item.get("meterName")),
                str(item.get("unitOfMeasure")),
            )
            preco = Decimal(str(item.get("retailPrice") or 0))
            if chave in _AZURE_POR_USO and preco > 0:
                achados[chave] = max(preco, achados.get(chave, Decimal(0)))
        precos += [
            (categoria, unidade, detalhe, preco)
            for chave, preco in achados.items()
            for categoria, unidade, detalhe in _AZURE_POR_USO[chave]
        ]
    zona = _azure_zona_da_front_door(region)
    filtro = (
        "serviceName eq 'Azure Front Door Service' and priceType eq 'Consumption' "
        f"and armRegionName eq '{zona}'"
    )
    cdn: dict[str, Decimal] = {}
    for item in _azure_itens(filtro, timeout):
        if item.get("productName") != "Azure Front Door" or item.get("tierMinimumUnits") not in (
            0,
            0.0,
        ):
            continue
        preco = Decimal(str(item.get("retailPrice") or 0))
        if preco <= 0:
            continue
        if (
            item.get("meterName") == "Standard Data Transfer Out"
            and item.get("unitOfMeasure") == "1 GB"
        ):
            cdn["cdn"] = preco
        elif item.get("meterName") == "Standard Requests" and item.get("unitOfMeasure") == "10K":
            cdn["cdn_requests"] = preco
    precos += [
        (categoria, "gb" if categoria == "cdn" else "10k", "front-door-standard", preco)
        for categoria, preco in cdn.items()
    ]
    return precos


def fetch_azure_category_rates(
    region: str = "eastus",
    *,
    timeout: float = 30.0,
    rede_global: tuple[tuple[str, str, Decimal], ...] | None = None,
) -> list[CloudPricePoint]:
    """Egress (Standard Data Transfer Out) e Blob Hot LRS de uma região.

    A tarifa de egress cai por volume; pegamos a maior faixa padrão como
    estimativa conservadora (o que a maioria dos custos encontra primeiro), não
    o mínimo de alto volume. O Blob é o `General Block Blob v2` na faixa
    inicial."""
    egress: Decimal | None = None
    filtro = (
        f"serviceName eq 'Bandwidth' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    )
    banda = _azure_itens(filtro, timeout)
    for item in banda:
        if item.get("meterName") != "Standard Data Transfer Out":
            continue
        if item.get("unitOfMeasure") != "1 GB":
            continue
        price = Decimal(str(item.get("retailPrice") or 0))
        if price > 0 and (egress is None or price > egress):
            egress = price

    blob: Decimal | None = None
    filtro = (
        f"serviceName eq 'Storage' and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption' and meterName eq 'Hot LRS Data Stored'"
    )
    for item in _azure_itens(filtro, timeout):
        if item.get("productName") != "General Block Blob v2":
            continue
        if item.get("unitOfMeasure") != "1 GB/Month":
            continue
        if item.get("tierMinimumUnits") not in (0, 0.0):
            continue
        price = Decimal(str(item.get("retailPrice") or 0))
        if price > 0:
            blob = price

    out: list[CloudPricePoint] = []
    if egress is not None:
        out.append(
            _point("azure", "network_egress", "gb", egress, "azure_retail_api", region=region)
        )
    if blob is not None:
        out.append(
            _point(
                "azure",
                "object_storage",
                "gb_month",
                blob,
                "azure_retail_api",
                region=region,
                detalhe="blob-hot-lrs",
            )
        )
    if rede_global is None:
        rede_global = fetch_azure_network_global(timeout=timeout)
    return out + _azure_rede_regional(region, banda, rede_global, timeout)


# ── OCI ──────────────────────────────────────────────────────────────────────


def _oci_primeira_faixa_paga(item: dict[str, Any]) -> Decimal | None:
    precos = (item.get("currencyCodeLocalizations") or [{}])[0].get("prices") or []
    for p in precos:
        valor = Decimal(str(p.get("value") or 0))
        if valor > 0:
            return valor
    return None


# VPU por GB do perfil Balanced do Block Volume
_VPU_BALANCED = Decimal(10)


def _oci_block_volume(
    block: Decimal | None, vpu: Decimal | None, regiao: str
) -> list[CloudPricePoint]:
    """SSD é o Balanced, o padrão do volume: armazenamento + 10 VPU por GB. HDD é o
    Lower Cost, sem VPU (docs.oracle.com, Block Volume Performance). Sem o preço do
    VPU o SSD não sai: o armazenamento sozinho o deixava ~40% barato. Mesma chave de
    antes, para o preço novo substituir o antigo no sync."""
    if block is None:
        return []
    tiers: dict[str, Decimal] = {"storage_hdd": block}
    if vpu is not None:
        tiers["storage_ssd"] = block + _VPU_BALANCED * vpu
    return [
        _point("oci", c, "gb_month", p, "oci_cetools_api", region=regiao, detalhe="block-volume")
        for c, p in tiers.items()
    ]


def _oci_origem_do_egress(regiao: str) -> str:
    """Grupo de origem do egress da OCI, como o `cetools` escreve."""
    if regiao.startswith(("sa-", "ap-")):
        return "APAC, Japan, and South America"
    if regiao.startswith(("me-", "af-", "il-")):
        return "Middle East and Africa"
    return "North America, Europe, and UK"


# Produto da OCI com preço por nome → (categoria, unidade, detalhe). O plano de controle
# é o cluster Enhanced, o par do EKS e do GKE (R4).
_OCI_POR_NOME = {
    "Load Balancer Base": ("load_balancer", "hour", "flexible-lb"),
    "OCI Kubernetes Engine - Enhanced Cluster": ("gke_control_plane", "hour", "enhanced-cluster"),
}


def fetch_oci_category_rates(
    regioes: Sequence[str] = ("us-ashburn-1",), *, timeout: float = 30.0
) -> list[CloudPricePoint]:
    """Block volume, Object Storage e egress, gravados em cada região pedida.

    Armazenamento tem preço global na OCI; o egress depende do continente de
    origem e sai por região com o valor do grupo dela."""
    items = _get(_OCI, timeout).get("items", [])
    block: Decimal | None = None
    vpu: Decimal | None = None
    objeto: Decimal | None = None
    egress_por_origem: dict[str, Decimal] = {}
    rede: dict[tuple[str, str, str], Decimal] = {}
    for it in items:
        name = it.get("displayName") or ""
        valor = _oci_primeira_faixa_paga(it)
        if valor is None:
            continue
        if name == "Storage - Block Volume - Storage":
            block = valor
        elif name == "Storage - Block Volume - Performance Units":
            vpu = valor
        elif name == "Object Storage - Storage":
            objeto = valor
        elif name.startswith("Outbound Data Transfer - Originating in "):
            egress_por_origem[name.removeprefix("Outbound Data Transfer - Originating in ")] = valor
        elif name in _OCI_POR_NOME:
            rede[_OCI_POR_NOME[name]] = valor
        elif name == "Load Balancer Bandwidth":
            # Por Mbps-hora; 1 Mbps durante uma hora são 0,45 GB
            rede[("load_balancer_data", "gb", "flexible-lb")] = (valor / _MBPS_HORA_EM_GB).quantize(
                Decimal("0.00000001")
            )

    out: list[CloudPricePoint] = []
    for regiao in regioes:
        out += _oci_block_volume(block, vpu, regiao)
        if objeto is not None:
            out.append(
                _point(
                    "oci",
                    "object_storage",
                    "gb_month",
                    objeto,
                    "oci_cetools_api",
                    region=regiao,
                    detalhe="object-storage",
                )
            )
        out += [
            _point(
                "oci",
                categoria,
                unidade,
                preco,
                "oci_cetools_api",
                region=regiao,
                detalhe=detalhe,
            )
            for (categoria, unidade, detalhe), preco in rede.items()
        ]
        egress = egress_por_origem.get(_oci_origem_do_egress(regiao))
        if egress is not None:
            out.append(
                _point(
                    "oci",
                    "network_egress",
                    "gb",
                    egress,
                    "oci_cetools_api",
                    region=regiao,
                    detalhe="internet",
                )
            )
    return out
