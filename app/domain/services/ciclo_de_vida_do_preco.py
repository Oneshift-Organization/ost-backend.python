"""Ciclo de vida do preço: VIVO, MADURO, EM_DECOMPOSICAO e ADUBO (PLANO_CICLO_DE_VIDA_DO_PRECO).

Preço oficial não fica falso quando envelhece: fica possivelmente desatualizado. O
score de um preço é o peso da fonte que o confirmou vezes o decaimento pela idade
dessa confirmação (meia-vida da categoria). O estado sai do score pelos limiares.
Nenhum estado é terminal: uma confirmação nova (sync oficial; billing com o mesmo
preço de lista, na P3.3) volta o preço a VIVO.

O agente (IA) observa, mas não confirma. A resposta dele pode ser alucinação, então
uma observação do agente nunca revive um preço nem sobe o score. Quando o preço já
virou ADUBO, ela pode aparecer na tela ao lado dele, marcada como não verificada.

Meia-vida, limiares e peso de cada fonte vêm de tabela (`price_lifecycle_rules`,
`price_source_weights`), não de código. Módulo puro, sem I/O.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from fnmatch import fnmatchcase

_ZERO = Decimal(0)
_SCORE = Decimal("0.0001")


class Estado(StrEnum):
    VIVO = "vivo"
    MADURO = "maduro"
    EM_DECOMPOSICAO = "em_decomposicao"
    ADUBO = "adubo"


class Fonte(StrEnum):
    OFICIAL = "oficial"  # API de preço do provedor (sync)
    REFERENCIA = "referencia"  # valor conferido na página oficial e digitado no seed
    BILLING = "billing"  # preço de lista na fatura de um cliente (P3.3)
    AGENTE = "agente"  # resposta da IA: observa, não confirma


@dataclass(frozen=True, slots=True)
class RegraDoCiclo:
    meia_vida_dias: Decimal
    limiar_vivo: Decimal = Decimal("0.8")
    limiar_maduro: Decimal = Decimal("0.6")
    limiar_decomposicao: Decimal = Decimal("0.3")


@dataclass(frozen=True, slots=True)
class PesoDaFonte:
    peso: Decimal
    # A observação dessa fonte renova o preço. O agente não renova.
    confirma: bool


@dataclass(frozen=True, slots=True)
class Observacao:
    fonte: str
    preco: Decimal
    observado_em: datetime
    # Billing com Savings Plan, reserva, spot ou desconto é preço praticado, não de
    # tabela: nunca confirma o preço sob demanda
    sob_demanda: bool = True


@dataclass(frozen=True, slots=True)
class Avaliacao:
    estado: Estado
    score: Decimal
    confirmado_em: datetime | None
    ultima_fonte: str | None
    idade_dias: int | None


def estado_do_score(score: Decimal, regra: RegraDoCiclo) -> Estado:
    if score >= regra.limiar_vivo:
        return Estado.VIVO
    if score >= regra.limiar_maduro:
        return Estado.MADURO
    if score >= regra.limiar_decomposicao:
        return Estado.EM_DECOMPOSICAO
    return Estado.ADUBO


def _score(peso: Decimal, idade_dias: Decimal, meia_vida: Decimal) -> Decimal:
    decaimento = Decimal(str(round(0.5 ** float(max(idade_dias, _ZERO) / meia_vida), 6)))
    return (peso * decaimento).quantize(_SCORE, rounding=ROUND_HALF_UP)


def avaliar(
    observacoes: Iterable[Observacao],
    regra: RegraDoCiclo,
    pesos: Mapping[str, PesoDaFonte],
    agora: datetime,
) -> Avaliacao:
    """Estado e score do preço pelas observações que o confirmam.

    Vale a confirmação de maior score: uma referência conferida há 5 dias vence um
    oficial de 200 dias. Agente e billing com desconto ficam de fora."""
    melhor: tuple[Decimal, Observacao] | None = None
    for obs in observacoes:
        peso = pesos.get(obs.fonte)
        if peso is None or not peso.confirma or peso.peso <= 0 or not obs.sob_demanda:
            continue
        idade = Decimal((agora - obs.observado_em).total_seconds()) / Decimal(86400)
        score = _score(peso.peso, idade, regra.meia_vida_dias)
        if melhor is None or (score, obs.observado_em) > (melhor[0], melhor[1].observado_em):
            melhor = (score, obs)
    if melhor is None:
        return Avaliacao(Estado.ADUBO, _ZERO, None, None, None)
    score, obs = melhor
    return Avaliacao(
        estado=estado_do_score(score, regra),
        score=score,
        confirmado_em=obs.observado_em,
        ultima_fonte=obs.fonte,
        idade_dias=max((agora - obs.observado_em).days, 0),
    )


def informacao_do_agente(
    observacoes: Iterable[Observacao], avaliacao: Avaliacao
) -> Observacao | None:
    """O que o agente disse do preço, para a tela, só quando o preço já é ADUBO.

    Mais nova que a última confirmação, ou não acrescenta nada. Não muda o número: é
    informação ao lado do preço, marcada como não verificada."""
    if avaliacao.estado is not Estado.ADUBO:
        return None
    do_agente = [
        o
        for o in observacoes
        if o.fonte == Fonte.AGENTE
        and (avaliacao.confirmado_em is None or o.observado_em > avaliacao.confirmado_em)
    ]
    return max(do_agente, key=lambda o: o.observado_em, default=None)


def duracao_dos_estados(
    regra: RegraDoCiclo, peso: Decimal
) -> dict[Estado, tuple[int, int | None] | None]:
    """Dias desde a confirmação em que cada estado começa e termina, sem confirmação nova.

    None: a fonte nunca chega a esse estado (a referência, com peso 0,7, não é VIVO)."""

    def dia(limiar: Decimal) -> int:
        if peso <= limiar:
            return 0
        return round(float(regra.meia_vida_dias) * math.log2(float(peso / limiar)))

    limites = [
        (Estado.VIVO, regra.limiar_vivo),
        (Estado.MADURO, regra.limiar_maduro),
        (Estado.EM_DECOMPOSICAO, regra.limiar_decomposicao),
    ]
    duracao: dict[Estado, tuple[int, int | None] | None] = {}
    inicio = 0
    for estado, limiar in limites:
        fim = dia(limiar)
        duracao[estado] = (inicio, fim) if fim > inicio else None
        inicio = max(inicio, fim)
    duracao[Estado.ADUBO] = (inicio, None)
    return duracao


def regra_para(
    regras: Sequence[tuple[str, str | None, RegraDoCiclo]], tipo: str, nome: str | None
) -> RegraDoCiclo:
    """A regra mais específica que casa: padrão de nome antes de padrão de tipo, e
    tipo exato antes de `*`. Sem nenhuma, a primeira `*`."""
    nome_baixo = (nome or "").lower()
    candidatas = [
        (padrao_nome is not None, padrao_tipo != "*", len(padrao_tipo), regra)
        for padrao_tipo, padrao_nome, regra in regras
        if fnmatchcase(tipo, padrao_tipo)
        and (padrao_nome is None or fnmatchcase(nome_baixo, padrao_nome.lower()))
    ]
    if not candidatas:
        raise ValueError(f"sem regra de ciclo de vida para {tipo!r}")
    return max(candidatas, key=lambda c: c[:3])[3]
