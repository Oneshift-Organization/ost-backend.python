"""Contrato do repositório de inventário (I5).

Toda consulta filtra por ``user_id``: inventário de outro usuário é tratado como
inexistente (a rota responde 404 e não revela que existe).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.entities.topologia import Inventario, Recurso, TipoDeRecurso


class IInventoryRepository(Protocol):
    async def salvar(self, inventario: InventarioEnviado) -> None: ...

    async def atualizar(self, inventario: InventarioEnviado) -> None: ...

    async def obter(self, inventario_id: UUID, user_id: UUID) -> InventarioEnviado | None: ...

    async def listar(
        self, user_id: UUID, *, limite: int = 50, deslocamento: int = 0
    ) -> list[InventarioEnviado]: ...

    async def apagar(self, inventario_id: UUID, user_id: UUID) -> bool: ...

    async def gravar_conteudo(
        self, inventario_id: UUID, user_id: UUID, conteudo: Inventario
    ) -> None:
        """Substitui recursos, relações e regras (idempotente: apaga e insere)."""
        ...

    async def carregar_conteudo(self, inventario: InventarioEnviado) -> Inventario: ...

    async def listar_recursos(
        self,
        inventario_id: UUID,
        user_id: UUID,
        *,
        tipo: TipoDeRecurso | None = None,
        limite: int = 100,
        deslocamento: int = 0,
    ) -> tuple[list[Recurso], int]:
        """Página de recursos ordenada pela chave, e o total."""
        ...

    async def contar_por_tipo(self, inventario_id: UUID, user_id: UUID) -> dict[str, int]: ...

    async def chaves_existentes(
        self, inventario_id: UUID, user_id: UUID, chaves: Iterable[str]
    ) -> set[str]: ...
