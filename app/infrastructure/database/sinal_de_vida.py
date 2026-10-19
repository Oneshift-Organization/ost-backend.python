"""Sinal de vida de um job que roda em BackgroundTasks (análise, geração de Terraform).

O job roda no processo web. Num deploy, o uvicorn espera as tarefas em andamento e o
Kubernetes mata o pod 30 s depois do aviso: nem o desligamento do app roda. Sem sinal,
a varredura só via o job morto pelo limite de 10 minutos (PROXIMAS_EXECUCOES, item 26).

Enquanto o bloco roda, uma tarefa grava `updated_at` a cada intervalo, em sessão
própria e só se o job ainda estiver ativo. A linha do job não fica presa pela execução
(o início é commitado em transação própria), então o sinal não disputa trava.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger


@asynccontextmanager
async def sinal_de_vida(
    sessionmaker: async_sessionmaker[AsyncSession],
    modelo: Any,
    coluna_id: Any,
    valor_id: object,
    ativos: Sequence[str],
    *,
    intervalo_segundos: float,
    agora: Callable[[], datetime],
) -> AsyncIterator[None]:
    async def bater() -> None:
        while True:
            await asyncio.sleep(intervalo_segundos)
            # Um sinal perdido não pode matar a execução: o próximo tenta de novo
            try:
                async with sessionmaker() as session:
                    await session.execute(
                        update(modelo)
                        .where(coluna_id == valor_id, modelo.status.in_(list(ativos)))
                        .values(updated_at=agora())
                    )
                    await session.commit()
            except Exception:
                get_logger(__name__).warning("sinal_de_vida_falhou", job=str(valor_id))

    tarefa = asyncio.create_task(bater())
    try:
        yield
    finally:
        tarefa.cancel()
        with suppress(asyncio.CancelledError):
            await tarefa
