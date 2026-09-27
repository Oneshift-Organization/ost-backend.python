"""Algoritmos de grafo da topologia (PLANO_INVENTARIO_API, RI6 a RI10).

Três ferramentas, todas determinísticas — a mesma entrada dá a mesma saída em
qualquer ordem de chegada, porque unidades, grupos e ondas viram contrato de
API e artefato de Terraform:

- ``UniaoBusca``: junta recursos em unidades e unidades em grupos. O
  representante de cada conjunto é a menor chave, não a primeira que chegou.
- ``condensar``: componentes fortemente conexos (Tarjan, iterativo para não
  estourar a pilha com milhares de nós) e o grafo acíclico entre eles.
- ``camadas`` e ``dependentes_transitivos``: a onda de cada nó e o raio de
  impacto.

Convenção de aresta em todo o módulo: ``(u, v)`` = **u depende de v**. Quem é
chamado fica numa camada menor que quem chama.

Módulo puro, sem I/O.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


class UniaoBusca:
    """Union-find com representante determinístico (a menor chave)."""

    def __init__(self, elementos: Iterable[str] = ()) -> None:
        self._pai: dict[str, str] = {}
        for e in elementos:
            self.adicionar(e)

    def adicionar(self, elemento: str) -> None:
        self._pai.setdefault(elemento, elemento)

    def __contains__(self, elemento: object) -> bool:
        return elemento in self._pai

    def raiz(self, elemento: str) -> str:
        raiz = elemento
        while self._pai[raiz] != raiz:
            raiz = self._pai[raiz]
        # Compressão de caminho: a próxima busca é direta.
        while self._pai[elemento] != raiz:
            self._pai[elemento], elemento = raiz, self._pai[elemento]
        return raiz

    def unir(self, a: str, b: str) -> str:
        self.adicionar(a)
        self.adicionar(b)
        ra, rb = self.raiz(a), self.raiz(b)
        if ra == rb:
            return ra
        # A menor chave vence: o representante não depende da ordem das uniões.
        menor, maior = (ra, rb) if ra < rb else (rb, ra)
        self._pai[maior] = menor
        return menor

    def conjuntos(self) -> dict[str, tuple[str, ...]]:
        """Representante → membros ordenados. Ordem das chaves: representante."""
        grupos: dict[str, list[str]] = {}
        for e in self._pai:
            grupos.setdefault(self.raiz(e), []).append(e)
        return {r: tuple(sorted(m)) for r, m in sorted(grupos.items())}


@dataclass(frozen=True, slots=True)
class Condensacao:
    """Componentes fortemente conexos e o grafo acíclico entre eles.

    ``componentes[i]`` são os nós do componente ``i``, ordenados; os índices
    seguem a ordem do menor nó de cada componente. ``arestas`` liga índices de
    componentes diferentes e segue a mesma convenção (``i`` depende de ``j``).
    """

    componentes: tuple[tuple[str, ...], ...]
    componente_de: Mapping[str, int]
    arestas: frozenset[tuple[int, int]]


def _adjacencia(nos: Iterable[str], arestas: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    adj: dict[str, set[str]] = {n: set() for n in nos}
    for u, v in arestas:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set())
    return {n: sorted(adj[n]) for n in sorted(adj)}


def condensar(nos: Iterable[str], arestas: Iterable[tuple[str, str]]) -> Condensacao:
    """Tarjan iterativo. Autolaço não conta como ciclo entre nós diferentes."""
    adj = _adjacencia(nos, arestas)
    indice: dict[str, int] = {}
    menor: dict[str, int] = {}
    na_pilha: set[str] = set()
    pilha: list[str] = []
    brutos: list[tuple[str, ...]] = []
    contador = 0

    for inicio in adj:
        if inicio in indice:
            continue
        # Cada quadro: (nó, próximo vizinho a visitar).
        quadros: list[tuple[str, int]] = [(inicio, 0)]
        indice[inicio] = menor[inicio] = contador
        contador += 1
        pilha.append(inicio)
        na_pilha.add(inicio)
        while quadros:
            no, i = quadros[-1]
            vizinhos = adj[no]
            if i < len(vizinhos):
                quadros[-1] = (no, i + 1)
                w = vizinhos[i]
                if w not in indice:
                    indice[w] = menor[w] = contador
                    contador += 1
                    pilha.append(w)
                    na_pilha.add(w)
                    quadros.append((w, 0))
                elif w in na_pilha:
                    menor[no] = min(menor[no], indice[w])
                continue
            quadros.pop()
            if quadros:
                pai = quadros[-1][0]
                menor[pai] = min(menor[pai], menor[no])
            if menor[no] == indice[no]:
                componente: list[str] = []
                while True:
                    w = pilha.pop()
                    na_pilha.discard(w)
                    componente.append(w)
                    if w == no:
                        break
                brutos.append(tuple(sorted(componente)))

    componentes = tuple(sorted(brutos))
    componente_de = {n: i for i, comp in enumerate(componentes) for n in comp}
    entre = frozenset(
        (componente_de[u], componente_de[v])
        for u, vizinhos in adj.items()
        for v in vizinhos
        if componente_de[u] != componente_de[v]
    )
    return Condensacao(componentes=componentes, componente_de=componente_de, arestas=entre)


def _ordem_topologica(n: int, arestas: frozenset[tuple[int, int]]) -> list[int]:
    """Dependências antes de quem depende (Kahn, com desempate pelo índice)."""
    dependencias: dict[int, set[int]] = {i: set() for i in range(n)}
    dependentes: dict[int, set[int]] = {i: set() for i in range(n)}
    for u, v in arestas:
        dependencias[u].add(v)
        dependentes[v].add(u)
    faltam = {i: len(dependencias[i]) for i in range(n)}
    prontos = [i for i in range(n) if faltam[i] == 0]
    heapq.heapify(prontos)
    ordem: list[int] = []
    while prontos:
        atual = heapq.heappop(prontos)
        ordem.append(atual)
        for d in dependentes[atual]:
            faltam[d] -= 1
            if faltam[d] == 0:
                heapq.heappush(prontos, d)
    return ordem


def camadas(nos: Iterable[str], arestas: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Camada pelo caminho mais longo: ``1 + max(camada das dependências)``.

    Nó sem dependência fica na camada 1. Nós do mesmo ciclo ficam na mesma
    camada. É a regra da onda (RI8): "está na 3 porque depende de algo na 2".
    """
    lista = list(nos)
    todas = list(arestas)
    cond = condensar(lista, todas)
    n = len(cond.componentes)
    dependencias: dict[int, set[int]] = {i: set() for i in range(n)}
    for u, v in cond.arestas:
        dependencias[u].add(v)
    camada: dict[int, int] = {}
    for c in _ordem_topologica(n, cond.arestas):
        camada[c] = 1 + max((camada[d] for d in dependencias[c]), default=0)
    return {no: camada[cond.componente_de[no]] for no in sorted(cond.componente_de)}


def dependentes_transitivos(
    nos: Iterable[str], arestas: Iterable[tuple[str, str]]
) -> dict[str, frozenset[str]]:
    """Para cada nó, quem depende dele direta ou indiretamente (sem ele mesmo).

    É o raio de impacto (RI10) e a base da propagação de criticidade. Membros
    do mesmo ciclo dependem uns dos outros.
    """
    lista = list(nos)
    todas = list(arestas)
    cond = condensar(lista, todas)
    n = len(cond.componentes)
    dependentes: dict[int, set[int]] = {i: set() for i in range(n)}
    for u, v in cond.arestas:
        dependentes[v].add(u)
    # Ordem topológica invertida: quem depende é resolvido antes de quem é
    # dependido, e cada componente herda o fecho dos seus dependentes diretos.
    alcance: dict[int, frozenset[int]] = {}
    for c in reversed(_ordem_topologica(n, cond.arestas)):
        acumulado: set[int] = set()
        for d in dependentes[c]:
            acumulado.add(d)
            acumulado |= alcance[d]
        alcance[c] = frozenset(acumulado)
    resultado: dict[str, frozenset[str]] = {}
    for no in sorted(cond.componente_de):
        c = cond.componente_de[no]
        membros = {m for m in cond.componentes[c] if m != no}
        for d in alcance[c]:
            membros.update(cond.componentes[d])
        resultado[no] = frozenset(membros)
    return resultado
