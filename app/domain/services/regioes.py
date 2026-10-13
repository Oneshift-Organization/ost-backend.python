"""Regiões: onde a carga roda e onde cada provedor deve ser cotado.

O problema que motivou o módulo: toda cotação usava uma região fixa por provedor
(`us-east-1`, `eastus`, `us-central1`, `us-ashburn-1`). Um bill de São Paulo era
comparado com preço da Virgínia, que é ~1,6x mais barato. A economia de sair
saía otimista em ~60% no compute e até 90% no disco.

Três peças, todas puras:

- **zona**: a metrópole equivalente entre provedores. Cada zona tem a região
  que se cota como destino em cada provedor (a canônica) e as regiões que
  pertencem a ela. Região fora da tabela não ganha vizinho inventado: quem
  chama cai na zona padrão e avisa;
- **região da linha**: a coluna do export, senão o que a própria linha diz
  (prefixo do `UsageType` da AWS, localidade na descrição do SKU do GCP);
- **escolha do preço**: dada a região desejada e as regiões que têm preço
  sincronizado, qual consultar e com que fidelidade (exata, preço global ou
  aproximada).

Sem I/O, sem framework.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

# O que tem preço único em todas as regiões comerciais, por provedor. A Oracle
# publica isso como política para compute e armazenamento; o `cetools` nem tem
# região para eles. O egress da OCI não entra: depende do continente de origem
# (US$ 0,0085/GB na América do Norte e na Europa, 0,025 na América do Sul).
PRECO_GLOBAL: Mapping[str, frozenset[str]] = {
    "oci": frozenset(
        {"virtual_server", "storage_ssd", "storage_hdd", "object_storage", "database"}
    ),
}


def tem_preco_global(provedor: str, service_type: str) -> bool:
    return service_type in PRECO_GLOBAL.get(provedor.lower(), frozenset())


# Rótulo da região das taxas de referência digitadas (`seed_category_rates`).
# Não é região de provedor nenhum: é "costa leste dos EUA" de propósito.
REGIAO_DE_REFERENCIA = "us-east"


@dataclass(frozen=True, slots=True)
class Zona:
    """Metrópole equivalente entre provedores.

    ``canonica`` é a região cotada como destino. Quando o provedor não tem
    região na metrópole, a canônica é a vizinha mais próxima e fica fora de
    ``membros`` — ela pertence à zona onde de fato está.
    """

    id: str
    rotulo: str
    canonica: Mapping[str, str]
    membros: Mapping[str, frozenset[str]] = field(default_factory=dict)


def _zona(id_: str, rotulo: str, **por_provedor: tuple[str | None, ...]) -> Zona:
    """`aws=("us-east-1",)`: a primeira é a canônica; tupla com `None` na frente
    declara vizinha (`aws=(None, "us-east-2")`)."""
    canonica: dict[str, str] = {}
    membros: dict[str, frozenset[str]] = {}
    for provedor, regioes in por_provedor.items():
        proprias = [r for r in regioes if r is not None]
        canonica[provedor] = proprias[0]
        membros[provedor] = frozenset() if regioes[0] is None else frozenset(proprias)
    return Zona(id=id_, rotulo=rotulo, canonica=canonica, membros=membros)


ZONAS: tuple[Zona, ...] = (
    _zona(
        "virginia",
        "Virgínia (EUA)",
        aws=("us-east-1",),
        gcp=("us-east4", "us-east1"),
        azure=("eastus", "eastus2"),
        oci=("us-ashburn-1",),
    ),
    _zona(
        # `americas` é grupo de preço do GCP (Iowa, Carolina do Sul e Oregon
        # custam o mesmo); a canônica é Iowa.
        "meio-oeste",
        "Meio-oeste (EUA)",
        aws=("us-east-2",),
        gcp=("us-central1", "us-east5", "americas"),
        azure=("centralus", "northcentralus"),
        oci=("us-chicago-1",),
    ),
    _zona(
        "texas",
        "Texas (EUA)",
        aws=(None, "us-east-2"),
        gcp=("us-south1",),
        azure=("southcentralus",),
        oci=(None, "us-chicago-1"),
    ),
    _zona(
        "oregon",
        "Oregon e Washington (EUA)",
        aws=("us-west-2",),
        gcp=("us-west1",),
        azure=("westus2",),
        oci=(None, "us-sanjose-1"),
    ),
    _zona(
        "california",
        "Califórnia e sudoeste (EUA)",
        aws=("us-west-1",),
        gcp=("us-west2", "us-west4"),
        azure=("westus", "westus3"),
        oci=("us-sanjose-1", "us-phoenix-1"),
    ),
    _zona(
        "canada",
        "Canadá",
        aws=("ca-central-1",),
        gcp=("northamerica-northeast1", "northamerica-northeast2"),
        azure=("canadacentral", "canadaeast"),
        oci=("ca-montreal-1", "ca-toronto-1"),
    ),
    _zona(
        "sao-paulo",
        "São Paulo",
        aws=("sa-east-1",),
        gcp=("southamerica-east1",),
        azure=("brazilsouth", "brazilsoutheast"),
        oci=("sa-saopaulo-1", "sa-vinhedo-1"),
    ),
    _zona(
        "santiago",
        "Santiago",
        aws=(None, "sa-east-1"),
        gcp=("southamerica-west1",),
        azure=(None, "brazilsouth"),
        oci=("sa-santiago-1", "sa-valparaiso-1"),
    ),
    _zona(
        "londres",
        "Londres",
        aws=("eu-west-2",),
        gcp=("europe-west2",),
        azure=("uksouth", "ukwest"),
        oci=("uk-london-1", "uk-cardiff-1"),
    ),
    _zona(
        "frankfurt",
        "Frankfurt",
        aws=("eu-central-1",),
        gcp=("europe-west3",),
        azure=("germanywestcentral",),
        oci=("eu-frankfurt-1",),
    ),
    _zona(
        "europa-oeste",
        "Irlanda e Benelux",
        aws=("eu-west-1",),
        gcp=("europe-west1", "europe-west4"),
        azure=("northeurope", "westeurope"),
        oci=("eu-amsterdam-1",),
    ),
)

_POR_ID = {z.id: z for z in ZONAS}

# Onde já estavam três dos quatro padrões antigos e as taxas de referência.
ZONA_PADRAO = "virginia"


def zona(id_: str) -> Zona:
    return _POR_ID[id_]


def normalizar_regiao(provedor: str, regiao: str | None) -> str | None:
    """`East US` → `eastus` na Azure; minúsculas e sem espaço nas demais."""
    if not regiao:
        return None
    texto = regiao.strip().lower()
    if provedor.lower() == "azure":
        texto = texto.replace(" ", "")
    return texto or None


def zona_da_regiao(provedor: str, regiao: str | None) -> str | None:
    """Zona a que a região pertence, ou None se fora da tabela.

    O rótulo das taxas de referência (`us-east`) é costa leste dos EUA: está na
    Virgínia em qualquer provedor.
    """
    alvo = normalizar_regiao(provedor, regiao)
    if alvo is None:
        return None
    if alvo == REGIAO_DE_REFERENCIA:
        return "virginia"
    p = provedor.lower()
    for z in ZONAS:
        if alvo in z.membros.get(p, frozenset()):
            return z.id
    return None


def regiao_canonica(zona_id: str, provedor: str) -> str | None:
    """A região cotada como destino neste provedor para esta zona.

    None para provedor fora da tabela: quem chama cai na escolha aproximada.
    """
    return _POR_ID[zona_id].canonica.get(provedor.lower())


# ── Região da linha ──────────────────────────────────────────────────────────

# Prefixo do `UsageType` → região. Sem prefixo é us-east-1 (convenção da AWS).
_PREFIXOS_AWS = {
    "USE1": "us-east-1",
    "USE2": "us-east-2",
    "USW1": "us-west-1",
    "USW2": "us-west-2",
    "CAN1": "ca-central-1",
    "CAN2": "ca-west-1",
    "SAE1": "sa-east-1",
    "MXC1": "mx-central-1",
    "EUW1": "eu-west-1",
    "EUW2": "eu-west-2",
    "EUW3": "eu-west-3",
    "EUC1": "eu-central-1",
    "EUC2": "eu-central-2",
    "EUN1": "eu-north-1",
    "EUS1": "eu-south-1",
    "EUS2": "eu-south-2",
    "APN1": "ap-northeast-1",
    "APN2": "ap-northeast-2",
    "APN3": "ap-northeast-3",
    "APS1": "ap-southeast-1",
    "APS2": "ap-southeast-2",
    "APS3": "ap-south-1",
    "APS4": "ap-southeast-3",
    "APS5": "ap-south-2",
    "APE1": "ap-east-1",
    "MES1": "me-south-1",
    "MEC1": "me-central-1",
    "AFS1": "af-south-1",
    "ILC1": "il-central-1",
    "UGW1": "us-gov-west-1",
    "UGE1": "us-gov-east-1",
}
_PREFIXO_REGIONAL = re.compile(r"^([A-Z]{2,4}\d)-")
# `US-`, `EU-`, `SA-` (CloudFront, por edge) e `Global-` não são região.
_PREFIXO_NAO_REGIONAL = re.compile(r"^(?:[A-Z]{2}-|Global-)")

# Localidade na descrição do SKU do GCP → região (ou grupo de preço).
_LOCALIDADES_GCP = {
    "americas": "americas",
    "iowa": "us-central1",
    "south carolina": "us-east1",
    "virginia": "us-east4",
    "northern virginia": "us-east4",
    "columbus": "us-east5",
    "dallas": "us-south1",
    "oregon": "us-west1",
    "los angeles": "us-west2",
    "salt lake city": "us-west3",
    "las vegas": "us-west4",
    "montreal": "northamerica-northeast1",
    "toronto": "northamerica-northeast2",
    "sao paulo": "southamerica-east1",
    "santiago": "southamerica-west1",
    "belgium": "europe-west1",
    "london": "europe-west2",
    "frankfurt": "europe-west3",
    "netherlands": "europe-west4",
}
_DE_PARA = re.compile(r"\bfrom (.+?) to ", re.IGNORECASE)
_EM = re.compile(r"\bin ([^,()]+?)\s*$", re.IGNORECASE)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _regiao_aws(usage_type: str) -> str | None:
    m = _PREFIXO_REGIONAL.match(usage_type)
    if m:
        return _PREFIXOS_AWS.get(m.group(1))
    if _PREFIXO_NAO_REGIONAL.match(usage_type):
        return None
    return "us-east-1"


def _regiao_gcp(descricao: str) -> str | None:
    m = _DE_PARA.search(descricao) or _EM.search(descricao)
    if m is None:
        return None
    local = _sem_acento(m.group(1)).strip().lower()
    return _LOCALIDADES_GCP.get(local)


def regiao_da_linha(provedor: str, regiao: str | None, usage_type: str | None) -> str | None:
    """Região de uma linha de billing, ou None quando a linha não diz.

    A coluna do export vem primeiro. Sem ela, o que a própria linha informa:
    o prefixo do `UsageType` na AWS e a localidade da descrição do SKU no GCP.
    Azure e OCI sempre trazem a coluna.
    """
    p = provedor.lower()
    informada = normalizar_regiao(p, regiao)
    if informada and informada != "global":
        return informada
    if not usage_type:
        return None
    if p == "aws":
        return _regiao_aws(usage_type)
    if p == "gcp":
        return _regiao_gcp(usage_type)
    return None


def regiao_dominante(
    custos: Iterable[tuple[str, str | None, Decimal]],
) -> tuple[str, str] | None:
    """(zona, região) com mais custo entre linhas `(provedor, região, custo)`.

    A zona vem primeiro: um bill com 60% em `us-east-1` e 40% em `us-east-2`
    está na Virgínia e no meio-oeste, e cada uma conta separado. Dentro da
    zona, a região de mais custo. Empates resolvidos pelo nome, para a escolha
    ser reprodutível. None quando nenhuma linha tem zona.
    """
    por_zona: dict[str, dict[str, Decimal]] = {}
    for provedor, regiao, custo in custos:
        z = zona_da_regiao(provedor, regiao)
        if z is None or custo <= 0 or regiao is None:
            continue
        r = normalizar_regiao(provedor, regiao) or regiao
        regioes = por_zona.setdefault(z, {})
        regioes[r] = regioes.get(r, Decimal("0")) + custo
    if not por_zona:
        return None
    zona_ = max(sorted(por_zona), key=lambda z: sum(por_zona[z].values()))
    regioes = por_zona[zona_]
    return zona_, max(sorted(regioes), key=lambda r: regioes[r])


def zona_dominante(custos: Iterable[tuple[str, str | None, Decimal]]) -> str | None:
    """Zona com mais custo entre linhas `(provedor, região, custo)` com zona."""
    dominante = regiao_dominante(custos)
    return dominante[0] if dominante else None


# ── Escolha do preço ─────────────────────────────────────────────────────────


class Situacao(StrEnum):
    EXATA = "exata"
    # Outra região da mesma metrópole (`eastus2` para `eastus`, a referência
    # `us-east` para `us-east-1`). Não é aproximação de preço regional.
    MESMA_ZONA = "mesma_zona"
    PRECO_GLOBAL = "preco_global"
    APROXIMADA = "aproximada"


@dataclass(frozen=True, slots=True)
class RegiaoDePreco:
    regiao: str
    situacao: Situacao

    @property
    def aproximada(self) -> bool:
        return self.situacao is Situacao.APROXIMADA


def escolher_regiao_de_preco(
    provedor: str,
    preferidas: Sequence[str | None],
    disponiveis: Iterable[str],
    *,
    service_type: str = "virtual_server",
) -> RegiaoDePreco | None:
    """Qual região consultar, entre as que têm preço sincronizado.

    Em ordem: a primeira preferida que tenha preço; outra região da mesma zona
    de uma preferida; para provedor de preço global, qualquer região
    sincronizada; a região do provedor na zona padrão; a região de referência
    das taxas digitadas; a primeira em ordem alfabética. None só quando o
    provedor não tem preço nenhum.
    """
    regioes = sorted(set(disponiveis))
    if not regioes:
        return None
    for r in preferidas:
        if r and r in regioes:
            return RegiaoDePreco(r, Situacao.EXATA)
    p = provedor.lower()
    zonas = {z for r in preferidas if r and (z := zona_da_regiao(p, r))}
    for r in regioes:
        if zona_da_regiao(p, r) in zonas:
            return RegiaoDePreco(r, Situacao.MESMA_ZONA)
    if tem_preco_global(p, service_type):
        return RegiaoDePreco(regioes[0], Situacao.PRECO_GLOBAL)
    padrao = _POR_ID[ZONA_PADRAO].canonica.get(p)
    for r in (padrao, REGIAO_DE_REFERENCIA):
        if r in regioes:
            return RegiaoDePreco(r, Situacao.APROXIMADA)
    return RegiaoDePreco(regioes[0], Situacao.APROXIMADA)
