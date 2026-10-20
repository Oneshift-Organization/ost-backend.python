"""RenameBillingReportUseCase: nome do billing dado pelo usuário (M9)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.billing_report import BillingReport
from app.use_cases.billing.rename_billing_report import (
    RenameBillingReportInput,
    RenameBillingReportUseCase,
)


class _FakeRepo:
    def __init__(self, report: BillingReport) -> None:
        self._report = report
        self.gravado: list[tuple[UUID, UUID, str | None]] = []

    async def get_by_id(self, report_id, user_id):
        r = self._report
        return r if (r.id, r.user_id) == (report_id, user_id) else None

    async def rename(self, report_id, user_id, alias):
        self.gravado.append((report_id, user_id, alias))


@pytest.mark.asyncio
async def test_grava_o_alias_normalizado() -> None:
    r = BillingReport(user_id=uuid4(), source_filename="cur.csv")
    repo = _FakeRepo(r)
    out = await RenameBillingReportUseCase(repo).execute(  # type: ignore[arg-type]
        RenameBillingReportInput(user_id=r.user_id, report_id=r.id, alias="  Março  AWS ")
    )
    assert (out.alias, out.nome_exibicao) == ("Março AWS", "Março AWS")
    assert repo.gravado == [(r.id, r.user_id, "Março AWS")]


@pytest.mark.asyncio
async def test_billing_de_outro_usuario_e_404_sem_gravar() -> None:
    r = BillingReport(user_id=uuid4())
    repo = _FakeRepo(r)
    with pytest.raises(NotFoundError):
        await RenameBillingReportUseCase(repo).execute(  # type: ignore[arg-type]
            RenameBillingReportInput(user_id=uuid4(), report_id=r.id, alias="x")
        )
    assert repo.gravado == []


@pytest.mark.asyncio
async def test_nome_invalido_nao_grava() -> None:
    r = BillingReport(user_id=uuid4())
    repo = _FakeRepo(r)
    with pytest.raises(ValidationError):
        await RenameBillingReportUseCase(repo).execute(  # type: ignore[arg-type]
            RenameBillingReportInput(user_id=r.user_id, report_id=r.id, alias="x" * 500)
        )
    assert repo.gravado == []
