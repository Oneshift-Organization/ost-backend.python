#!/usr/bin/env python3
"""Recalcula estado e score dos preços (PLANO_CICLO_DE_VIDA_DO_PRECO, P3.2).

O decaimento anda com o relógio, então isto roda agendado (diário) além de rodar ao
fim de cada `sync_prices`. Não muda número de análise: o estado é rótulo.

Uso:
    python scripts/recalcular_ciclo_de_vida.py
    python - < scripts/recalcular_ciclo_de_vida.py      # no pod
"""

from __future__ import annotations

import asyncio

from app.infrastructure.pricing.ciclo_de_vida import recalcular_ciclo_de_vida

if __name__ == "__main__":
    print(asyncio.run(recalcular_ciclo_de_vida()))  # noqa: T201
