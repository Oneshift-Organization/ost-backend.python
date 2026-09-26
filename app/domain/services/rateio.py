"""Rateio de dinheiro sem perder centavo.

Repartir um valor na proporção de pesos e arredondar cada parte faz a soma
das partes divergir do total. Numa análise que se reconcilia contra a fatura,
esse resíduo aparece como "dinheiro que sumiu" — pequeno, mas suficiente para
um gate de conservação falhar sem que nada esteja errado.

Função pura, sem I/O.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

_CASAS = Decimal("1E-10")


def repartir(total: Decimal, pesos: list[Decimal]) -> list[Decimal]:
    """Parte ``total`` na proporção de ``pesos``; a soma das partes é ``total``.

    Cada parte é arredondada em 10 casas e o resíduo vai para a de maior peso.
    Pesos todos zero (ou negativos somando zero) repartem igualmente — sem
    isso a divisão por zero derrubaria quem chama por um detalhe de dado.
    """
    if not pesos:
        return []
    soma = sum(pesos, Decimal("0"))
    if soma == 0:
        pesos = [Decimal("1")] * len(pesos)
        soma = Decimal(len(pesos))
    partes = [(total * p / soma).quantize(_CASAS, rounding=ROUND_HALF_EVEN) for p in pesos]
    maior = max(range(len(pesos)), key=lambda i: pesos[i])
    partes[maior] += total - sum(partes, Decimal("0"))
    return partes
