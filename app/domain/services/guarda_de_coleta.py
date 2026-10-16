"""Guarda contra coleta parcial no sync de preços (PLANO_CICLO_DE_VIDA_DO_PRECO §11).

O sync substitui, por conjunto (provedor, região, tipo, fonte), o que a coleta nova não
trouxe: o SKU que faltou fica `ausente_desde` e sai da cotação na hora. Uma API que
devolve menos itens sem dar erro (página cortada, 429 engolido, filtro mudado) tiraria
da cotação, de uma vez, preços que continuam valendo. A cobertura cairia até o próximo
sync, e a análise feita nesse meio-tempo sairia com menos preço oficial.

A guarda: conjunto grande que volta com menos da metade dos itens vigentes não marca
ausência. Os preços que vieram são gravados mesmo assim. Conjunto pequeno pode encolher
de verdade (um SKU aposentado em três), e por isso fica fora da guarda. Módulo puro.
"""

from __future__ import annotations

from decimal import Decimal

# A coleta nova precisa trazer ao menos esta fração dos itens vigentes do conjunto
FRACAO_MINIMA = Decimal("0.5")
# Abaixo disso o conjunto pode encolher de verdade, e a guarda não vale
TAMANHO_MINIMO = 10


def coleta_parcial(vigentes: int, trazidos: int) -> bool:
    """True quando a coleta parece cortada: não marcar como ausente o que faltou."""
    if vigentes < TAMANHO_MINIMO:
        return False
    return Decimal(trazidos) < FRACAO_MINIMA * vigentes
