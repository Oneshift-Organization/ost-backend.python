"""Isolation and lifecycle gates using disposable rows, rolled back after each test."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.domain.entities.institution import InstitutionProfile, InstitutionRole
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.models.institution import (
    InstitutionInviteModel,
    InstitutionMemberModel,
    InstitutionModel,
    InstitutionPublicationModel,
)
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_institution_repo import PgInstitutionRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def workspace(_db_ready):
    async with get_sessionmaker()() as session:
        try:
            yield session, PgInstitutionRepository(session)
        finally:
            await session.rollback()


async def source_job(session, actor, status="COMPLETED"):
    now = datetime.now(UTC)
    report = BillingReportModel(
        id=uuid4(),
        user_id=actor,
        provider="aws",
        schema_version="test",
        source_filename="institution-test.csv",
        status="done",
        row_count=1,
        created_at=now,
        updated_at=now,
    )
    session.add(report)
    await session.flush()
    job = AnalysisJobModel(
        job_id=uuid4(),
        user_id=actor,
        report_id=report.id,
        status=status,
        created_at=now.replace(tzinfo=None),
        disp_status=1,
        ai_structured_output={"recommendation": "Snapshot", "llm_input_payload": {"private": True}},
    )
    session.add(job)
    await session.flush()
    return job


async def test_institution_isolation_invite_permissions_and_last_admin(workspace):
    session, repo = workspace
    owner, guest, outsider = uuid4(), uuid4(), uuid4()
    institution = await repo.create(owner, "Equipe", "Responsável")
    iid = institution["id"]
    assert await repo.list_for_user(outsider, 100, 0) == []
    with pytest.raises(NotFoundError):
        await repo.detail(outsider, iid)
    invite = await repo.create_invite(owner, iid)
    stored = await session.get(InstitutionInviteModel, invite["id"])
    assert invite["code"] != stored.token_hash
    assert "token_hash" not in invite
    joined = await repo.join(guest, invite["code"], "Convidado")
    assert joined["membership"]["role"] == "member"
    assert joined["membership"]["can_publish"] is False
    with pytest.raises(NotFoundError):
        await repo.join(outsider, invite["code"], "Terceiro")
    with pytest.raises(ForbiddenError):
        await repo.create_invite(guest, iid)
    with pytest.raises(ForbiddenError):
        await repo.update_member(
            guest,
            iid,
            guest,
            role=InstitutionRole.ADMIN,
            profile=InstitutionProfile.ADMINISTRATIVE,
            can_publish=True,
        )
    with pytest.raises(ConflictError):
        await repo.remove_member(owner, iid, owner)
    with pytest.raises(ConflictError):
        await repo.prepare_account_deletion(owner)
    await repo.update_member(
        owner,
        iid,
        guest,
        role=InstitutionRole.ADMIN,
        profile=InstitutionProfile.FINANCIAL,
        can_publish=True,
    )
    await repo.remove_member(owner, iid, owner)
    with pytest.raises(NotFoundError):
        await repo.detail(owner, iid)
    assert (await repo.detail(guest, iid))["membership"]["role"] == "admin"


async def test_revoked_expired_and_demoted_creator_invites_cannot_join(workspace):
    session, repo = workspace
    owner, other = uuid4(), uuid4()
    iid = (await repo.create(owner, "Equipe", "Responsável"))["id"]
    revoked = await repo.create_invite(owner, iid)
    await repo.revoke_invite(owner, iid, revoked["id"])
    with pytest.raises(NotFoundError):
        await repo.join(other, revoked["code"], "Convidado")
    expired = await repo.create_invite(owner, iid)
    row = await session.get(InstitutionInviteModel, expired["id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    with pytest.raises(NotFoundError):
        await repo.join(other, expired["code"], "Convidado")
    valid = await repo.create_invite(owner, iid)
    await repo.join(other, valid["code"], "Convidado")
    await repo.update_member(
        owner,
        iid,
        other,
        role=InstitutionRole.ADMIN,
        profile=InstitutionProfile.TECHNICAL,
        can_publish=True,
    )
    pending = await repo.create_invite(other, iid)
    await repo.update_member(
        owner,
        iid,
        other,
        role=InstitutionRole.MEMBER,
        profile=InstitutionProfile.FINANCIAL,
        can_publish=True,
    )
    with pytest.raises(NotFoundError):
        await repo.join(uuid4(), pending["code"], "Convidado")


async def test_only_owned_completed_analyses_publish_as_independent_copies(workspace):
    session, repo = workspace
    owner, guest = uuid4(), uuid4()
    iid = (await repo.create(owner, "Equipe", "Responsável"))["id"]
    invite = await repo.create_invite(owner, iid)
    await repo.join(guest, invite["code"], "Convidado")
    job = await source_job(session, owner)
    with pytest.raises(ForbiddenError):
        await repo.publish(guest, iid, job.job_id)
    await repo.update_member(
        owner,
        iid,
        guest,
        role=InstitutionRole.MEMBER,
        profile=InstitutionProfile.FINANCIAL,
        can_publish=True,
    )
    with pytest.raises(NotFoundError):
        await repo.publish(guest, iid, job.job_id)
    pending = await source_job(session, owner, status="PROCESSING")
    with pytest.raises(ConflictError):
        await repo.publish(owner, iid, pending.job_id)
    published = await repo.publish(owner, iid, job.job_id)
    assert (await repo.publish(owner, iid, job.job_id))["id"] == published["id"]
    job.ai_structured_output = {"recommendation": "Changed"}
    await session.flush()
    shared = await repo.publication(guest, iid, published["id"])
    assert shared["snapshot"]["result"]["recommendation"] == "Snapshot"
    assert "llm_input_payload" not in shared["snapshot"]["result"]
    second = (await repo.create(owner, "Outra equipe", "Responsável"))["id"]
    with pytest.raises(NotFoundError):
        await repo.publication(owner, second, published["id"])
    with pytest.raises(ForbiddenError):
        await repo.unpublish(guest, iid, published["id"])
    await repo.remove_member(owner, iid, guest)
    with pytest.raises(NotFoundError):
        await repo.publications(guest, iid, 100, 0)
    await repo.unpublish(owner, iid, published["id"])
    assert (await session.get(AnalysisJobModel, job.job_id)) is not None


async def test_delete_institution_preserves_personal_analysis_and_cleans_children(workspace):
    session, repo = workspace
    owner = uuid4()
    iid = (await repo.create(owner, "Equipe", "Responsável"))["id"]
    await repo.create_invite(owner, iid)
    job = await source_job(session, owner)
    await repo.publish(owner, iid, job.job_id)
    await repo.remove(owner, iid)
    for model in (InstitutionMemberModel, InstitutionInviteModel, InstitutionPublicationModel):
        assert (
            await session.scalar(
                select(func.count()).select_from(model).where(model.institution_id == iid)
            )
            == 0
        )
    assert await session.get(AnalysisJobModel, job.job_id) is not None


async def test_account_deletion_removes_only_own_memberships_copies_and_invites(workspace):
    session, repo = workspace
    owner, guest = uuid4(), uuid4()
    iid = (await repo.create(owner, "Equipe", "Responsável"))["id"]
    invite = await repo.create_invite(owner, iid)
    await repo.join(guest, invite["code"], "Convidado")
    await repo.update_member(
        owner,
        iid,
        guest,
        role=InstitutionRole.ADMIN,
        profile=InstitutionProfile.ADMINISTRATIVE,
        can_publish=True,
    )
    job = await source_job(session, owner)
    await repo.publish(owner, iid, job.job_id)
    await repo.prepare_account_deletion(owner)
    assert await repo.publications(guest, iid, 100, 0) == []
    assert await repo.list_for_user(owner, 100, 0) == []
    assert await repo.invites(guest, iid, 100, 0) == []
    assert (await repo.detail(guest, iid))["name"] == "Equipe"


async def test_concurrent_invite_consumption_and_last_admin_changes(_db_ready):
    owner, first, second = uuid4(), uuid4(), uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        repo = PgInstitutionRepository(session)
        iid = (await repo.create(owner, "Concurrent test", "Owner"))["id"]
        invite = await repo.create_invite(owner, iid)
        await session.commit()

    async def join(actor):
        async with factory() as session:
            try:
                await PgInstitutionRepository(session).join(actor, invite["code"], "Member")
                await session.commit()
                return actor
            except NotFoundError:
                await session.rollback()
                return None

    async def demote(actor):
        async with factory() as session:
            try:
                await PgInstitutionRepository(session).update_member(
                    actor,
                    iid,
                    actor,
                    role=InstitutionRole.MEMBER,
                    profile=InstitutionProfile.STRATEGIC,
                    can_publish=False,
                )
                await session.commit()
                return True
            except ConflictError:
                await session.rollback()
                return False

    try:
        joined = await asyncio.wait_for(asyncio.gather(join(first), join(second)), timeout=15)
        winners = [actor for actor in joined if actor is not None]
        assert len(winners) == 1
        async with factory() as session:
            await PgInstitutionRepository(session).update_member(
                owner,
                iid,
                winners[0],
                role=InstitutionRole.ADMIN,
                profile=InstitutionProfile.ADMINISTRATIVE,
                can_publish=True,
            )
            await session.commit()
        demoted = await asyncio.wait_for(
            asyncio.gather(demote(owner), demote(winners[0])), timeout=15
        )
        assert sum(demoted) == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(InstitutionMemberModel)
                    .where(
                        InstitutionMemberModel.institution_id == iid,
                        InstitutionMemberModel.role == "admin",
                    )
                )
                == 1
            )
    finally:
        async with factory() as session:
            row = await session.get(InstitutionModel, iid)
            if row:
                await session.delete(row)
            await session.commit()
