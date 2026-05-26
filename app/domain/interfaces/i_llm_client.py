from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


class ILLMClient(ABC):
    @abstractmethod
    async def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        ...
