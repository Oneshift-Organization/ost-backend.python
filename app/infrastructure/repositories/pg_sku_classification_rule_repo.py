from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.sku_normalization import normalize_provider
from app.infrastructure.database.models.sku_classification_rules import (
    SkuClassificationRuleModel,
)


def _to_entity(model: SkuClassificationRuleModel) -> SkuClassificationRule:
    return SkuClassificationRule(
        id=model.id,
        provider=model.provider,
        pattern=model.pattern,
        kind=model.kind,
        component_type=model.component_type,
        family=model.family,
        confidence=model.confidence,
        priority=model.priority,
        active=model.active,
        created_at=model.created_at,
    )


class PgSkuClassificationRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_by_provider(
        self, provider: str
    ) -> list[SkuClassificationRule]:
        stmt = (
            select(SkuClassificationRuleModel)
            .where(
                SkuClassificationRuleModel.provider.ilike(normalize_provider(provider)),
                SkuClassificationRuleModel.active.is_(True),
            )
            .order_by(SkuClassificationRuleModel.priority.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]
