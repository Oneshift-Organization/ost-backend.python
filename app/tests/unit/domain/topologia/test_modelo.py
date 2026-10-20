"""Modelo canônico: regra de rede e papéis dos tipos (I1)."""

from __future__ import annotations

import pytest

from app.domain.entities.topologia import (
    ANEXO,
    DADOS,
    FUNDACAO,
    UNIDADE,
    Acao,
    Direcao,
    RegraDeRede,
    Seletor,
    TipoDeRecurso,
    TipoDeSeletor,
)

_QUALQUER = Seletor(TipoDeSeletor.QUALQUER)


def _regra(protocolo: str, inicio: int | None = None, fim: int | None = None) -> RegraDeRede:
    return RegraDeRede(
        grupo="sg-1",
        direcao=Direcao.ENTRADA,
        acao=Acao.PERMITIR,
        protocolo=protocolo,
        origem=_QUALQUER,
        porta_inicio=inicio,
        porta_fim=fim,
    )


@pytest.mark.parametrize(
    ("regra", "porta", "cobre"),
    [
        (_regra("tcp", 22, 22), 22, True),
        (_regra("tcp", 22, 22), 23, False),
        (_regra("tcp", 8000, 8100), 8080, True),
        (_regra("udp", 53, 53), 53, True),
        (_regra("tcp"), 5432, True),  # sem porta = todas
        (_regra("todos"), 3389, True),
        (_regra("icmp"), 22, False),  # ICMP não tem porta
        (_regra("50"), 22, False),  # ESP
        (_regra("tcp", None, 1024), 22, True),  # só o fim informado
    ],
)
def test_cobre_porta(regra: RegraDeRede, porta: int, cobre: bool) -> None:
    assert regra.cobre_porta(porta) is cobre


@pytest.mark.parametrize(
    ("regra", "todas"),
    [
        (_regra("todos"), True),
        (_regra("tcp"), True),
        (_regra("tcp", 0, 65535), True),
        (_regra("tcp", 1, 65535), False),
        (_regra("tcp", 22, 22), False),
        (_regra("icmp"), False),
    ],
)
def test_todas_as_portas(regra: RegraDeRede, todas: bool) -> None:
    assert regra.todas_as_portas is todas


def test_papeis_nao_se_sobrepoem_e_cobrem_quase_tudo() -> None:
    assert not (UNIDADE & ANEXO)
    assert not (UNIDADE & FUNDACAO)
    assert not (ANEXO & FUNDACAO)
    assert DADOS <= UNIDADE
    fora = set(TipoDeRecurso) - UNIDADE - ANEXO - FUNDACAO
    assert fora == {TipoDeRecurso.SNAPSHOT, TipoDeRecurso.OUTRO}
