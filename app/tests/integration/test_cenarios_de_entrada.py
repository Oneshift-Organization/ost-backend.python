"""Cost Explorer recusado no upload, com o caminho certo (PLANO_CENARIOS_DE_ENTRADA §3.4)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import config
from app.main import create_app

pytestmark = pytest.mark.asyncio(loop_scope="session")
_CE = Path(__file__).parents[1] / "fixtures" / "billing" / "aws_ce_daily_by_service.tsv"


@pytest.fixture
def app() -> Iterator[FastAPI]:
    config.get_settings.cache_clear()
    yield create_app()
    config.get_settings.cache_clear()


async def test_upload_de_cost_explorer_e_recusado_com_a_explicacao(app, _db_ready) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        verificacao = await c.post(
            "/v1/billing-file-checks", files={"file": (_CE.name, _CE.read_bytes(), "text/plain")}
        )
        upload = await c.post(
            "/v1/billing-files", files={"file": (_CE.name, _CE.read_bytes(), "text/plain")}
        )
    assert verificacao.status_code == 200, verificacao.text
    assert verificacao.json()["aceita"] is False
    assert upload.status_code == 422, upload.text
    detalhe = upload.json()["detail"]
    assert detalhe["codigo"] == "FORMATO_NAO_SUPORTADO"
    assert "CUR 2.0" in detalhe["mensagem"]


async def test_upload_de_arquivo_que_nao_e_fatura_e_recusado_na_hora(app, _db_ready) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        upload = await c.post(
            "/v1/billing-files", files={"file": ("notas.csv", b"nome,idade\nana,30\n", "text/csv")}
        )
    assert upload.status_code == 422, upload.text
    assert upload.json()["detail"]["codigo"] == "FORMATO_NAO_SUPORTADO"
