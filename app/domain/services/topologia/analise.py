"""A análise de topologia inteira: inventário + declarações → resultado (RI5 a RI12).

Ordem das etapas, e por que ela importa:

1. unidades (anexos e tag de aplicação) — a dependência é entre unidades;
2. dependências (regras de rede, encaminhamento, declarações);
3. acoplamento (filtros de ferramenta, balanceador compartilhado e hub, e só
   então a junção pelo que é forte) — os filtros vêm antes, senão uma
   ferramenta junta o ambiente inteiro;
4. ondas sobre os grupos, com o que sobrou de fraco;
5. criticidade, que herda pelo mesmo grafo das ondas;
6. riscos e ociosos.

Função pura: sem I/O, sem relógio. O ``fingerprint`` cobre o inventário e as
declarações, não o resultado — quem congela uma análise guarda o fingerprint da
entrada que a produziu.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict

from app.domain.entities.topologia import FUNDACAO, Inventario
from app.domain.services.topologia.acoplamento import ResultadoDoAcoplamento, agrupar
from app.domain.services.topologia.criticidade import (
    criticidade_dos_grupos,
    declarada_por_recurso,
)
from app.domain.services.topologia.declaracoes import SEM_DECLARACOES, Declaracoes
from app.domain.services.topologia.dependencias import derivar_dependencias, resolver_referencia
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import (
    AnaliseDeTopologia,
    Dependencia,
    GrupoDeMovimentacao,
    Nivel,
    Ocioso,
    Premissa,
    Risco,
    Severidade,
    Unidade,
)
from app.domain.services.topologia.ondas import (
    ResultadoDasOndas,
    arestas_entre_grupos,
    calcular_ondas,
)
from app.domain.services.topologia.riscos import (
    ociosos,
    riscos_de_acoplamento,
    riscos_de_disponibilidade,
    riscos_de_exposicao,
    riscos_entre_ambientes,
    riscos_informativos,
)
from app.domain.services.topologia.unidades import construir_unidades

_MAX_NOMES = 3
# Ordem de exibição dentro da onda (RI8.4): baixa, média, não declarada, alta.
_RANK_DE_EXIBICAO = {Nivel.BAIXA: 0, Nivel.MEDIA: 1, None: 2, Nivel.ALTA: 3}
_ORDEM_DA_SEVERIDADE = {Severidade.ALTA: 0, Severidade.MEDIA: 1, Severidade.INFORMATIVA: 2}


def analisar_topologia(
    inventario: Inventario, declaracoes: Declaracoes = SEM_DECLARACOES
) -> AnaliseDeTopologia:
    indice = Indice(inventario)
    u = construir_unidades(indice, declaracoes.chave_aplicacao, declaracoes.chave_ambiente)
    chave_app = u.aplicacao.chave

    deps = derivar_dependencias(indice, u.unidades, u.unidade_de, declaracoes, chave_app)
    declaradas_como_ferramenta = [
        unidade
        for ref in declaracoes.ferramentas
        for unidade in resolver_referencia(ref, u.unidades, u.unidade_de, chave_app)
    ]
    ac = agrupar(
        indice,
        unidades=u.unidades,
        unidade_de=u.unidade_de,
        dependencias=deps.dependencias,
        destinos_do_balanceador=deps.destinos_do_balanceador,
        ferramentas_declaradas=declaradas_como_ferramenta,
    )

    nomes_de_grupo = {g: _nome_do_grupo(membros, u.unidades) for g, membros in ac.grupos.items()}
    arestas = arestas_entre_grupos(ac.dependencias, ac.grupo_de, ac.ferramentas)
    crit_rec = declarada_por_recurso(indice, declaracoes, chave_app)
    crit = criticidade_dos_grupos(
        ac.grupos, u.unidades, crit_rec.por_recurso, arestas, nomes_de_grupo
    )
    recursos_do_grupo = {
        g: sum(len(u.unidades[x].recursos) for x in membros) for g, membros in ac.grupos.items()
    }

    def exibicao(g: str) -> tuple[object, ...]:
        return (
            _RANK_DE_EXIBICAO[crit.por_grupo[g].efetiva],
            recursos_do_grupo[g],
            nomes_de_grupo[g],
            g,
        )

    grupos_nas_ondas = [
        g for g, membros in ac.grupos.items() if not set(membros) <= set(ac.ferramentas)
    ]
    ondas = calcular_ondas(grupos_nas_ondas, arestas, exibicao)
    depende_de: dict[str, set[str]] = {g: set() for g in ac.grupos}
    for g, h in arestas:
        depende_de[g].add(h)
    grupos = tuple(
        GrupoDeMovimentacao(
            id=g,
            nome=nomes_de_grupo[g],
            unidades=membros,
            recursos=recursos_do_grupo[g],
            criticidade=crit.por_grupo[g],
            raio_de_impacto=crit.raio_de_impacto[g],
            depende_de=tuple(sorted(depende_de[g])),
        )
        for g, membros in ac.grupos.items()
    )

    nomes_de_unidade = {i: un.nome for i, un in u.unidades.items()}
    riscos = [
        *riscos_de_exposicao(indice),
        *riscos_de_disponibilidade(
            indice,
            unidades=u.unidades,
            unidade_de=u.unidade_de,
            dependencias=ac.dependencias,
            ferramentas=ac.ferramentas,
            grupos=ac.grupos,
            criticidade=crit.por_grupo,
        ),
        *riscos_de_acoplamento(ac.compartilhados, ac.grandes, ac.grupos, nomes_de_unidade),
        *riscos_entre_ambientes(u.unidades, ac.dependencias, ac.ferramentas),
        *riscos_informativos(indice, [*deps.achados, *ac.achados], ondas.ciclos),
    ]
    riscos.sort(key=lambda r: (_ORDEM_DA_SEVERIDADE[r.severidade], r.regra, r.detalhe))

    premissas = list(u.premissas)
    if deps.ignoradas:
        premissas.append(
            Premissa(
                campo="dependencias_declaradas_ignoradas",
                valor=str(len(deps.ignoradas)),
                base="Não acharam unidade no inventário (ou ligariam escopos diferentes): "
                + "; ".join(deps.ignoradas[:5]),
            )
        )
    for motivo, n in sorted(crit_rec.contagens.items()):
        premissas.append(Premissa(campo=motivo, valor=str(n), base=_BASE_DE_CRITICIDADE[motivo]))
    premissas.append(
        Premissa(
            campo="dependencias",
            valor=None,
            base="Dependências vêm das regras de rede e do que foi declarado: o que a "
            "configuração permite, não o tráfego real.",
        )
    )

    lista_ociosos = ociosos(indice)
    resumo = _resumo(
        inventario,
        unidades=u.unidades,
        ac=ac,
        ondas=ondas,
        riscos=riscos,
        lista_ociosos=lista_ociosos,
        dependencias=deps.dependencias,
    )
    return AnaliseDeTopologia(
        unidades=tuple(u.unidades.values()),
        dependencias=ac.dependencias,
        grupos=grupos,
        ondas=ondas.ondas,
        independentes=ondas.independentes,
        ferramentas=ac.ferramentas,
        dependencias_entre_ondas=ondas.entre_ondas,
        riscos=tuple(riscos),
        ociosos=tuple(lista_ociosos),
        premissas=tuple(premissas),
        resumo=resumo,
        fingerprint=fingerprint(inventario, declaracoes),
        unidade_de=u.unidade_de,
        grupo_de=ac.grupo_de,
    )


_BASE_DE_CRITICIDADE = {
    "criticidade_tag_fora_do_vocabulario": "Tag de criticidade com valor fora do vocabulário "
    "(alta/média/baixa, high/medium/low, critical, tier1-3, p1-p3): ignorada.",
    "criticidade_por_aplicacao_sem_chave": "Criticidade declarada por aplicação, mas não há "
    "chave de aplicação ativa: declare `chave_aplicacao` ou declare por recurso.",
}


def _nome_do_grupo(membros: tuple[str, ...], unidades: Mapping[str, Unidade]) -> str:
    nomes = sorted({unidades[m].nome for m in membros})
    if len(nomes) <= _MAX_NOMES:
        return " + ".join(nomes)
    return " + ".join(nomes[:_MAX_NOMES]) + f" e mais {len(nomes) - _MAX_NOMES}"


def _resumo(
    inventario: Inventario,
    *,
    unidades: Mapping[str, Unidade],
    ac: ResultadoDoAcoplamento,
    ondas: ResultadoDasOndas,
    riscos: list[Risco],
    lista_ociosos: list[Ocioso],
    dependencias: tuple[Dependencia, ...],
) -> dict[str, object]:
    por_tipo = Counter(r.tipo.value for r in inventario.recursos)
    return {
        "recursos": len(inventario.recursos),
        "por_tipo": dict(sorted(por_tipo.items())),
        "fundacao": {t.value: por_tipo.get(t.value, 0) for t in sorted(FUNDACAO)},
        "unidades": len(unidades),
        "grupos": len(ac.grupos),
        "ondas": len(ondas.ondas),
        "independentes": len(ondas.independentes),
        "ferramentas": len(ac.ferramentas),
        "dependencias": dict(sorted(Counter(d.natureza.value for d in dependencias).items())),
        "dependencias_entre_ondas": len(ondas.entre_ondas),
        "riscos": {s.value: sum(1 for r in riscos if r.severidade is s) for s in Severidade},
        "ociosos": len(lista_ociosos),
    }


def fingerprint(inventario: Inventario, declaracoes: Declaracoes) -> str:
    """SHA-256 da entrada canônica. Mesmo inventário + mesmas declarações =
    mesmo valor, em qualquer ordem de chegada (RI16)."""
    conteudo = {
        "provedor": inventario.provedor,
        "recursos": [
            [
                r.chave,
                r.tipo.value,
                r.estado.value,
                r.conta,
                r.regiao,
                r.zona,
                dict(sorted(r.atributos.items())),
                dict(sorted(r.tags.items())),
            ]
            for r in sorted(inventario.recursos, key=lambda x: x.chave)
        ],
        "relacoes": sorted([r.de, r.para, r.tipo.value] for r in inventario.relacoes),
        "regras": sorted(
            json.dumps(asdict(r), sort_keys=True, default=str) for r in inventario.regras
        ),
        "declaracoes": {
            "chave_aplicacao": declaracoes.chave_aplicacao,
            "chave_ambiente": declaracoes.chave_ambiente,
            "criticidade_recursos": {
                k: v.value for k, v in sorted(declaracoes.criticidade_recursos.items())
            },
            "criticidade_aplicacoes": {
                k: v.value for k, v in sorted(declaracoes.criticidade_aplicacoes.items())
            },
            "dependencias": sorted(
                [d.de, d.para, d.acoplamento.value, d.motivo or ""]
                for d in declaracoes.dependencias
            ),
            "ferramentas": sorted(declaracoes.ferramentas),
        },
    }
    texto = json.dumps(conteudo, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(texto.encode()).hexdigest()
