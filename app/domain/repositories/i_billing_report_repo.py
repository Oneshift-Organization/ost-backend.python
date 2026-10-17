from typing import Protocol
from uuid import UUID

from app.domain.entities.billing_report import BillingReport


class IBillingReportRepository(Protocol):
    """Repository interface for parent billing reports."""

    async def save(self, report: BillingReport) -> BillingReport: ...

    async def get_by_id(self, report_id: UUID, user_id: UUID) -> BillingReport | None:
        """MUST filter by user_id — never return another user's report."""
        ...

    async def update(self, report: BillingReport) -> BillingReport:
        """Estado da ingestão. Não grava o alias: a ingestão em curso não apaga o nome."""
        ...

    async def rename(self, report_id: UUID, user_id: UUID, alias: str | None) -> None:
        """Grava só o alias. MUST filter by user_id."""
        ...

    async def list_by_user(self, user_id: UUID, limit: int = 50) -> list[BillingReport]: ...
