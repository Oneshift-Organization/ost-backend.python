#!/usr/bin/env python3
"""Popula `exchange_rates` com cotações de referência do BCE.

Roda offline (script/cron), nunca no caminho crítico da análise — mesmo padrão
de `sync_prices.py`.

Enquanto a tabela estiver vazia, `_resolve_fx` cai no fallback `fx_<par>` do
ambiente: um número sem data, que ninguém atualiza, e que hoje está ~11% fora
do câmbio real. Rodar isto é o que dá data e procedência ao câmbio.

Uso:
    python scripts/sync_fx.py                    # BRL, EUR, GBP, ...
    python scripts/sync_fx.py --moedas BRL,EUR
"""

from __future__ import annotations

import argparse
import asyncio

from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.fx.ecb_fx_client import MOEDAS_PADRAO, fetch_fx_rates
from app.infrastructure.repositories.pg_exchange_rate_repo import (
    PgExchangeRateRepository,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--moedas", default=",".join(MOEDAS_PADRAO))
    args = parser.parse_args()

    moedas = tuple(m.strip().upper() for m in args.moedas.split(",") if m.strip())
    taxas = fetch_fx_rates(moedas)
    if not taxas:
        print("nenhuma cotação obtida — fonte fora do ar ou moedas inválidas")
        return

    sm = get_sessionmaker()
    async with sm() as session:
        gravadas = await PgExchangeRateRepository(session).upsert_many(taxas)
        await session.commit()

    for t in sorted(taxas, key=lambda x: x.par):
        print(f"{t.par}: {t.taxa} ({t.data}, {t.fonte})")
    print(f"gravadas={gravadas}")


if __name__ == "__main__":
    asyncio.run(main())
