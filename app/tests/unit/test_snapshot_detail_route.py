"""Contrato HTTP do detalhe congelado, sem conexão a banco ou serviços externos."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.v1.routes import agent_snapshots as routes
from app.domain.services.fingerprint_payload import hash_canonico


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_detail_scopes_user_and_returns_only_frozen_decision(monkeypatch, found):
    user_id, snapshot_id = uuid4(), uuid4()
    decision = {"policy_version": "score-7rs-v5", "selected_provider": None}
    snapshot = SimpleNamespace(id=snapshot_id, payload={"decision_support": decision})
    lookup = AsyncMock(return_value=snapshot if found else None)
    monkeypatch.setattr(
        routes, "consultar_envios_uc", lambda _: SimpleNamespace(obter_snapshot=lookup)
    )
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(agent_snapshot_enabled=True, agent_flow_enabled=True),
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(sub=str(user_id))
    session = SimpleNamespace(commit=AsyncMock())
    app.dependency_overrides[deps.get_db_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/agent/snapshots/{snapshot_id}/decision-support")
    lookup.assert_awaited_once_with(user_id, snapshot_id)
    session.commit.assert_not_awaited()
    if found:
        assert response.status_code == 200
        assert response.json() == {
            "snapshot_id": str(snapshot_id),
            "decision_support": decision,
            "sha256": hash_canonico(decision),
        }
    else:
        assert response.status_code == 404
        assert response.json()["detail"]["codigo"] == "SNAPSHOT_NAO_ENCONTRADO"


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_enabled,flow_enabled", [(False, True), (True, False)])
async def test_detail_respects_both_flags_before_lookup(
    monkeypatch, snapshot_enabled, flow_enabled
):
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            agent_snapshot_enabled=snapshot_enabled, agent_flow_enabled=flow_enabled
        ),
    )
    with pytest.raises(HTTPException) as error:
        await routes.obter_decisao_snapshot(uuid4(), SimpleNamespace(sub=str(uuid4())), None)
    assert error.value.status_code == 404
    assert error.value.detail["codigo"] == "SNAPSHOT_DESLIGADO"
