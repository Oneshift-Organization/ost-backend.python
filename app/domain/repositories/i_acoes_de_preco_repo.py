from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.domain.services.contestacao import ObservacaoDoAgente, PedidoDeSync


class IAcoesDePrecoRepository(ABC):
    """O que a contestação do agente deixa gravado (lote v4, V4.1)."""

    @abstractmethod
    async def registrar_contestacoes(
        self,
        pedidos: Sequence[PedidoDeSync],
        observacoes: Sequence[ObservacaoDoAgente],
        snapshot_id: UUID | None,
    ) -> None:
        """Pedidos de sync (sem repetir um aberto igual) e observações do agente."""

    @abstractmethod
    async def pedidos_abertos(self) -> list[tuple[UUID, PedidoDeSync]]:
        """Pedidos ainda não atendidos."""

    @abstractmethod
    async def marcar_atendidos(self, ids: Sequence[UUID]) -> int:
        """Fecha os pedidos atendidos pelo sync."""
