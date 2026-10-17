"""UploadBillingFileUseCase: o nome dado na importação (M9)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.exceptions import ValidationError
from app.use_cases.billing.upload_billing_file import (
    UploadBillingFileInput,
    UploadBillingFileUseCase,
)


class _FakeRepo:
    def __init__(self) -> None:
        self.salvos: list = []

    async def save(self, report):
        self.salvos.append(report)
        return report


def _entrada(alias: str | None) -> UploadBillingFileInput:
    return UploadBillingFileInput(
        user_id=uuid4(), file_path=Path("x.csv"), source_filename="cur.csv", alias=alias
    )


@pytest.mark.asyncio
async def test_nome_da_importacao_vai_junto_com_o_billing() -> None:
    repo = _FakeRepo()
    r = await UploadBillingFileUseCase(repo).execute(_entrada("  Março   AWS "))  # type: ignore[arg-type]
    assert (r.alias, r.nome_exibicao) == ("Março AWS", "Março AWS")
    assert repo.salvos == [r]


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", [None, "", "   "])
async def test_sem_nome_fica_o_do_arquivo(alias: str | None) -> None:
    r = await UploadBillingFileUseCase(_FakeRepo()).execute(_entrada(alias))  # type: ignore[arg-type]
    assert (r.alias, r.nome_exibicao) == (None, "cur.csv")


@pytest.mark.asyncio
async def test_nome_invalido_nao_grava_o_billing() -> None:
    repo = _FakeRepo()
    with pytest.raises(ValidationError):
        await UploadBillingFileUseCase(repo).execute(_entrada("x" * 121))  # type: ignore[arg-type]
    assert repo.salvos == []
