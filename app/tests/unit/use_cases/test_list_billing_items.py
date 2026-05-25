"""ListBillingItemsUseCase: enforces ownership + pagination passthrough."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.domain.entities.billing_item import BillingItem
from app.domain.entities.billing_report import BillingReport
from app.domain.value_objects.cloud_provider import CloudProvider
from app.use_cases.billing.list_billing_items import (
    ListBillingItemsInput,
    ListBillingItemsUseCase,
)


class _FakeReportRepo:
    def __init__(self, reports: dict[tuple[UUID, UUID], BillingReport]):
        self._reports = reports

    async def save(self, r): ...
    async def update(self, r): ...
    async def list_by_user(self, user_id, limit=50): return []

    async def get_by_id(self, report_id, user_id):
        return self._reports.get((report_id, user_id))


class _FakeItemRepo:
    def __init__(self, items: list[BillingItem], total: int | None = None):
        self._items = items
        self._total = total if total is not None else len(items)
        self.last_call: dict | None = None

    async def bulk_insert(self, items): return len(items)
    async def delete_by_report(self, report_id, user_id): return 0

    async def count_by_report(self, report_id, user_id):
        return self._total

    async def list_by_report(self, report_id, user_id, *, limit=100, offset=0):
        self.last_call = {"limit": limit, "offset": offset}
        return self._items[offset : offset + limit]


def _make_item(report_id: UUID, user_id: UUID, cost: str) -> BillingItem:
    return BillingItem(
        user_id=user_id,
        report_id=report_id,
        provider=CloudProvider.AWS,
        service="EC2",
        cost=Decimal(cost),
        currency="USD",
        billing_period="2026-04",
        line_hash="x" * 64,
    )


def _make_report(report_id: UUID, user_id: UUID) -> BillingReport:
    return BillingReport(
        id=report_id,
        user_id=user_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_returns_items_for_owner():
    user_id, report_id = uuid4(), uuid4()
    items = [_make_item(report_id, user_id, "10"), _make_item(report_id, user_id, "20")]
    uc = ListBillingItemsUseCase(
        _FakeReportRepo({(report_id, user_id): _make_report(report_id, user_id)}),
        _FakeItemRepo(items),
    )
    out = await uc.execute(ListBillingItemsInput(user_id=user_id, report_id=report_id))
    assert out.total == 2
    assert len(out.items) == 2


@pytest.mark.asyncio
async def test_404_when_report_belongs_to_another_user():
    user_a, user_b, report_id = uuid4(), uuid4(), uuid4()
    # Report stored under user_a; user_b tries to read.
    reports = {(report_id, user_a): _make_report(report_id, user_a)}
    uc = ListBillingItemsUseCase(_FakeReportRepo(reports), _FakeItemRepo([]))
    with pytest.raises(NotFoundError):
        await uc.execute(ListBillingItemsInput(user_id=user_b, report_id=report_id))


@pytest.mark.asyncio
async def test_pagination_passthrough_to_repo():
    user_id, report_id = uuid4(), uuid4()
    items = [_make_item(report_id, user_id, str(i)) for i in range(1, 21)]
    fake_items = _FakeItemRepo(items, total=20)
    uc = ListBillingItemsUseCase(
        _FakeReportRepo({(report_id, user_id): _make_report(report_id, user_id)}),
        fake_items,
    )
    out = await uc.execute(
        ListBillingItemsInput(user_id=user_id, report_id=report_id, limit=5, offset=10)
    )
    assert fake_items.last_call == {"limit": 5, "offset": 10}
    assert out.limit == 5
    assert out.offset == 10
    assert out.total == 20
    assert len(out.items) == 5
