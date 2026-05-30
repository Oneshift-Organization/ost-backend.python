from __future__ import annotations

from typing import Protocol

from app.domain.entities.sku_classification_rule import SkuClassificationRule


class ISkuClassificationRuleRepository(Protocol):
    async def list_active_by_provider(
        self, provider: str
    ) -> list[SkuClassificationRule]:
        """Return active rules for a provider, ordered by priority ascending."""
        ...
