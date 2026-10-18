"""Linha do balde de rede separada pelo tipo (PLANO_PRECO_NAO_COMPUTE §2, R1).

A classificação manda para `network_egress` tudo que é rede: o egress de verdade e
também o balanceador, o NAT, o IP público, a VPN e o tráfego entre zonas. Cotar o
balde inteiro pela razão da taxa de egress transformava hora de balanceador e GB de
NAT em "GB de internet": na OCI saíam por 10% do valor, no GCP 33% acima.

Aqui o uso de cada linha (UsageType, descrição do SKU, medidor) diz o tipo. Cada tipo
tem uma unidade (hora ou GB) e o destino é cotado pela quantidade medida vezes a taxa
dele. O fator traduz a unidade da linha para a da categoria quando elas não são a
mesma coisa (regra adicional de balanceador, VM-hora do Cloud NAT, túnel de VPN,
Mbps-hora da OCI). O que não casa vai para `network_other`, mantido ao custo atual.

Tabela de dados, sem ``if`` por nuvem. Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

EGRESS = "network_egress"
ENTRE_ZONAS = "inter_zone_transfer"
BALANCEADOR = "load_balancer"
BALANCEADOR_DADOS = "load_balancer_data"
NAT = "nat_gateway"
NAT_DADOS = "nat_gateway_data"
IP_PUBLICO = "public_ip"
VPN = "vpn"
# CDN: o tráfego entregue pela borda e as requisições (R4). Da borda para a origem é
# tráfego comum.
CDN = "cdn"
CDN_REQUISICOES = "cdn_requests"
OUTROS = "network_other"

HORA = "hour"
GB = "gb"
MES = "month"
# Operações e requisições, em lotes de 10 mil (a unidade das tabelas de preço)
DEZ_MIL = "10k"
# Armazenamento de log e de imagem (PLANO_EQUIVALENTES_E_TARIFAS §3)
GB_MES = "gb_month"

# Todas as categorias em que o balde de rede se divide, na ordem de exibição
CATEGORIAS: tuple[str, ...] = (
    EGRESS,
    ENTRE_ZONAS,
    BALANCEADOR,
    BALANCEADOR_DADOS,
    NAT,
    NAT_DADOS,
    IP_PUBLICO,
    VPN,
    CDN,
    CDN_REQUISICOES,
    OUTROS,
)

# Categoria → chave dela em `custos_auxiliares` do payload (o egress segue `egresso_rede`)
CHAVE_NO_PAYLOAD: Mapping[str, str] = {
    ENTRE_ZONAS: "transferencia_entre_zonas",
    BALANCEADOR: "balanceador",
    BALANCEADOR_DADOS: "balanceador_dados",
    NAT: "nat",
    NAT_DADOS: "nat_dados",
    IP_PUBLICO: "ip_publico",
    VPN: "vpn",
    CDN: "cdn",
    CDN_REQUISICOES: "cdn_requisicoes",
    OUTROS: "rede_outros",
}

# Categoria cotada pela quantidade → unidade dela; `network_other` não é cotada. O
# egress entrou em 17/10 (PLANO_PRECISAO_OCI P1.5): pela quantidade, a franquia do
# destino pode ser descontada; pela razão de taxas, não.
UNIDADE: Mapping[str, str] = {
    EGRESS: GB,
    BALANCEADOR: HORA,
    BALANCEADOR_DADOS: GB,
    NAT: HORA,
    NAT_DADOS: GB,
    IP_PUBLICO: HORA,
    VPN: HORA,
    ENTRE_ZONAS: GB,
    CDN: GB,
    CDN_REQUISICOES: DEZ_MIL,
}

# GB de saída para a internet que o destino não cobra por mês, somadas todas as regiões
# (fontes oficiais lidas em 17/10/2026): OCI, primeiros 10 TB (oracle.com, preço de
# rede da VCN); AWS, 100 GB (aws.amazon.com, free tier de data transfer, desde 12/2021);
# Azure, 100 GB (azure.microsoft.com, bandwidth pricing). GCP Premium: nenhuma.
FRANQUIA_DE_EGRESS_GB: Mapping[str, Decimal] = {
    "oci": Decimal("10240"),
    "aws": Decimal("100"),
    "azure": Decimal("100"),
}

_1 = Decimal(1)
# Requisição contada uma a uma, na unidade de 10 mil
_POR_DEZ_MIL = Decimal("0.0001")
# Regra adicional (US$ 0,01/h) contra a mínima, que é o balanceador (US$ 0,025/h)
_REGRA_ADICIONAL = Decimal("0.4")
# Cloud NAT cobra por VM (US$ 0,0014/h) até o teto do gateway (US$ 0,044/h, 32 VMs)
_VM_DO_CLOUD_NAT = Decimal("0.0318")
# Conexão de VPN tem dois túneis, como a da AWS; o GCP cobra por túnel
_TUNEL_DE_VPN = Decimal("0.5")
# 1 Mbps durante uma hora: 3.600 Mb = 0,45 GB
_MBPS_HORA_EM_GB = Decimal("0.45")


@dataclass(frozen=True, slots=True)
class TipoDeRede:
    categoria: str
    # Quanto da quantidade da linha vale na unidade da categoria
    fator: Decimal = _1


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(texto, re.IGNORECASE)


# Provedor → (padrão em "serviço | uso", categoria, fator), na ordem: o primeiro que casa
REGRAS: Mapping[str, tuple[tuple[re.Pattern[str], str, Decimal], ...]] = {
    "aws": (
        # CDN antes do egress: a CloudFront usa o mesmo UsageType da internet
        (_r(r"cloudfront[^|]*\|.*requests"), CDN_REQUISICOES, _POR_DEZ_MIL),
        (_r(r"cloudfront[^|]*\|.*datatransfer-out-bytes"), CDN, _1),
        (_r(r"datatransfer-regional-bytes"), ENTRE_ZONAS, _1),
        (_r(r"lcuusage|lcubytes"), BALANCEADOR_DADOS, _1),
        (_r(r"(elb|load ?balanc).*dataprocessing-bytes"), BALANCEADOR_DADOS, _1),
        (_r(r"loadbalancerusage"), BALANCEADOR, _1),
        (_r(r"natgateway-bytes"), NAT_DADOS, _1),
        (_r(r"natgateway-hours"), NAT, _1),
        (_r(r"publicipv4"), IP_PUBLICO, _1),
        (_r(r"vpn-usage-hours"), VPN, _1),
        (_r(r"datatransfer|-(in|out)-bytes|vpcpeering"), EGRESS, _1),
    ),
    "gcp": (
        (_r(r"cdn.*lookup"), CDN_REQUISICOES, _1),
        (_r(r"cdn.*(cache data transfer|cache egress)"), CDN, _1),
        (_r(r"inter ?zone"), ENTRE_ZONAS, _1),
        (_r(r"load ?balanc.*data processing"), BALANCEADOR_DADOS, _1),
        (_r(r"nat.*data processing"), NAT_DADOS, _1),
        (_r(r"nat ip usage"), IP_PUBLICO, _1),
        (_r(r"data transfer|egress"), EGRESS, _1),
        (
            _r(r"forwarding rule additional|passthrough.*forwarding rule"),
            BALANCEADOR,
            _REGRA_ADICIONAL,
        ),
        (_r(r"forwarding rule"), BALANCEADOR, _1),
        (_r(r"private nat gateway uptime"), NAT, _1),
        (_r(r"nat gateway uptime"), NAT, _VM_DO_CLOUD_NAT),
        (_r(r"static ip|external ip|ip charge"), IP_PUBLICO, _1),
        (_r(r"vpn tunnel|cloud vpn"), VPN, _TUNEL_DE_VPN),
    ),
    "azure": (
        (_r(r"(front door|content delivery network)[^|]*\|.*requests"), CDN_REQUISICOES, _1),
        (_r(r"(front door|content delivery network)[^|]*\|.*data transfer(?! in)"), CDN, _1),
        (_r(r"inter-availability zone"), ENTRE_ZONAS, _1),
        (_r(r"nat gateway.*data processed"), NAT_DADOS, _1),
        (_r(r"nat gateway"), NAT, _1),
        (
            _r(r"load balancer.*data processed|application gateway.*capacity unit"),
            BALANCEADOR_DADOS,
            _1,
        ),
        (_r(r"load balancer.*overage"), BALANCEADOR, _REGRA_ADICIONAL),
        (_r(r"load balancer|application gateway.*fixed cost"), BALANCEADOR, _1),
        (_r(r"ip address|public ip"), IP_PUBLICO, _1),
        (_r(r"vpn gateway.*p2s"), OUTROS, _1),
        (_r(r"vpn gateway"), VPN, _1),
        (_r(r"bandwidth|data transfer"), EGRESS, _1),
    ),
    "oci": (
        (_r(r"load balancer.*bandwidth"), BALANCEADOR_DADOS, _MBPS_HORA_EM_GB),
        (_r(r"load balancer"), BALANCEADOR, _1),
        (_r(r"data transfer"), EGRESS, _1),
    ),
}

_OUTROS = TipoDeRede(OUTROS)


def tipo_de_rede(provedor: str, servico: str | None, uso: str | None) -> TipoDeRede:
    """Categoria e fator de uma linha do balde de rede."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, categoria, fator in REGRAS.get(provedor.lower(), ()):
        if padrao.search(texto):
            return TipoDeRede(categoria, fator)
    return _OUTROS


_ESCALA_1 = Decimal(1)

# Rótulo de unidade da linha → (dimensão, quanto um vale nela). A Mbps-hora da OCI
# entra como GB pelo fator da regra; a requisição contada uma a uma vira 10 mil aqui.
_UNIDADES: Mapping[str, tuple[str, Decimal]] = {
    **{
        rotulo: (HORA, _ESCALA_1)
        for rotulo in (
            "h", "hr", "hrs", "hour", "hours", "1 hour", "1/hour",
            "cluster per hour", "cluster hours", "clusters per hour",
        )
    },
    **{
        rotulo: (GB, _ESCALA_1)
        for rotulo in (
            "gb", "gib", "giby", "gibibyte", "gigabyte", "1 gb", "1 gib",
            "mbps per hour", "mbps hours",
        )
    },
    **{
        rotulo: (MES, _ESCALA_1)
        for rotulo in (
            "month", "months", "1/month", "key version per month", "1", "instance per month",
            "instance months",
        )
    },
    **{
        rotulo: (GB_MES, _ESCALA_1)
        for rotulo in (
            "gb-mo", "gb-month", "gb/month", "1 gb/month", "gibibyte month", "gigabyte month",
        )
    },
    **{rotulo: (DEZ_MIL, _ESCALA_1) for rotulo in ("10k", "10,000", "10000")},
    **{rotulo: (DEZ_MIL, _POR_DEZ_MIL) for rotulo in ("count", "requests", "api requests")},
    **{
        rotulo: (DEZ_MIL, Decimal(100))
        for rotulo in (
            "1m", "1m/month", "1,000,000 queries", "1,000,000 incoming requests per month",
            "million requests",
        )
    },
    **{rotulo: (DEZ_MIL, Decimal(1000)) for rotulo in ("10m", "10m samples")},
}


def converter_quantidade(
    dimensao: str | None,
    provedor: str,
    fator: Decimal,
    quantidade: Decimal | None,
    unidade: str | None,
) -> Decimal | None:
    """Quantidade da linha na dimensão da categoria, ou None quando não serve.

    Na AWS o UsageType já diz a unidade (`-Hours`, `-Bytes`, `LCUUsage`), e o rótulo
    do export varia (`Units`, vazio): vale a quantidade vezes o fator da regra. Nas
    outras, o rótulo precisa ser da dimensão da categoria, e a escala dele entra."""
    if dimensao is None or quantidade is None or quantidade <= 0:
        return None
    if provedor.lower() == "aws":
        return quantidade * fator
    lida = _UNIDADES.get((unidade or "").strip().lower())
    if lida is None or lida[0] != dimensao:
        return None
    return quantidade * lida[1] * fator


def quantidade_na_unidade(
    provedor: str, tipo: TipoDeRede, quantidade: Decimal | None, unidade: str | None
) -> Decimal | None:
    """Quantidade da linha na unidade da categoria, ou None quando não serve."""
    return converter_quantidade(
        UNIDADE.get(tipo.categoria), provedor, tipo.fator, quantidade, unidade
    )
