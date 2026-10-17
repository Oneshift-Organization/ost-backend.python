"""Contrato da fila de curadoria de nomes de serviço (decisão 2.11)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ServicoNasFaturas:
    """Um nome de serviço por provedor e quantas faturas e clientes o trazem. Só contagens."""

    provider: str
    nome: str
    faturas: int
    clientes: int


class IServiceNameSuggestionRepository(Protocol):
    async def servicos_das_faturas(
        self, usuarios_excluidos: list[UUID]
    ) -> list[ServicoNasFaturas]: ...

    async def registrar(
        self,
        servico: ServicoNasFaturas,
        chave: str,
        alvo_sugerido: str | None,
        semelhanca: float | None,
    ) -> None: ...
