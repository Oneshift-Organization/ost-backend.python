"""Exercise HTTP parsing and background-task handoff without DB/LLM/network."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1.routes import analysis
from app.infrastructure.database.session import get_db_session
from app.schemas.auth import UserClaims


@pytest.fixture
def api(monkeypatch):
    session = AsyncMock()
    repo = AsyncMock()
    repo.buscar_ativo_por_report.return_value = None
    pipeline = AsyncMock()
    monkeypatch.setattr(analysis, "PgAnalysisJobRepository", lambda _: repo)
    monkeypatch.setattr(analysis, "get_sessionmaker", lambda: None)
    monkeypatch.setattr(analysis, "_run_analysis_pipeline", pipeline)
    app = FastAPI()
    app.include_router(analysis.router)
    app.dependency_overrides[get_current_user] = lambda: UserClaims(sub=str(uuid4()))
    app.dependency_overrides[get_db_session] = lambda: session
    with TestClient(app) as client:
        yield client, pipeline


def test_existing_start_without_body_is_compatible(api):
    client, pipeline = api
    response = client.post(f"/analyses/start/{uuid4()}")
    assert response.status_code == 202, response.text
    assert pipeline.await_args.args[-1] is None


def test_validated_context_reaches_pipeline(api):
    client, pipeline = api
    context = {
        "governance": [
            {
                "provider": "gcp",
                "evidence": "Aprovação do responsável",
                "region_approved": True,
            }
        ],
        "workloads": [],
    }
    response = client.post(f"/analyses/start/{uuid4()}", json={"decision_context": context})
    assert response.status_code == 202, response.text
    assert pipeline.await_args.args[-1] == context


def test_client_cannot_send_authoritative_score(api):
    client, pipeline = api
    response = client.post(f"/analyses/start/{uuid4()}", json={"score": 100})
    assert response.status_code == 422
    pipeline.assert_not_awaited()


def test_string_boolean_is_not_accepted_as_governance_evidence(api):
    client, pipeline = api
    response = client.post(
        f"/analyses/start/{uuid4()}",
        json={
            "decision_context": {
                "governance": [{"provider": "gcp", "evidence": "x", "region_approved": "true"}],
            }
        },
    )
    assert response.status_code == 422
    pipeline.assert_not_awaited()
