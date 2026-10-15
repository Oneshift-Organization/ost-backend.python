"""Unidades: o menor conjunto que migra junto (RI6).

Primeiro os anexos (disco, NIC e IP vão com a VM; instâncias do mesmo cluster
de banco vão juntas). Depois, se houver tag de aplicação, os recursos da mesma
aplicação — na mesma conta, região e ambiente — viram uma unidade só.

A chave da tag vem da declaração ou é detectada: a candidata presente em mais
unidades, desde que em pelo menos metade delas. Primeiro as chaves de aplicação;
só sem nenhuma delas, as de projeto (conta com um produto só costuma marcar
``project`` e nada mais). A cobertura conta cada conjunto de anexos uma vez: um
cluster com a tag e 30 nós sem ela é uma unidade marcada, não 1 em 31. A escolha
sai como premissa: agrupar pela tag errada juntaria o que não tem relação.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from app.domain.entities.topologia import (
    ANEXO,
    DADOS,
    UNIDADE,
    Recurso,
    TipoDeRecurso,
    TipoDeRelacao,
)
from app.domain.services.grafo import UniaoBusca
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import Premissa, Unidade

CANDIDATAS_APLICACAO = (
    "app",
    "application",
    "aplicacao",
    "service",
    "servico",
    "workload",
    "system",
    "sistema",
)
# Segunda camada: só quando nenhuma chave de aplicação alcança a cobertura
CANDIDATAS_PROJETO = ("project", "projeto", "product", "produto")
CANDIDATAS_AMBIENTE = ("env", "environment", "ambiente", "stage")
_COBERTURA_MINIMA = 0.5


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def valor_da_tag(recurso: Recurso, chave: str | None) -> str | None:
    """Valor da tag sem diferenciar maiúsculas nem acento na chave."""
    if not chave:
        return None
    alvo = sem_acento(chave)
    for k, v in recurso.tags.items():
        if sem_acento(k) == alvo and str(v).strip():
            return str(v).strip()
    return None


@dataclass(frozen=True, slots=True)
class EscolhaDeChave:
    chave: str | None
    # "declarada" | "detectada" | "detectada_projeto" | "desligada" | "nao_detectada"
    origem: str
    cobertura: float


def escolher_chave(
    declarada: str | None,
    camadas: Iterable[Iterable[str]],
    conjuntos: list[list[Recurso]],
) -> EscolhaDeChave:
    """``conjuntos``: os recursos de papel unidade de cada conjunto de anexos. Um
    conjunto está marcado quando algum recurso dele tem a tag."""
    total = len(conjuntos)

    def presentes(chave: str) -> int:
        return sum(1 for c in conjuntos if any(valor_da_tag(r, chave) for r in c))

    if declarada == "":
        return EscolhaDeChave(None, "desligada", 0.0)
    if declarada:
        return EscolhaDeChave(
            declarada, "declarada", presentes(declarada) / total if total else 0.0
        )
    melhor_de_todas: tuple[int, str] | None = None
    for camada, candidatas in enumerate(camadas):
        melhor: tuple[int, str] | None = None
        for candidata in candidatas:
            n = presentes(candidata)
            if n and (melhor is None or n > melhor[0]):
                melhor = (n, candidata)
        if melhor is not None and total and melhor[0] / total >= _COBERTURA_MINIMA:
            origem = "detectada" if camada == 0 else "detectada_projeto"
            return EscolhaDeChave(melhor[1], origem, melhor[0] / total)
        if melhor is not None and (melhor_de_todas is None or melhor[0] > melhor_de_todas[0]):
            melhor_de_todas = melhor
    return EscolhaDeChave(
        None,
        "nao_detectada",
        (melhor_de_todas[0] / total) if melhor_de_todas and total else 0.0,
    )


@dataclass(frozen=True, slots=True)
class ResultadoDasUnidades:
    unidades: Mapping[str, Unidade]
    unidade_de: Mapping[str, str]
    aplicacao: EscolhaDeChave
    ambiente: EscolhaDeChave
    premissas: tuple[Premissa, ...]


def construir_unidades(
    indice: Indice, chave_aplicacao: str | None, chave_ambiente: str | None
) -> ResultadoDasUnidades:
    recursos = indice.recursos
    uniao = UniaoBusca(
        c
        for c, r in recursos.items()
        if r.tipo in UNIDADE or r.tipo in ANEXO or r.tipo is TipoDeRecurso.OUTRO
    )
    juntam = (TipoDeRelacao.ANEXADO_A, TipoDeRelacao.MEMBRO_DE)
    for rel in indice.inventario.relacoes:
        if rel.tipo in juntam and rel.de in uniao and rel.para in uniao:
            uniao.unir(rel.de, rel.para)

    de_unidade = [r for r in recursos.values() if r.tipo in UNIDADE]
    conjuntos = [
        principais
        for membros in uniao.conjuntos().values()
        if (principais := [recursos[m] for m in membros if recursos[m].tipo in UNIDADE])
    ]
    aplicacao = escolher_chave(
        chave_aplicacao, (CANDIDATAS_APLICACAO, CANDIDATAS_PROJETO), conjuntos
    )
    ambiente = escolher_chave(chave_ambiente, (CANDIDATAS_AMBIENTE,), conjuntos)
    if aplicacao.chave:
        primeiro_do_escopo: dict[tuple[str, str, str, str], str] = {}
        for r in sorted(de_unidade, key=lambda x: x.chave):
            app = valor_da_tag(r, aplicacao.chave)
            if not app:
                continue
            escopo = (r.conta or "", r.regiao or "", app, valor_da_tag(r, ambiente.chave) or "")
            if escopo in primeiro_do_escopo:
                uniao.unir(primeiro_do_escopo[escopo], r.chave)
            else:
                primeiro_do_escopo[escopo] = r.chave

    unidades: dict[str, Unidade] = {}
    unidade_de: dict[str, str] = {}
    for representante, membros in uniao.conjuntos().items():
        principais = [recursos[m] for m in membros if recursos[m].tipo in UNIDADE]
        if not principais:
            continue  # disco ou IP sozinho, cluster vazio: não é unidade
        unidade = _unidade(
            representante,
            membros=membros,
            principais=principais,
            recursos=recursos,
            aplicacao=aplicacao,
            ambiente=ambiente,
        )
        unidades[unidade.id] = unidade
        for m in membros:
            unidade_de[m] = unidade.id
    unidades = _nomes_unicos(unidades)

    premissas = [
        Premissa(
            campo="chave_aplicacao",
            valor=aplicacao.chave,
            base=_base_da_escolha("aplicação", aplicacao),
        ),
        Premissa(
            campo="chave_ambiente",
            valor=ambiente.chave,
            base=_base_da_escolha("ambiente", ambiente),
        ),
    ]
    return ResultadoDasUnidades(
        unidades=dict(sorted(unidades.items())),
        unidade_de=dict(sorted(unidade_de.items())),
        aplicacao=aplicacao,
        ambiente=ambiente,
        premissas=tuple(premissas),
    )


def _unidade(
    representante: str,
    *,
    membros: tuple[str, ...],
    principais: list[Recurso],
    recursos: Mapping[str, Recurso],
    aplicacao: EscolhaDeChave,
    ambiente: EscolhaDeChave,
) -> Unidade:
    apps = sorted({v for r in principais if (v := valor_da_tag(r, aplicacao.chave))})
    ambientes = sorted({v for r in principais if (v := valor_da_tag(r, ambiente.chave))})
    tipos = Counter(recursos[m].tipo.value for m in membros)
    app = apps[0] if len(apps) == 1 else None
    amb = ambientes[0] if len(ambientes) == 1 else None
    if app:
        nome = f"{app} ({amb})" if amb else app
    else:
        principal = min(principais, key=_preferencia_de_nome)
        nome = principal.nome or principal.chave.rsplit("/", 1)[-1]
    return Unidade(
        id=representante,
        nome=nome,
        recursos=membros,
        aplicacao=app,
        ambiente=amb,
        conta=_unico(r.conta for r in principais),
        regiao=_unico(r.regiao for r in principais),
        tipos=dict(sorted(tipos.items())),
        aplicacao_dentro=any(r.tipo not in DADOS for r in principais),
    )


# Sem aplicação, a unidade leva o nome do que a define: o cluster (não um nó dele),
# o banco, o balanceador; a máquina por último. Dentro do tipo, nome dado por gente
# antes de identificador gerado (`i-019711e3c383ca17a`).
_ORDEM_DO_NOME = {
    TipoDeRecurso.CLUSTER_KUBERNETES: 0,
    TipoDeRecurso.BANCO: 1,
    TipoDeRecurso.CACHE: 2,
    TipoDeRecurso.BALANCEADOR: 3,
    TipoDeRecurso.BUCKET: 4,
    TipoDeRecurso.INSTANCIA: 5,
}
_IDENTIFICADOR_GERADO = re.compile(r"^[a-z]{1,10}-[0-9a-f]{8,}$")


def _preferencia_de_nome(r: Recurso) -> tuple[int, bool, str]:
    gerado = not r.nome or bool(_IDENTIFICADOR_GERADO.match(r.nome))
    return (_ORDEM_DO_NOME.get(r.tipo, 9), gerado, r.chave)


def _nomes_unicos(unidades: dict[str, Unidade]) -> dict[str, Unidade]:
    """A mesma aplicação em duas regiões ou contas tem o mesmo nome: acrescenta
    região, depois conta, até o nome distinguir. Nome repetido na resposta
    faria o cliente confundir duas ondas."""
    for campo in ("regiao", "conta", "id"):
        contagem = Counter(u.nome for u in unidades.values())
        repetidos = {n for n, c in contagem.items() if c > 1}
        if not repetidos:
            break
        unidades = {
            i: replace(u, nome=f"{u.nome} · {getattr(u, campo) or '?'}")
            if u.nome in repetidos
            else u
            for i, u in unidades.items()
        }
    return unidades


def _unico(valores: Iterable[str | None]) -> str | None:
    distintos = {v for v in valores if v}
    return next(iter(distintos)) if len(distintos) == 1 else None


def _base_da_escolha(o_que: str, e: EscolhaDeChave) -> str:
    if e.origem == "desligada":
        return f"Agrupamento por {o_que} desligado na declaração."
    if e.origem == "declarada":
        return (
            f"Chave de {o_que} declarada pelo cliente; presente em {e.cobertura:.0%} dos recursos."
        )
    if e.origem == "detectada":
        return (
            f"Chave de {o_que} detectada nas tags: presente em {e.cobertura:.0%} das unidades. "
            "Declare outra se não for esta."
        )
    if e.origem == "detectada_projeto":
        return (
            f"Nenhuma tag de {o_que} nos recursos; usada a de projeto, presente em "
            f"{e.cobertura:.0%} das unidades. Cada projeto conta como uma {o_que}. "
            "Declare outra chave se não for esta."
        )
    return (
        f"Nenhuma tag de {o_que} presente em pelo menos metade das unidades: sem agrupamento "
        f"por {o_que}; cada máquina (com seus discos) é uma unidade."
    )
