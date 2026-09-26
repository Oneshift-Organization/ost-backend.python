"""Leitura do custo de base por recurso de um billing (RI13)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.services.correlacao_inventario import CustosDoBilling


class ILeitorDeCustoPorRecurso(Protocol):
    async def custos_por_recurso(self, report_id: UUID, user_id: UUID) -> CustosDoBilling:
        """MUST filtrar por user_id."""
        ...
