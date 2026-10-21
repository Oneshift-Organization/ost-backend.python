from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1.routes.institutions import institution_repository, router
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.domain.entities.institution import (
    InstitutionProfile,
    InstitutionRole,
    Membership,
    PublicationSource,
    protect_last_admin,
    publication_snapshot,
)
from app.schemas.auth import UserClaims
from app.use_cases.institutions import (
    CreateInstitutionInput,
    CreateInstitutionUseCase,
    JoinInstitutionInput,
    JoinInstitutionUseCase,
    PublishAnalysisInput,
    PublishAnalysisUseCase,
)


def member(role=InstitutionRole.MEMBER, profile=InstitutionProfile.STRATEGIC, publish=False):
    return Membership(uuid4(), uuid4(), "Ana", role, profile, publish, datetime.now(UTC))


@pytest.mark.parametrize("profile", list(InstitutionProfile))
def test_profile_never_grants_administrative_or_publication_permissions(profile):
    current = member(profile=profile)
    with pytest.raises(ForbiddenError):
        current.require_admin()
    with pytest.raises(ForbiddenError):
        current.require_publisher()


def test_administrator_and_explicit_publisher_permissions():
    member(role=InstitutionRole.ADMIN).require_admin()
    member(role=InstitutionRole.ADMIN).require_publisher()
    member(publish=True).require_publisher()
    with pytest.raises(ForbiddenError):
        member(publish=True).require_admin()


def test_last_administrator_cannot_leave_or_be_demoted():
    admin = member(role=InstitutionRole.ADMIN)
    with pytest.raises(ConflictError):
        protect_last_admin(admin, 1)
    protect_last_admin(admin, 2)
    protect_last_admin(member(), 1)


def test_snapshot_is_independent_allowlisted_and_preserves_unknown_score():
    raw = {
        "recommendation": "Validar",
        "llm_input_payload": {"secret": "private"},
        "cross_cloud_equivalents": [{"resource_id": "private"}],
        "decision_support": {
            "score": {
                "value": None,
                "status": "not_scored",
                "axes": [{"id": "technical", "value": None, "evidence": ["private"]}],
            },
            "workload_strategies": [{"resource_id": "private"}],
        },
        "action_plan": ["Comparar"],
        "savings_summary": {"total_monthly_savings_usd": 0},
    }
    source = PublicationSource(uuid4(), uuid4(), "Cenário", "aws", datetime.now(UTC), raw)
    snapshot = publication_snapshot(source)
    raw["action_plan"].append("changed")
    assert snapshot["result"]["action_plan"] == ["Comparar"]
    assert snapshot["result"]["savings_summary"]["total_monthly_savings_usd"] == 0
    assert snapshot["decision"]["score"]["value"] is None
    assert "private" not in str(snapshot)
    assert (
        publication_snapshot(
            PublicationSource(uuid4(), uuid4(), "Legacy", "oci", datetime.now(UTC), {})
        )["decision"]
        is None
    )


@pytest.mark.asyncio
async def test_use_cases_preserve_actor_and_explicit_target():
    repo = AsyncMock()
    user, institution, job = uuid4(), uuid4(), uuid4()
    await CreateInstitutionUseCase(repo).execute(CreateInstitutionInput(user, "Equipe", "Ana"))
    repo.create.assert_awaited_once_with(user, "Equipe", "Ana")
    await JoinInstitutionUseCase(repo).execute(JoinInstitutionInput(user, "code", "Ana"))
    repo.join.assert_awaited_once_with(user, "code", "Ana")
    await PublishAnalysisUseCase(repo).execute(PublishAnalysisInput(user, institution, job))
    repo.publish.assert_awaited_once_with(user, institution, job)


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    actor = uuid4()
    repo = AsyncMock()
    repo.list_for_user.return_value = []
    app.dependency_overrides[get_current_user] = lambda: UserClaims(sub=str(actor))
    app.dependency_overrides[institution_repository] = lambda: repo
    with TestClient(app) as client:
        yield client, repo, actor


@pytest.mark.parametrize(
    "exc,status",
    [(NotFoundError("missing"), 404), (ForbiddenError("no"), 403), (ConflictError("last"), 409)],
)
def test_route_maps_domain_errors(api, exc, status):
    client, repo, actor = api
    institution = uuid4()
    repo.detail.side_effect = exc
    assert client.get(f"/v1/institutions/{institution}").status_code == status
    repo.detail.assert_awaited_once_with(actor, institution)


def test_lists_are_private_paginated_and_not_cacheable(api):
    client, repo, actor = api
    response = client.get("/v1/institutions?limit=20&offset=10")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    repo.list_for_user.assert_awaited_once_with(actor, 20, 10)
    assert client.get("/v1/institutions?limit=101").status_code == 422
    assert client.get("/v1/institutions?offset=-1").status_code == 422


def test_cannot_choose_admin_or_inject_actor_when_joining(api):
    client, repo, _ = api
    response = client.post(
        "/v1/institutions/memberships",
        json={"code": "a" * 32, "display_name": "Ana", "role": "admin", "user_id": str(uuid4())},
    )
    assert response.status_code == 422
    repo.join.assert_not_awaited()
    assert (
        client.post(
            "/v1/institutions/memberships", json={"code": "invalid", "display_name": "Ana"}
        ).status_code
        == 422
    )


def test_cannot_supply_shared_result_instead_of_owned_job(api):
    client, repo, _ = api
    response = client.post(
        f"/v1/institutions/{uuid4()}/publications",
        json={"job_id": str(uuid4()), "snapshot": {"recommendation": "Forged"}},
    )
    assert response.status_code == 422
    repo.publish.assert_not_awaited()
