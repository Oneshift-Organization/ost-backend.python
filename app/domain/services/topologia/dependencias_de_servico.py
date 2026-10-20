"""Dependências entre serviços da fatura (PLANO_ESCOPO_DE_MIGRACAO, E6).

A topologia liga **unidades**: dentro da mesma unidade (a VM e o banco da mesma
aplicação) a aresta é descartada, porque migram juntas de qualquer jeito. Isso
basta para formar grupos e ondas, mas deixa a pergunta "o EC2 depende do RDS?"
sem resposta: marcar o EC2 no plano não dizia nada do banco.

Aqui as mesmas regras de rede são lidas no nível do recurso e agregadas por
(unidade, serviço) dos dois lados. Nada muda em unidades, grupos, ondas nem no
Terraform: é uma leitura a mais do mesmo inventário.

Mesmos filtros da derivação entre unidades: regra ampla, grupo que libera os
próprios membros, origem fora do inventário e teto de pares ficam de fora;
porta só de administração não é dependência.

Função pura, sem I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from app.domain.entities.topologia import DADOS, Acao, Direcao, TipoDeRecurso, TipoDeRelacao
from app.domain.services.topologia.dependencias import (
    TETO_DE_PARES,
    Achado,
    _alvos,
    _fontes,
    chave_de_regra,
    libera_porta_de_banco,
    natureza_da_regra,
    rotulo_de_porta,
)
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import Natureza

# Mais forte primeiro, como na derivação entre unidades
_FORCA = {Natureza.ENCAMINHAMENTO: 3, Natureza.DADOS: 3, Natureza.SERVICO: 2}


@dataclass(frozen=True, slots=True)
class DependenciaDeServico:
    """O serviço ``de`` (na unidade ``unidade_de``) chama o ``para`` (na ``unidade_para``)."""

    unidade_de: str
    servico_de: str
    unidade_para: str
    servico_para: str
    natureza: Natureza
    portas: tuple[str, ...]

    @property
    def interna(self) -> bool:
        """Dentro da mesma unidade: a aresta que a topologia entre unidades descarta."""
        return self.unidade_de == self.unidade_para


Lado = tuple[str, str]  # (unidade, serviço)
_Par = tuple[Lado, Lado, Natureza, str | None]


def dependencias_de_servico(
    indice: Indice,
    unidade_de: Mapping[str, str],
    servico_de: Mapping[str, str],
) -> tuple[DependenciaDeServico, ...]:
    """``servico_de``: chave do recurso no inventário → serviço da fatura.

    Recurso sem serviço (não casou com nenhuma linha da fatura) não entra:
    a pergunta é entre serviços que o cliente paga.
    """

    def lado(chave: str) -> Lado | None:
        u, s = unidade_de.get(chave), servico_de.get(chave)
        return (u, s) if u is not None and s is not None else None

    arestas: dict[tuple[str, str, str, str], tuple[Natureza, set[str]]] = {}
    for a, b, natureza, porta in (
        *_pares_das_regras(indice, lado),
        *_encaminhamentos(indice, lado),
    ):
        if a == b:
            continue  # o mesmo serviço na mesma unidade não é dependência
        chave = (*a, *b)
        atual = arestas.get(chave)
        if atual is None or _FORCA[natureza] > _FORCA[atual[0]]:
            atual = arestas[chave] = (natureza, atual[1] if atual else set())
        if porta:
            atual[1].add(porta)

    return tuple(
        DependenciaDeServico(
            unidade_de=ud,
            servico_de=sd,
            unidade_para=up,
            servico_para=sp,
            natureza=natureza,
            portas=tuple(sorted(portas)),
        )
        for (ud, sd, up, sp), (natureza, portas) in sorted(arestas.items())
    )


def _pares_das_regras(indice: Indice, lado: Callable[[str], Lado | None]) -> Iterator[_Par]:
    """Cada regra de entrada que permite, lida por recurso e agregada por (unidade, serviço)."""
    internos: set[str] = set()
    descartados: list[Achado] = []
    for regra in sorted(indice.inventario.regras, key=chave_de_regra):
        if regra.direcao is not Direcao.ENTRADA or regra.acao is not Acao.PERMITIR:
            continue
        grupo = indice.recursos.get(regra.grupo)
        if grupo is None or grupo.atributos.get("amplo") or natureza_da_regra(regra) is not None:
            continue  # regra ampla, ou só administração: não restringe a migração
        destinos = {
            (b, indice.tipo(alvo) in DADOS)
            for alvo in _alvos(indice, regra)
            if (b := lado(alvo)) is not None
        }
        origens = {
            (a, indice.tipo(fonte) is TipoDeRecurso.BALANCEADOR)
            for fonte in _fontes(indice, regra, internos, descartados)
            if (a := lado(fonte)) is not None
        }
        if not destinos or not origens or len(destinos) * len(origens) > TETO_DE_PARES:
            continue
        porta = rotulo_de_porta(regra)
        de_banco = libera_porta_de_banco(regra)
        for b, b_dados in sorted(destinos):
            for a, a_balanceador in sorted(origens):
                if a_balanceador:
                    yield a, b, Natureza.ENCAMINHAMENTO, porta
                else:
                    yield a, b, Natureza.DADOS if b_dados or de_banco else Natureza.SERVICO, porta


def _encaminhamentos(indice: Indice, lado: Callable[[str], Lado | None]) -> Iterator[_Par]:
    """Encaminhamento dito pelo provedor (backend pool, ELB clássico)."""
    for rel in sorted(indice.inventario.relacoes, key=lambda r: (r.de, r.para)):
        if rel.tipo is not TipoDeRelacao.ENCAMINHA_PARA or (a := lado(rel.de)) is None:
            continue
        alvos = (
            set(indice.donos(rel.para))
            if indice.tipo(rel.para) is TipoDeRecurso.INTERFACE_REDE
            else {rel.para}
        )
        for alvo in sorted(alvos):
            if (b := lado(alvo)) is not None:
                yield a, b, Natureza.ENCAMINHAMENTO, None


def agrupar_por_par(
    deps: tuple[DependenciaDeServico, ...],
) -> dict[tuple[str, str], tuple[Natureza, tuple[str, ...], frozenset[str]]]:
    """(serviço que chama, serviço chamado) → natureza mais forte, portas, unidades."""
    por_par: dict[tuple[str, str], list[DependenciaDeServico]] = defaultdict(list)
    for d in deps:
        if d.servico_de != d.servico_para:
            por_par[(d.servico_de, d.servico_para)].append(d)
    return {
        par: (
            max((d.natureza for d in ds), key=lambda n: _FORCA[n]),
            tuple(sorted({p for d in ds for p in d.portas})),
            frozenset(d.unidade_de for d in ds),
        )
        for par, ds in sorted(por_par.items())
    }
