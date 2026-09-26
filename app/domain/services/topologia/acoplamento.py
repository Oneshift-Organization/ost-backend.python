"""Grupos de movimentação (RI7).

Dependência forte junta unidades num grupo: acesso a banco entre nuvens é
latência em cada consulta, e balanceador numa nuvem com as máquinas em outra
não funciona. Antes de juntar, três filtros — sem eles, qualquer ferramenta
compartilhada (bastion, monitoramento, ALB por roteamento de caminho) colapsa o
ambiente inteiro num grupo só, e o plano de ondas deixa de dizer alguma coisa.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from app.domain.entities.topologia import DADOS
from app.domain.services.grafo import UniaoBusca
from app.domain.services.topologia.dependencias import Achado
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import (
    Acoplamento,
    Dependencia,
    Natureza,
    Unidade,
)

FERRAMENTA_MINIMO_DE_ALVOS = 2
BALANCEADOR_COMPARTILHADO_MINIMO = 2
HUB_MINIMO_DE_ALVOS = 3
GRUPO_GRANDE_UNIDADES = 5
GRUPO_GRANDE_FRACAO = 0.4


@dataclass(frozen=True, slots=True)
class BancoCompartilhado:
    unidade: str  # a unidade onde o banco está
    bancos: tuple[str, ...]  # os recursos de banco/cache
    consumidores: tuple[str, ...]  # unidades de fora que acessam


@dataclass(frozen=True, slots=True)
class ResultadoDoAcoplamento:
    dependencias: tuple[Dependencia, ...]
    ferramentas: tuple[str, ...]
    grupos: Mapping[str, tuple[str, ...]]
    grupo_de: Mapping[str, str]
    achados: tuple[Achado, ...]
    compartilhados: tuple[BancoCompartilhado, ...]
    grandes: tuple[str, ...]


def agrupar(
    indice: Indice,
    *,
    unidades: Mapping[str, Unidade],
    unidade_de: Mapping[str, str],
    dependencias: Iterable[Dependencia],
    destinos_do_balanceador: Mapping[str, frozenset[str]],
    ferramentas_declaradas: Iterable[str],
) -> ResultadoDoAcoplamento:
    deps = list(dependencias)
    achados: list[Achado] = []
    ferramentas = _ferramentas(deps, unidades, ferramentas_declaradas, achados)
    deps = _rebaixar_balanceadores(deps, unidade_de, destinos_do_balanceador, achados)
    deps = _rebaixar_hubs(deps, unidades, ferramentas, achados)

    # 4. O que sobrou de forte junta.
    uniao = UniaoBusca(unidades)
    for d in deps:
        if (
            d.acoplamento is Acoplamento.FORTE
            and d.de not in ferramentas
            and d.para not in ferramentas
        ):
            uniao.unir(d.de, d.para)
    grupos = uniao.conjuntos()
    grupo_de = {u: g for g, membros in grupos.items() for u in membros}

    compartilhados = _bancos_compartilhados(indice, unidades, deps, ferramentas)
    fora = [g for g, m in grupos.items() if not set(m) <= ferramentas]
    total = sum(len(grupos[g]) for g in fora)
    grandes = tuple(
        g
        for g in fora
        if len(grupos[g]) >= GRUPO_GRANDE_UNIDADES and len(grupos[g]) >= GRUPO_GRANDE_FRACAO * total
    )
    return ResultadoDoAcoplamento(
        dependencias=tuple(deps),
        ferramentas=tuple(sorted(ferramentas)),
        grupos=grupos,
        grupo_de=dict(sorted(grupo_de.items())),
        achados=tuple(achados),
        compartilhados=compartilhados,
        grandes=grandes,
    )


def _ferramentas(
    deps: list[Dependencia],
    unidades: Mapping[str, Unidade],
    declaradas: Iterable[str],
    achados: list[Achado],
) -> set[str]:
    """1. Ferramenta de administração: a porta denuncia (ou o cliente declara)."""
    alvos_admin: dict[str, set[str]] = defaultdict(set)
    for d in deps:
        if d.natureza is Natureza.ADMINISTRATIVO:
            alvos_admin[d.de].add(d.para)
    ferramentas = {
        u for u, alvos in alvos_admin.items() if len(alvos) >= FERRAMENTA_MINIMO_DE_ALVOS
    }
    for u in sorted(ferramentas):
        achados.append(
            Achado(
                "ferramenta_administrativa",
                u,
                f"acesso administrativo a {len(alvos_admin[u])} unidades",
            )
        )
    for u in sorted(set(declaradas) - ferramentas):
        if u in unidades:
            ferramentas.add(u)
            achados.append(Achado("ferramenta_administrativa", u, "declarada pelo cliente"))
    return ferramentas


def _rebaixar_balanceadores(
    deps: list[Dependencia],
    unidade_de: Mapping[str, str],
    destinos_do_balanceador: Mapping[str, frozenset[str]],
    achados: list[Achado],
) -> list[Dependencia]:
    """2. Balanceador compartilhado: conta as unidades de destino, inclusive a dele."""
    compartilhado: set[str] = set()
    for lb, destinos in destinos_do_balanceador.items():
        dona = unidade_de.get(lb)
        if dona is not None and len(destinos) >= BALANCEADOR_COMPARTILHADO_MINIMO:
            compartilhado.add(dona)
            achados.append(
                Achado("balanceador_compartilhado", lb, f"encaminha para {len(destinos)} unidades")
            )
    return [
        replace(d, acoplamento=Acoplamento.FRACO, rebaixada="balanceador_compartilhado")
        if d.natureza is Natureza.ENCAMINHAMENTO
        and d.de in compartilhado
        and d.acoplamento is Acoplamento.FORTE
        else d
        for d in deps
    ]


def _rebaixar_hubs(
    deps: list[Dependencia],
    unidades: Mapping[str, Unidade],
    ferramentas: set[str],
    achados: list[Achado],
) -> list[Dependencia]:
    """3. Hub de dados: acesso a dados de muitas outras aplicações."""
    consumidores: dict[str, set[str]] = defaultdict(set)
    for d in deps:
        if _dados_forte(d) and d.de not in ferramentas:
            consumidores[d.para].add(d.de)
    hubs: set[str] = set()
    for u in sorted({d.de for d in deps if _dados_forte(d)} - ferramentas):
        alvos = {
            d.para for d in deps if d.de == u and _dados_forte(d) and d.para not in ferramentas
        }
        contam = [v for v in alvos if unidades[v].aplicacao_dentro or (consumidores[v] - {u})]
        if len(contam) >= HUB_MINIMO_DE_ALVOS:
            hubs.add(u)
            achados.append(Achado("hub_de_dados", u, f"acessa dados de {len(contam)} unidades"))
    return [
        replace(d, acoplamento=Acoplamento.FRACO, rebaixada="hub_de_dados")
        if d.de in hubs and _dados_forte(d)
        else d
        for d in deps
    ]


def _dados_forte(d: Dependencia) -> bool:
    return d.natureza is Natureza.DADOS and d.acoplamento is Acoplamento.FORTE


def _bancos_compartilhados(
    indice: Indice,
    unidades: Mapping[str, Unidade],
    deps: list[Dependencia],
    ferramentas: set[str],
) -> tuple[BancoCompartilhado, ...]:
    """Banco que serve 2 ou mais aplicações: os consumidores de fora, mais a
    aplicação dona quando o banco está na unidade dela (RI7)."""
    de_fora: dict[str, set[str]] = defaultdict(set)
    for d in deps:
        if _dados_forte(d) and d.de not in ferramentas and d.para not in ferramentas:
            de_fora[d.para].add(d.de)
    resultado = []
    for v, consumidores in sorted(de_fora.items()):
        bancos = tuple(r for r in unidades[v].recursos if indice.tipo(r) in DADOS)
        if not bancos:
            continue
        # O banco tem dono quando está numa unidade de aplicação, ou quando a tag
        # de aplicação diz de quem ele é: aí um consumidor de fora já é o segundo.
        dono = unidades[v].aplicacao_dentro or unidades[v].aplicacao is not None
        total = len(consumidores) + (1 if dono else 0)
        if total >= 2:  # noqa: PLR2004 - "compartilhado" é servir a duas ou mais
            resultado.append(BancoCompartilhado(v, bancos, tuple(sorted(consumidores))))
    return tuple(resultado)
