"""Prováveis aplicações dentro de um cluster Kubernetes (PLANO_CENARIOS_DE_ENTRADA §9, opção A).

O inventário da nuvem vê o cluster, os nós, o balanceador que o cluster criou e os
discos dos volumes; não vê Deployment nem Service. Mas deixa rastros: o repositório
de imagem de cada serviço, o segredo de cada um, o nome no service discovery, o
Service que a tag do balanceador cita, o namespace de cada volume.

Isto junta esses rastros e diz "provavelmente estas aplicações rodam aqui". É
indício, não inventário: não vira unidade, não divide custo, não entra no plano.
Segredo sozinho não basta (credencial de banco, variáveis globais); imagem, service
discovery ou Service do balanceador bastam.

Função pura, sem I/O.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.entities.topologia import Inventario, Recurso, TipoDeRecurso
from app.domain.services.topologia.modelo import AnaliseDeTopologia

IMAGEM = "imagem"
SEGREDO = "segredo"
DESCOBERTA = "service discovery"
BALANCEADOR = "balanceador"
_BASTA = frozenset({IMAGEM, DESCOBERTA, BALANCEADOR})

_FONTE_DO_COMPONENTE = {
    "registro_de_imagens": IMAGEM,
    "segredo": SEGREDO,
    "descoberta_de_servico": DESCOBERTA,
}
# Tag que o controlador de balanceador põe com `<namespace>/<service>`
_TAGS_DO_SERVICE = ("service.k8s.aws/stack",)
# Tag que o driver de volume persistente põe com o namespace do PVC
_TAGS_DO_NAMESPACE = ("kubernetes.io/created-for/pvc/namespace",)

_GENERICOS = frozenset({"sm", "secret", "secrets", "repo", "repository", "ecr", "image", "img"})
_AMBIENTES = frozenset(
    {"prd", "prod", "production", "dev", "develop", "hml", "homolog", "stg", "stage", "staging"}
    | {"qa", "test", "uat"}
)
_SEPARADOR = re.compile(r"[-_/.\s]+")
# Fração dos nomes de imagem que precisam começar igual para o prefixo sair. Alta de
# propósito: tira a organização (`ghnetsoft-`), não a linha de produto (`erp-`, `kit-conta-`)
_PREFIXO_COMUM = 0.8
_MINIMO_PARA_PREFIXO = 3


@dataclass(frozen=True, slots=True)
class CargaProvavel:
    nome: str
    fontes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CargasDoCluster:
    cluster: str
    cargas: tuple[CargaProvavel, ...]
    namespaces: tuple[str, ...]


def cargas_no_cluster(
    inventario: Inventario, analise: AnaliseDeTopologia
) -> dict[str, CargasDoCluster]:
    """Unidade com cluster → as prováveis aplicações dele."""
    recursos = {r.chave: r for r in inventario.recursos}
    resultado: dict[str, CargasDoCluster] = {}
    for unidade in analise.unidades:
        clusters = [
            recursos[c]
            for c in unidade.recursos
            if c in recursos and recursos[c].tipo is TipoDeRecurso.CLUSTER_KUBERNETES
        ]
        if not clusters:
            continue
        cluster = clusters[0]
        rastros = list(_rastros(inventario.recursos, cluster, unidade.id, analise.unidade_de))
        namespaces = sorted(
            {
                v
                for r in inventario.recursos
                if r.conta == cluster.conta
                for v in (
                    r.atributos.get("namespace_kubernetes"),
                    *(r.tags.get(k) for k in _TAGS_DO_NAMESPACE),
                )
                if v
            }
            | {ns for _, fonte, ns in rastros if fonte == BALANCEADOR and ns}
        )
        cargas = _agrupar([(nome, fonte) for nome, fonte, _ in rastros])
        if cargas or namespaces:
            resultado[unidade.id] = CargasDoCluster(
                cluster=cluster.nome or cluster.chave, cargas=cargas, namespaces=tuple(namespaces)
            )
    return resultado


def _rastros(
    recursos: Iterable[Recurso], cluster: Recurso, unidade: str, unidade_de: Mapping[str, str]
) -> Iterable[tuple[str, str, str | None]]:
    """(nome, fonte, namespace) de cada rastro da mesma conta, fora de outra aplicação."""
    for r in recursos:
        if r.conta != cluster.conta or unidade_de.get(r.chave, unidade) != unidade:
            continue
        fonte = _FONTE_DO_COMPONENTE.get(str(r.atributos.get("componente") or ""))
        if fonte:
            yield r.nome or r.chave.rsplit("/", 1)[-1], fonte, None
        # O normalizador de cada nuvem traduz a marca dela para `servico_kubernetes`
        # (`<namespace>/<service>`); a tag da AWS segue lida direto (inventários antigos)
        stacks = [str(v) for v in [r.atributos.get("servico_kubernetes")] if v]
        stacks += [v for k in _TAGS_DO_SERVICE if (v := r.tags.get(k))]
        for stack in dict.fromkeys(stacks):
            namespace, _, servico = stack.partition("/")
            yield _sem_namespace(servico or namespace, namespace), BALANCEADOR, namespace


def _sem_namespace(servico: str, namespace: str) -> str:
    """`kong-kong-proxy` no namespace `kong` → `kong-proxy` (o Helm repete o release)."""
    partes = _tokens(servico)
    while len(partes) > 1 and partes[0] == namespace and partes[1] == namespace:
        partes = partes[1:]
    return "-".join(partes)


def _tokens(nome: str) -> list[str]:
    partes = [p for p in _SEPARADOR.split(nome.lower()) if p]
    while partes and partes[0] in _GENERICOS:
        partes = partes[1:]
    while partes and partes[-1] in _AMBIENTES:
        partes = partes[:-1]
    return partes


def _agrupar(rastros: list[tuple[str, str]]) -> tuple[CargaProvavel, ...]:
    tokens = [(_tokens(nome), fonte) for nome, fonte in rastros]
    tokens = [(t, f) for t, f in tokens if t]
    imagens = [t for t, f in tokens if f == IMAGEM]
    prefixo = _prefixo_comum(imagens or [t for t, f in tokens if f == SEGREDO])
    fontes: dict[str, set[str]] = {}
    for t, fonte in tokens:
        curto = (
            t[len(prefixo) :]
            if prefixo and t[: len(prefixo)] == prefixo and len(t) > len(prefixo)
            else t
        )
        fontes.setdefault("-".join(curto), set()).add(fonte)
    # O nome curto do service discovery (`empresa`) é o fim do nome da imagem
    # (`kit-conta-empresa`): é a mesma carga
    for curto in [n for n, f in fontes.items() if f <= {DESCOBERTA, BALANCEADOR}]:
        longos = [n for n in fontes if n != curto and n.endswith(f"-{curto}")]
        if len(longos) == 1:
            fontes[longos[0]] |= fontes.pop(curto)
    cargas = [CargaProvavel(n, tuple(sorted(f))) for n, f in fontes.items() if f & _BASTA]
    return tuple(sorted(cargas, key=lambda c: (-len(c.fontes), c.nome)))


def _prefixo_comum(nomes: list[list[str]]) -> list[str]:
    """O prefixo mais longo que ao menos 80% dos nomes compartilham."""
    if len(nomes) < _MINIMO_PARA_PREFIXO:
        return []
    contagem: Counter[tuple[str, ...]] = Counter()
    for t in nomes:
        for k in range(1, len(t)):
            contagem[tuple(t[:k])] += 1
    minimo = max(2, _PREFIXO_COMUM * len(nomes))
    candidatos = [p for p, n in contagem.items() if n >= minimo]
    return list(max(candidatos, key=len)) if candidatos else []
