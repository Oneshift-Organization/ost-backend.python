from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.services.ciclo_de_vida_do_preco import (
    Avaliacao,
    Observacao,
    PesoDaFonte,
    RegraDoCiclo,
)

# (provedor, sku, região): a chave de um ponto de preço
ChaveDoPreco = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class PontoDoCiclo:
    chave: ChaveDoPreco
    service_type: str
    service_name: str
    preco: Decimal
    moeda: str
    unidade: str
    confianca: str
    capturado_em: datetime


@dataclass(frozen=True, slots=True)
class NovaObservacao:
    chave: ChaveDoPreco
    observacao: Observacao
    moeda: str
    unidade: str
    # O ponto já tinha outro valor: a versão dele sobe
    troca: bool = False


class IPriceLifecycleRepository(ABC):
    @abstractmethod
    async def regras(self) -> list[tuple[str, str | None, RegraDoCiclo]]:
        """Regras ativas do ciclo: (padrão do tipo, padrão do nome, regra)."""

    @abstractmethod
    async def pesos(self) -> dict[str, PesoDaFonte]:
        """Peso de cada fonte e se ela confirma."""

    @abstractmethod
    async def pontos(self) -> list[PontoDoCiclo]:
        """Todos os pontos de preço."""

    @abstractmethod
    async def observacoes(self) -> dict[ChaveDoPreco, list[Observacao]]:
        """As observações gravadas, por ponto."""

    @abstractmethod
    async def registrar(self, novas: Sequence[NovaObservacao]) -> int:
        """Grava observações; troca de valor sobe a versão do ponto."""

    @abstractmethod
    async def atualizar(self, avaliacoes: Sequence[tuple[ChaveDoPreco, Avaliacao]]) -> int:
        """Grava estado, score, confirmação e fonte em cada ponto."""
