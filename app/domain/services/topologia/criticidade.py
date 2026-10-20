"""Criticidade declarada e efetiva (RI10).

Nenhuma API de nuvem diz o que é crítico. Aqui só entra o que o cliente disse:
pela API (por recurso ou por aplicação) ou por uma tag de criticidade com
vocabulário fechado. Do mais específico para o mais geral: declaração do
recurso, tag do recurso, declaração da aplicação.

A efetiva acrescenta a herdada: quem sustenta algo crítico é crítico. Nunca vem
de custo, de nome nem de centralidade no grafo — o número de dependentes sai
como **raio de impacto**, com esse nome, justamente para não ser lido como
criticidade.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.services.grafo import dependentes_transitivos
from app.domain.services.topologia.declaracoes import Declaracoes
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import ORDEM_DO_NIVEL, Criticidade, Nivel, Unidade
from app.domain.services.topologia.unidades import sem_acento, valor_da_tag

CHAVES_DE_TAG = ("criticality", "criticidade", "business-criticality", "business_criticality")
_VOCABULARIO: Mapping[str, Nivel] = {
    **dict.fromkeys(
        ("alta", "alto", "high", "critical", "critica", "critico", "tier1", "tier-1", "p1"),
        Nivel.ALTA,
    ),
    **dict.fromkeys(
        ("media", "medio", "medium", "moderate", "moderada", "tier2", "tier-2", "p2"),
        Nivel.MEDIA,
    ),
    **dict.fromkeys(("baixa", "baixo", "low", "tier3", "tier-3", "p3"), Nivel.BAIXA),
}


@dataclass(frozen=True, slots=True)
class Declarada:
    nivel: Nivel
    origem: str


@dataclass(frozen=True, slots=True)
class CriticidadePorRecurso:
    por_recurso: Mapping[str, Declarada]
    contagens: Mapping[str, int]


def declarada_por_recurso(
    indice: Indice,
    declaracoes: Declaracoes,
    chave_aplicacao: str | None,
) -> CriticidadePorRecurso:
    contagens: Counter[str] = Counter()
    resultado: dict[str, Declarada] = {}
    for chave, recurso in indice.recursos.items():
        if chave in declaracoes.criticidade_recursos:
            resultado[chave] = Declarada(declaracoes.criticidade_recursos[chave], "declarada")
            continue
        tag = next(((k, v) for k in CHAVES_DE_TAG if (v := valor_da_tag(recurso, k))), None)
        if tag is not None:
            nivel = _VOCABULARIO.get(sem_acento(tag[1]).replace(" ", ""))
            if nivel is not None:
                resultado[chave] = Declarada(nivel, f"tag:{tag[0]}={tag[1]}")
                continue
            contagens["criticidade_tag_fora_do_vocabulario"] += 1
        app = valor_da_tag(recurso, chave_aplicacao)
        if app and app in declaracoes.criticidade_aplicacoes:
            resultado[chave] = Declarada(
                declaracoes.criticidade_aplicacoes[app], f"declarada para a aplicação {app}"
            )
    if declaracoes.criticidade_aplicacoes and not chave_aplicacao:
        contagens["criticidade_por_aplicacao_sem_chave"] = len(declaracoes.criticidade_aplicacoes)
    return CriticidadePorRecurso(resultado, dict(contagens))


def maior(declaradas: Iterable[Declarada]) -> Declarada | None:
    """A maior; em empate, a de origem menor em ordem alfabética (estável)."""
    lista = sorted(declaradas, key=lambda d: (-ORDEM_DO_NIVEL[d.nivel], d.origem))
    return lista[0] if lista else None


@dataclass(frozen=True, slots=True)
class CriticidadeDosGrupos:
    por_grupo: Mapping[str, Criticidade]
    raio_de_impacto: Mapping[str, int]


def criticidade_dos_grupos(
    grupos: Mapping[str, tuple[str, ...]],
    unidades: Mapping[str, Unidade],
    por_recurso: Mapping[str, Declarada],
    arestas: Iterable[tuple[str, str]],
    nomes: Mapping[str, str],
) -> CriticidadeDosGrupos:
    declarada: dict[str, Declarada | None] = {
        g: maior(por_recurso[r] for u in membros for r in unidades[u].recursos if r in por_recurso)
        for g, membros in grupos.items()
    }
    dependentes = dependentes_transitivos(list(grupos), list(arestas))
    resultado: dict[str, Criticidade] = {}
    for g in grupos:
        propria = declarada[g]
        herdadas: list[tuple[Declarada, str]] = []
        for h in dependentes.get(g, frozenset()):
            dh = declarada[h]
            if dh is not None:
                herdadas.append((dh, h))
        herdadas.sort(key=lambda x: (-ORDEM_DO_NIVEL[x[0].nivel], nomes[x[1]]))
        melhor = herdadas[0] if herdadas else None
        if propria is not None and (
            melhor is None or ORDEM_DO_NIVEL[propria.nivel] >= ORDEM_DO_NIVEL[melhor[0].nivel]
        ):
            resultado[g] = Criticidade(propria.nivel, propria.nivel, propria.origem)
        elif melhor is not None:
            resultado[g] = Criticidade(
                propria.nivel if propria else None,
                melhor[0].nivel,
                f"herdada de {nomes[melhor[1]]}",
            )
        else:
            resultado[g] = Criticidade(None, None, "nao_declarada")
    return CriticidadeDosGrupos(
        por_grupo=resultado,
        raio_de_impacto={g: len(dependentes.get(g, frozenset())) for g in grupos},
    )
