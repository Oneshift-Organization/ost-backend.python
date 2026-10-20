"""Leitura de TODAS as linhas de um relatório, em páginas.

O enriquecimento e o contexto liam ``limit=100000`` numa chamada só: acima de
cem mil linhas o excedente sumia sem erro nenhum, e o custo total da análise
saía menor do que a fatura. Um CUR diário de conta média passa disso.

A paginação por ``offset`` só é correta se a ordenação do repositório for
TOTAL — com empate, a mesma linha pode aparecer em duas páginas e outra em
nenhuma. Os repositórios de itens desempatam por ``id``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

PAGINA = 5000


async def listar_todos[T](
    listar: Callable[..., Awaitable[list[T]]],
    report_id: UUID,
    user_id: UUID,
    *,
    pagina: int | None = None,
) -> list[T]:
    # Lido na chamada, não na definição: permite ao teste provar a paginação
    # com páginas pequenas sem gerar cem mil linhas.
    pagina = pagina or PAGINA
    itens: list[T] = []
    offset = 0
    while True:
        lote = await listar(report_id, user_id, limit=pagina, offset=offset)
        itens.extend(lote)
        if len(lote) < pagina:
            return itens
        offset += pagina
