from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.unmapped_billing_line import UnmappedBillingLine
from app.infrastructure.database.models.unmapped_billing_lines import UnmappedBillingLineModel

_BATCH_SIZE = 1000


class PgUnmappedBillingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, items: list[UnmappedBillingLine]) -> int:
        if not items:
            return 0
        rows = [self._to_row(it) for it in items]
        total = 0
        for i in range(0, len(rows), _BATCH_SIZE):
            chunk = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(UnmappedBillingLineModel).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            result = await self._session.execute(stmt)
            total += result.rowcount or len(chunk)  # type: ignore[attr-defined]
        await self._session.flush()
        return total

    @staticmethod
    def _to_row(item: UnmappedBillingLine) -> dict[str, Any]:
        return {
            "id": item.id,
            "report_id": item.report_id,
            "user_id": item.user_id,
            "provider": item.provider,
            "billing_period": item.billing_period,
            "usage_type": item.usage_type,
            "service_name": item.service_name,
            "region": item.region,
            "reason": item.reason,
            "raw_payload": item.raw_payload,
            "source_line_hash": item.source_line_hash,
        }
