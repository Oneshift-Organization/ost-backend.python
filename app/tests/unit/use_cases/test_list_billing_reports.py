"""ListBillingReportsUseCase: per-user list passthrough."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.entities.billing_report import BillingReport
from app.use_cases.billing.list_billing_reports import (
    ListBillingReportsInput,
    ListBillingReportsUseCase,
)


class _FakeReportRepo:
    def __init__(self, by_user: dict[UUID, list[BillingReport]]):
        self._by_user = by_user
        self.last_limit: int | None = None

    async def save(self, r): ...
    async def update(self, r): ...
    async def get_by_id(self, report_id, user_id): return None

    async def list_by_user(self, user_id, limit=50):
        self.last_limit = limit
        return self._by_user.get(user_id, [])


def _make_report(user_id: UUID) -> BillingReport:
    return BillingReport(
        user_id=user_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_returns_only_user_owned_reports():
    user_a, user_b = uuid4(), uuid4()
    repo = _FakeReportRepo(
        {user_a: [_make_report(user_a), _make_report(user_a)], user_b: [_make_report(user_b)]}
    )
    uc = ListBillingReportsUseCase(repo)
    out = await uc.execute(ListBillingReportsInput(user_id=user_a))
    assert len(out) == 2


@pytest.mark.asyncio
async def test_passes_limit_to_repo():
    user_id = uuid4()
    repo = _FakeReportRepo({user_id: []})
    uc = ListBillingReportsUseCase(repo)
    await uc.execute(ListBillingReportsInput(user_id=user_id, limit=17))
    assert repo.last_limit == 17
