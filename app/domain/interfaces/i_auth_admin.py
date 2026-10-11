from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


class IAuthAdmin(ABC):
    @abstractmethod
    async def delete_user(self, user_id: UUID) -> None:
        """Remove o usuário do provedor de identidade. Idempotente."""
        ...
