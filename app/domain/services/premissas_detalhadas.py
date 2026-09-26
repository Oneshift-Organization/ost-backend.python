"""Premissas que só existem com export detalhado (RN1, RN9, RN11 do PLANO_CUR_BIGQUERY).

Cada uma responde a uma pergunta que o leitor da análise faria e que, sem ela,
ficaria sem resposta:

- ``base_de_custo``: o custo que estou vendo é o da fatura ou o amortizado?
- ``desconto_origem_sobre_lista``: quanto de desconto eu tenho HOJE — e perco se
  migrar sem recontratar compromisso no destino?
- ``dimensionamento_medido``: quanto da capacidade é fato, não estimativa?

Funções puras: entram números, sai dict pronto para o ledger. Aditivas — o
caminho agregado não as chama e o payload dele não muda.
"""

from __future__ import annotations

from decimal import Decimal

_ZERO = Decimal("0")

_BASES = {"amortized": "amortizado", "billed": "faturado"}


def base_de_custo(custo_por_base: dict[str, Decimal]) -> dict[str, object] | None:
    """Amortizado quando todo o custo pôde ser amortizado; misto quando parte
    ficou na base faturada por falta das colunas de compromisso."""
    presentes = {b: v for b, v in custo_por_base.items() if v != 0}
    if not presentes:
        return None
    if set(presentes) == {"amortized"}:
        base = "amortizado"
        explicacao = (
            "Custo efetivo amortizado: Savings Plan, reserva e CUD distribuídos "
            "sobre o uso que cobriram, desconto negociado aplicado. É o custo "
            "real de cada recurso — a instância coberta por compromisso não "
            "aparece como gratuita."
        )
    elif set(presentes) == {"billed"}:
        base = "faturado"
        explicacao = (
            "Custo como faturado: o export não trouxe as colunas de compromisso, "
            "então recursos cobertos por Savings Plan/reserva aparecem com custo "
            "zero e o plano aparece à parte."
        )
    else:
        base = "misto"
        explicacao = (
            "Parte do custo foi amortizada e parte ficou como faturada, porque o "
            "export não trouxe todas as colunas de compromisso. Os recursos da "
            "parte faturada podem aparecer com custo zero."
        )
    return {
        "base": base,
        "por_base": {_BASES.get(b, b): round(float(v), 2) for b, v in sorted(presentes.items())},
        "explicacao": explicacao,
    }


def desconto_origem_sobre_lista(
    custo_sob_demanda: Decimal, custo_efetivo: Decimal
) -> dict[str, object] | None:
    """(sob demanda - efetivo) / sob demanda, só nas linhas que têm os dois.

    O destino é cotado a preço de lista. Sem esta premissa, o comparativo
    parece dizer que o destino é mais caro quando o que acontece é que a origem
    tem desconto — que o cliente perde se migrar sem recontratar.
    """
    if custo_sob_demanda <= 0:
        return None
    fracao = (custo_sob_demanda - custo_efetivo) / custo_sob_demanda
    return {
        "fracao": round(float(fracao), 4),
        "custo_sob_demanda": round(float(custo_sob_demanda), 2),
        "custo_efetivo": round(float(custo_efetivo), 2),
        "explicacao": (
            "Desconto que a origem tem hoje sobre o preço de lista (compromisso, "
            "desconto negociado, uso sustentado). O destino está cotado sem "
            "compromisso: para comparar igual, considere recontratar compromisso "
            "no destino ou descontar esta fração da economia."
        ),
    }


def fracao(parte: Decimal, total: Decimal) -> float:
    return float(parte / total) if total > 0 else 0.0
