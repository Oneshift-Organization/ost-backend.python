"""Payback: a premissa é do agente, a divisão é do backend.

O backend não modela o custo único de migração (horas de engenharia, dupla
operação, treinamento). Por isso `payback_period_months` era SEM_BASE: o
agente mandava `meses` junto com a premissa, e o backend repassava sem
conferir. O número saía da mesma conta que o resto da tela, mas feita pelo
modelo — exatamente o que a regra "a IA narra, o backend calcula" proíbe.

Com a premissa declarada, a conta é determinística:

    meses = ⌈custo de migração ÷ economia mensal do provedor selecionado⌉

Sem economia, não existe payback: a migração não se paga. Devolver um número
nesse caso seria inventar.

Função pura, sem I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Payback:
    meses: int | None
    motivo: str | None = None


def calcular_payback(custo_migracao: Decimal, economia_mensal: Decimal) -> Payback:
    if economia_mensal <= 0:
        return Payback(
            None,
            "sem economia mensal no provedor selecionado: a migração não se paga",
        )
    if custo_migracao <= 0:
        return Payback(0)
    return Payback(math.ceil(custo_migracao / economia_mensal))
