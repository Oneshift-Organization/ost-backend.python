from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


class ILLMClient(ABC):
    @abstractmethod
    async def generate(
        self, system: str, user: str, response_model: type[BaseModel]
    ) -> BaseModel: ...
