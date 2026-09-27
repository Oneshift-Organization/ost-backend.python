"""Inventários pequenos, escritos à mão, para testar uma regra de cada vez."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.domain.entities.topologia import (
    Acao,
    Direcao,
    EstadoDoRecurso,
    Inventario,
    Recurso,
    RegraDeRede,
    Relacao,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)

T = TipoDeRecurso
R = TipoDeRelacao


def recurso(
    chave: str,
    tipo: TipoDeRecurso,
    *,
    tags: dict[str, str] | None = None,
    estado: EstadoDoRecurso = EstadoDoRecurso.ATIVO,
    conta: str = "c1",
    regiao: str = "r1",
    **atributos: Any,
) -> Recurso:
    return Recurso(
        chave=chave,
        tipo=tipo,
        tipo_nativo=tipo.value,
        nome=chave,
        conta=conta,
        regiao=regiao,
        estado=estado,
        atributos=atributos,
        tags=tags or {},
        apelidos=frozenset({chave}),
    )


def vm(chave: str, **kw: Any) -> Recurso:
    return recurso(chave, T.INSTANCIA, **kw)


def banco(chave: str, **kw: Any) -> Recurso:
    return recurso(chave, T.BANCO, **kw)


def lb(chave: str, **kw: Any) -> Recurso:
    return recurso(chave, T.BALANCEADOR, **kw)


def sg(chave: str, **kw: Any) -> Recurso:
    return recurso(chave, T.GRUPO_SEGURANCA, **kw)


def subrede(chave: str, cidr: str, **kw: Any) -> Recurso:
    return recurso(chave, T.SUBREDE, cidr=cidr, **kw)


def rel(de: str, para: str, tipo: TipoDeRelacao) -> Relacao:
    return Relacao(de=de, para=para, tipo=tipo)


def protege(grupo: str, *membros: str) -> list[Relacao]:
    return [rel(m, grupo, R.PROTEGIDO_POR) for m in membros]


def regra(
    grupo: str,
    porta: int | None = None,
    *,
    de_grupo: str | None = None,
    de_cidr: str | None = None,
    internet: bool = False,
    protocolo: str = "tcp",
    fim: int | None = None,
    destino: Seletor | None = None,
    direcao: Direcao = Direcao.ENTRADA,
    acao: Acao = Acao.PERMITIR,
) -> RegraDeRede:
    if de_grupo is not None:
        origem = Seletor(TipoDeSeletor.GRUPO, de_grupo)
    elif de_cidr is not None:
        origem = Seletor(TipoDeSeletor.CIDR, de_cidr)
    elif internet:
        origem = Seletor(TipoDeSeletor.QUALQUER)
    else:
        origem = Seletor(TipoDeSeletor.SERVICO, "x")
    return RegraDeRede(
        grupo=grupo,
        direcao=direcao,
        acao=acao,
        protocolo=protocolo,
        origem=origem,
        porta_inicio=porta,
        porta_fim=fim if fim is not None else porta,
        destino=destino,
    )


def inventario(
    recursos: Iterable[Recurso],
    relacoes: Iterable[Relacao] = (),
    regras: Iterable[RegraDeRede] = (),
) -> Inventario:
    return Inventario(
        provedor="aws",
        recursos=tuple(sorted(recursos, key=lambda r: r.chave)),
        relacoes=tuple(relacoes),
        regras=tuple(regras),
    )


def camadas_app_banco(
    app: str, *, tags: bool = True, sufixo: str = ""
) -> tuple[list[Recurso], list[Relacao]]:
    """Uma aplicação com uma VM, um banco e os grupos `app` e `dados`."""
    t = {"app": app} if tags else {}
    recursos = [
        vm(f"vm-{app}{sufixo}", tags=t),
        banco(f"db-{app}{sufixo}", tags=t, alta_disponibilidade=False),
        sg(f"sg-{app}-app{sufixo}"),
        sg(f"sg-{app}-dados{sufixo}"),
    ]
    relacoes = [
        *protege(f"sg-{app}-app{sufixo}", f"vm-{app}{sufixo}"),
        *protege(f"sg-{app}-dados{sufixo}", f"db-{app}{sufixo}"),
    ]
    return recursos, relacoes
