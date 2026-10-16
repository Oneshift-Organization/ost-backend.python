"""Composição do recálculo do ciclo de vida (PLANO_CICLO_DE_VIDA_DO_PRECO).

Fica no pacote, e não em `scripts/`, porque o sync roda no pod como
`python - < scripts/sync_prices.py`: só o pacote `app` existe lá.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_price_lifecycle_repo import PgPriceLifecycleRepository
from app.use_cases.catalog.recalcular_ciclo_de_vida import (
    RecalcularCicloDeVidaInput,
    RecalcularCicloDeVidaUseCase,
)


async def recalcular_ciclo_de_vida() -> str:
    """Recalcula estado e score de todos os preços e devolve a linha de resumo."""
    async with get_sessionmaker()() as session:
        uc = RecalcularCicloDeVidaUseCase(PgPriceLifecycleRepository(session))
        res = await uc.execute(RecalcularCicloDeVidaInput(agora=datetime.now(UTC)))
        await session.commit()
    estados = " ".join(f"{e}={n}" for e, n in sorted(res.por_estado.items()))
    return f"ciclo de vida: {estados or 'sem regras'} observacoes_novas={res.observacoes_novas}"
