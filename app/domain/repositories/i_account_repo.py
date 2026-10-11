from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


class IAccountRepository(ABC):
    @abstractmethod
    async def delete_user_data(self, user_id: UUID) -> None:
        """Apaga tudo que pertence ao usuário, em todas as tabelas."""
        ...
