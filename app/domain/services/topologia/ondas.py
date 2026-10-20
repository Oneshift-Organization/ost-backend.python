"""Ondas de migração (RI8 e RI9).

Sobre os grupos, com as dependências fracas entre grupos diferentes:
``onda(G) = 1 + max(onda das dependências de G)``, e 1 sem dependência. Quem é
chamado migra antes de quem chama (D3). Ciclo migra na mesma onda.

Grupo sem nenhuma dependência, nem de nem para, não recebe número: pode ir a
qualquer momento, e colocá-lo na onda 1 inflaria a primeira onda com o que não
tem restrição nenhuma.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from app.domain.services.grafo import camadas, condensar
from app.domain.services.topologia.modelo import (
    Acoplamento,
    Dependencia,
    DependenciaEntreOndas,
    Evidencia,
    Onda,
)

_MAX_EVIDENCIAS = 5


@dataclass(frozen=True, slots=True)
class ResultadoDasOndas:
    ondas: tuple[Onda, ...]
    independentes: tuple[str, ...]
    onda_de: Mapping[str, int]
    # (G, D) = o grupo G depende do grupo D
    arestas: Mapping[tuple[str, str], tuple[Dependencia, ...]]
    ciclos: tuple[tuple[str, ...], ...]
    entre_ondas: tuple[DependenciaEntreOndas, ...]


def arestas_entre_grupos(
    dependencias: Iterable[Dependencia],
    grupo_de: Mapping[str, str],
    ferramentas: Iterable[str],
) -> dict[tuple[str, str], tuple[Dependencia, ...]]:
    fora = set(ferramentas)
    por_par: dict[tuple[str, str], list[Dependencia]] = defaultdict(list)
    for d in dependencias:
        if d.de in fora or d.para in fora or d.acoplamento is not Acoplamento.FRACO:
            continue
        g, h = grupo_de[d.de], grupo_de[d.para]
        if g != h:
            por_par[(g, h)].append(d)
    return {par: tuple(deps) for par, deps in sorted(por_par.items())}


def calcular_ondas(
    grupos: Iterable[str],
    arestas: Mapping[tuple[str, str], tuple[Dependencia, ...]],
    ordem_de_exibicao: Callable[[str], tuple[object, ...]],
) -> ResultadoDasOndas:
    participantes = sorted({g for par in arestas for g in par})
    independentes = tuple(sorted(set(grupos) - set(participantes), key=ordem_de_exibicao))
    camada = camadas(participantes, list(arestas))
    por_onda: dict[int, list[str]] = defaultdict(list)
    for g, n in camada.items():
        por_onda[n].append(g)
    ondas = tuple(
        Onda(numero=n, grupos=tuple(sorted(gs, key=ordem_de_exibicao)))
        for n, gs in sorted(por_onda.items())
    )
    ciclos = tuple(c for c in condensar(participantes, list(arestas)).componentes if len(c) > 1)
    entre = []
    for (g, h), deps in arestas.items():
        if camada[g] > camada[h]:
            evidencias: list[Evidencia] = []
            for d in deps:
                for e in d.evidencias:
                    if e not in evidencias and len(evidencias) < _MAX_EVIDENCIAS:
                        evidencias.append(e)
            entre.append(
                DependenciaEntreOndas(
                    de_grupo=g,
                    para_grupo=h,
                    onda_de=camada[g],
                    onda_para=camada[h],
                    portas=tuple(sorted({p for d in deps for p in d.portas})),
                    evidencias=tuple(evidencias),
                )
            )
    return ResultadoDasOndas(
        ondas=ondas,
        independentes=independentes,
        onda_de=camada,
        arestas=arestas,
        ciclos=ciclos,
        entre_ondas=tuple(entre),
    )
