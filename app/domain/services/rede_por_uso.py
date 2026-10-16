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
OUTROS = "network_other"

HORA = "hour"
GB = "gb"

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
    OUTROS: "rede_outros",
}

# Categoria cotada pela quantidade → unidade dela. O egress segue pela razão de taxas,
# como antes; `network_other` não é cotada.
UNIDADE: Mapping[str, str] = {
    BALANCEADOR: HORA,
    BALANCEADOR_DADOS: GB,
    NAT: HORA,
    NAT_DADOS: GB,
    IP_PUBLICO: HORA,
    VPN: HORA,
    ENTRE_ZONAS: GB,
}

_1 = Decimal(1)
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


# Rótulo de unidade da linha → dimensão. A Mbps-hora da OCI entra como GB pelo fator.
_UNIDADES: Mapping[str, str] = {
    "h": HORA,
    "hr": HORA,
    "hrs": HORA,
    "hour": HORA,
    "hours": HORA,
    "1 hour": HORA,
    "1/hour": HORA,
    "gb": GB,
    "gib": GB,
    "giby": GB,
    "gibibyte": GB,
    "gigabyte": GB,
    "1 gb": GB,
    "1 gib": GB,
    "mbps per hour": GB,
    "mbps hours": GB,
}


def quantidade_na_unidade(
    provedor: str, tipo: TipoDeRede, quantidade: Decimal | None, unidade: str | None
) -> Decimal | None:
    """Quantidade da linha na unidade da categoria, ou None quando não serve.

    Na AWS o UsageType já diz a unidade (`-Hours`, `-Bytes`, `LCUUsage`), e o rótulo
    do export varia (`Units`, vazio): vale a quantidade. Nas outras, a unidade da
    linha precisa ser a da categoria."""
    dimensao = UNIDADE.get(tipo.categoria)
    if dimensao is None or quantidade is None or quantidade <= 0:
        return None
    if provedor.lower() != "aws" and _UNIDADES.get((unidade or "").strip().lower()) != dimensao:
        return None
    return quantidade * tipo.fator
