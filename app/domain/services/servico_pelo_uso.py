"""Serviço de nome ambíguo na fatura, resolvido pelo uso das linhas.

Alguns exports dão o mesmo nome a serviços diferentes: o "Storage" da Azure é disco
gerenciado, blob ou arquivos; o "Networking" do GCP é balanceador, NAT, VPN ou DNS; o
"DATABASE" da OCI é MySQL, Autonomous ou Exadata. Um apelido único escolheria errado
para metade das contas. O uso de cada linha (medidor, SKU, descrição) diz qual é.

A regra vale para o serviço inteiro só quando um alvo tem a maior parte do custo
(60% ou mais): mistura de verdade continua sem tipo identificado. O nome da fatura não
muda; só a entrada do DE-PARA que dá o tipo e os equivalentes, para as telas que cruzam
o nome com o inventário continuarem casando.

Tabela de dados, sem ``if`` por nuvem. Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal

_ZERO = Decimal(0)
DOMINANTE = Decimal("0.6")


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(texto, re.IGNORECASE)


# (provedor, nome na fatura) → (padrão no uso, nome no DE-PARA), na ordem: o primeiro que casa vale
REGRAS: Mapping[tuple[str, str], tuple[tuple[re.Pattern[str], str], ...]] = {
    ("azure", "storage"): (
        (_r(r"\bfiles?\b"), "Azure Files"),
        (_r(r"managed disk|\bdisks?\b"), "Azure Managed Disks"),
        (_r(r"blob|data lake|data stored|\b(hot|cool|cold|archive)\b"), "Azure Blob Storage"),
    ),
    ("gcp", "networking"): (
        (_r(r"load balanc|forwarding rule"), "Cloud Load Balancing"),
        (_r(r"\bnat\b"), "Cloud NAT"),
        (_r(r"\bvpn\b"), "Cloud VPN"),
        (_r(r"\bdns\b"), "Cloud DNS"),
        (_r(r"interconnect"), "Cloud Interconnect"),
    ),
    ("oci", "database"): (
        (_r(r"mysql|heatwave"), "MySQL Database Service"),
        (_r(r"autonomous.*(data warehouse|\bdw\b)"), "Oracle Autonomous Data Warehouse"),
        (_r(r"autonomous.*json"), "Oracle Autonomous JSON Database"),
        (_r(r"exadata"), "Exadata Cloud Service"),
        (_r(r"autonomous|database"), "Oracle Database Cloud Service"),
    ),
    # Agrupamento do Cost Explorer: disco, snapshot, NAT, IP e tráfego da EC2 (item 17)
    ("aws", "ec2 - other"): (
        (_r(r"natgateway|nat gateway"), "AWS NAT Gateways"),
        (
            _r(r"ebs:|volumeusage|volumeio|volumep-iops|snapshot"),
            "Amazon Elastic Block Store (EBS)",
        ),
        (_r(r"elasticip|publicipv4|idleaddress"), "Amazon VPC"),
        (_r(r"datatransfer|data transfer|-bytes"), "AWS Data Transfer"),
    ),
    ("oci", "network"): (
        (_r(r"fastconnect"), "OCI FastConnect"),
        (_r(r"\bvpn\b"), "Oracle VPN Connect"),
    ),
}


def servico_pelo_uso(
    provedor: str, servico: str, usos: Iterable[tuple[str | None, Decimal]]
) -> tuple[str, Decimal] | None:
    """(nome no DE-PARA, fração do custo) quando um alvo domina o serviço; senão None.

    ``usos``: (descritor de uso da linha, custo) de cada linha do serviço."""
    regras = REGRAS.get((provedor.lower(), servico.strip().casefold()))
    if not regras:
        return None
    total = _ZERO
    por_alvo: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for descritor, custo in usos:
        if custo <= 0:
            continue
        total += custo
        for padrao, alvo in regras:
            if padrao.search(descritor or ""):
                por_alvo[alvo] += custo
                break
    if total <= 0 or not por_alvo:
        return None
    alvo, custo = max(por_alvo.items(), key=lambda kv: (kv[1], kv[0]))
    fracao = custo / total
    return (alvo, fracao) if fracao >= DOMINANTE else None
