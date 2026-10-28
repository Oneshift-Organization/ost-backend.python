from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.domain.entities.institution import (
    ALL_VIEWS,
    InstitutionProfile,
    InstitutionRole,
    InstitutionView,
    Membership,
    PublicationSource,
    SharedAnalysisAccess,
    profile_of_views,
    protect_last_admin,
    publication_snapshot,
)
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.models.institution import (
    InstitutionInviteModel as Invite,
)
from app.infrastructure.database.models.institution import (
    InstitutionMemberModel as Member,
)
from app.infrastructure.database.models.institution import (
    InstitutionModel as Institution,
)
from app.infrastructure.database.models.institution import (
    InstitutionPublicationModel as Publication,
)


def _membership(row: Member) -> Membership:
    return Membership(
        row.institution_id,
        row.user_id,
        row.display_name,
        InstitutionRole(row.role),
        InstitutionProfile(row.profile),
        row.can_publish,
        row.joined_at,
        frozenset(InstitutionView(view) for view in row.views or ()),
    )


def _member_dict(row: Member) -> dict[str, Any]:
    member = {
        key: getattr(row, key)
        for key in (
            "institution_id",
            "user_id",
            "display_name",
            "role",
            "profile",
            "can_publish",
            "joined_at",
        )
    }
    # O que o membro vê de fato: administrador, a análise inteira
    member["views"] = sorted(_membership(row).visible_views())
    return member


def _stored(views: frozenset[InstitutionView]) -> list[str]:
    return sorted(view.value for view in views)


def _invite_dict(row: Invite) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in ("id", "created_at", "expires_at", "consumed_at", "revoked_at")
    }


def _publication_dict(row: Publication) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "institution_id",
            "published_by",
            "source_job_id",
            "title",
            "snapshot",
            "created_at",
        )
    }


class PgInstitutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _lock_actor(self, actor: UUID) -> None:
        key = int.from_bytes(hashlib.sha256(actor.bytes).digest()[:8], signed=True)
        await self._s.execute(select(func.pg_advisory_xact_lock(key)))

    async def _access(self, actor: UUID, institution_id: UUID) -> tuple[Institution, Member]:
        # All operations take the same parent lock: revocation, joining and sharing
        # cannot pass an authorization check against a concurrently removed member.
        institution = await self._s.scalar(
            select(Institution)
            .where(
                Institution.id == institution_id,
                select(Member.user_id)
                .where(Member.institution_id == Institution.id, Member.user_id == actor)
                .exists(),
            )
            .with_for_update()
        )
        if institution is None:
            raise NotFoundError("Instituição não encontrada.")
        member = await self._s.get(Member, (institution_id, actor), populate_existing=True)
        if member is None:
            raise NotFoundError("Instituição não encontrada.")
        return institution, member

    async def create(self, actor: UUID, name: str, display_name: str) -> dict[str, Any]:
        await self._lock_actor(actor)
        now = datetime.now(UTC)
        institution = Institution(id=uuid4(), name=name, created_at=now)
        self._s.add(institution)
        await self._s.flush()
        self._s.add(
            Member(
                institution_id=institution.id,
                user_id=actor,
                display_name=display_name,
                role="admin",
                profile="administrative",
                views=_stored(ALL_VIEWS),
                can_publish=True,
                joined_at=now,
            )
        )
        await self._s.flush()
        return await self.detail(actor, institution.id)

    async def list_for_user(self, actor: UUID, limit: int, offset: int) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                select(Institution, Member)
                .join(Member, Member.institution_id == Institution.id)
                .where(Member.user_id == actor)
                .order_by(Institution.created_at, Institution.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [
            {
                "id": inst.id,
                "name": inst.name,
                "created_at": inst.created_at,
                "membership": _member_dict(member),
            }
            for inst, member in rows
        ]

    async def detail(self, actor: UUID, institution_id: UUID) -> dict[str, Any]:
        institution, member = await self._access(actor, institution_id)
        return {
            "id": institution.id,
            "name": institution.name,
            "created_at": institution.created_at,
            "membership": _member_dict(member),
        }

    async def rename(self, actor: UUID, institution_id: UUID, name: str) -> None:
        institution, member = await self._access(actor, institution_id)
        _membership(member).require_admin()
        institution.name = name
        await self._s.flush()

    async def remove(self, actor: UUID, institution_id: UUID) -> None:
        institution, member = await self._access(actor, institution_id)
        _membership(member).require_admin()
        await self._s.delete(institution)
        await self._s.flush()

    async def members(
        self, actor: UUID, institution_id: UUID, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        await self._access(actor, institution_id)
        rows = await self._s.scalars(
            select(Member)
            .where(Member.institution_id == institution_id)
            .order_by(Member.joined_at, Member.user_id)
            .limit(limit)
            .offset(offset)
        )
        return [_member_dict(row) for row in rows]

    async def _target(self, institution_id: UUID, target: UUID) -> Member:
        member = await self._s.get(Member, (institution_id, target), populate_existing=True)
        if member is None:
            raise NotFoundError("Membro não encontrado.")
        return member

    async def _protect_admin(self, member: Member) -> None:
        count = await self._s.scalar(
            select(func.count())
            .select_from(Member)
            .where(Member.institution_id == member.institution_id, Member.role == "admin")
        )
        protect_last_admin(_membership(member), count or 0)

    async def update_member(
        self,
        actor: UUID,
        institution_id: UUID,
        target: UUID,
        *,
        role: InstitutionRole,
        views: frozenset[InstitutionView],
        can_publish: bool,
    ) -> None:
        _, caller = await self._access(actor, institution_id)
        _membership(caller).require_admin()
        member = await self._target(institution_id, target)
        if role != InstitutionRole.ADMIN:
            await self._protect_admin(member)
            await self._s.execute(
                update(Invite)
                .where(
                    Invite.institution_id == institution_id,
                    Invite.created_by == target,
                    Invite.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
        member.role, member.can_publish = role.value, can_publish
        member.views, member.profile = _stored(views), profile_of_views(views).value
        await self._s.flush()

    async def remove_member(self, actor: UUID, institution_id: UUID, target: UUID) -> None:
        _, caller = await self._access(actor, institution_id)
        if actor != target:
            _membership(caller).require_admin()
        member = await self._target(institution_id, target)
        await self._protect_admin(member)
        # A removed administrator must not retain valid invitation codes.
        await self._s.execute(
            update(Invite)
            .where(
                Invite.institution_id == institution_id,
                Invite.created_by == target,
                Invite.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._s.delete(member)
        await self._s.flush()

    async def create_invite(self, actor: UUID, institution_id: UUID) -> dict[str, Any]:
        _, member = await self._access(actor, institution_id)
        _membership(member).require_admin()
        now = datetime.now(UTC)
        code = secrets.token_urlsafe(24)
        invite = Invite(
            id=uuid4(),
            institution_id=institution_id,
            created_by=actor,
            token_hash=hashlib.sha256(code.encode()).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        self._s.add(invite)
        await self._s.flush()
        return {**_invite_dict(invite), "code": code}

    async def invites(
        self, actor: UUID, institution_id: UUID, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        _, member = await self._access(actor, institution_id)
        _membership(member).require_admin()
        rows = await self._s.scalars(
            select(Invite)
            .where(Invite.institution_id == institution_id)
            .order_by(Invite.created_at.desc(), Invite.id)
            .limit(limit)
            .offset(offset)
        )
        return [_invite_dict(row) for row in rows]

    async def revoke_invite(self, actor: UUID, institution_id: UUID, invite_id: UUID) -> None:
        _, member = await self._access(actor, institution_id)
        _membership(member).require_admin()
        row = await self._s.scalar(
            select(Invite).where(Invite.institution_id == institution_id, Invite.id == invite_id)
        )
        if row is None:
            raise NotFoundError("Convite não encontrado.")
        row.revoked_at = datetime.now(UTC)
        await self._s.flush()

    async def join(self, actor: UUID, code: str, display_name: str) -> dict[str, Any]:
        await self._lock_actor(actor)
        token_hash = hashlib.sha256(code.encode()).hexdigest()
        institution_id = await self._s.scalar(
            select(Invite.institution_id).where(Invite.token_hash == token_hash)
        )
        if institution_id is None:
            raise NotFoundError("Convite inválido, expirado ou já utilizado.")
        # Same lock order as all other mutations: institution, then invite/member.
        institution = await self._s.scalar(
            select(Institution).where(Institution.id == institution_id).with_for_update()
        )
        invite = await self._s.scalar(
            select(Invite)
            .where(Invite.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        now = datetime.now(UTC)
        if (
            institution is None
            or invite is None
            or invite.revoked_at is not None
            or invite.consumed_at is not None
            or invite.expires_at <= now
        ):
            raise NotFoundError("Convite inválido, expirado ou já utilizado.")
        if await self._s.get(Member, (institution_id, actor)) is not None:
            raise ConflictError("Você já participa desta instituição.")
        self._s.add(
            Member(
                institution_id=institution_id,
                user_id=actor,
                display_name=display_name,
                role="member",
                profile="strategic",
                views=[InstitutionView.STRATEGIC.value],
                can_publish=False,
                joined_at=now,
            )
        )
        invite.consumed_at = now
        await self._s.flush()
        return await self.detail(actor, institution_id)

    async def publish(self, actor: UUID, institution_id: UUID, job_id: UUID) -> dict[str, Any]:
        _, member = await self._access(actor, institution_id)
        _membership(member).require_publisher()
        source = (
            await self._s.execute(
                select(AnalysisJobModel, BillingReportModel)
                .join(BillingReportModel, BillingReportModel.id == AnalysisJobModel.report_id)
                .where(
                    AnalysisJobModel.job_id == job_id,
                    AnalysisJobModel.user_id == actor,
                    BillingReportModel.user_id == actor,
                    AnalysisJobModel.disp_status == 1,
                )
            )
        ).first()
        if source is None:
            raise NotFoundError("Análise pessoal não encontrada.")
        job, report = source
        if job.status != "COMPLETED" or not job.ai_structured_output:
            raise ConflictError("Somente análises concluídas podem ser compartilhadas.")
        existing = await self._s.scalar(
            select(Publication).where(
                Publication.institution_id == institution_id, Publication.source_job_id == job_id
            )
        )
        if existing is not None:
            return _publication_dict(existing)
        source_data = PublicationSource(
            job_id,
            report.id,
            report.alias or report.source_filename,
            report.provider,
            job.created_at,
            job.ai_structured_output,
        )
        publication = Publication(
            id=uuid4(),
            institution_id=institution_id,
            published_by=actor,
            source_job_id=job_id,
            title=source_data.title,
            snapshot=publication_snapshot(source_data),
            created_at=datetime.now(UTC),
        )
        self._s.add(publication)
        await self._s.flush()
        return _publication_dict(publication)

    async def publications(
        self, actor: UUID, institution_id: UUID, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        await self._access(actor, institution_id)
        rows = await self._s.scalars(
            select(Publication)
            .where(Publication.institution_id == institution_id)
            .order_by(Publication.created_at.desc(), Publication.id)
            .limit(limit)
            .offset(offset)
        )
        return [_publication_dict(row) for row in rows]

    async def _publication(self, institution_id: UUID, publication_id: UUID) -> Publication:
        row = await self._s.scalar(
            select(Publication).where(
                Publication.institution_id == institution_id, Publication.id == publication_id
            )
        )
        if row is None:
            raise NotFoundError("Publicação não encontrada.")
        return row

    async def publication(
        self, actor: UUID, institution_id: UUID, publication_id: UUID
    ) -> dict[str, Any]:
        await self._access(actor, institution_id)
        return _publication_dict(await self._publication(institution_id, publication_id))

    async def shared_analysis(
        self,
        actor: UUID,
        institution_id: UUID,
        publication_id: UUID,
        requested: frozenset[InstitutionView] | None = None,
    ) -> SharedAnalysisAccess:
        """Quem é membro abre a análise publicada, recortada pelas suas visões.

        `requested` restringe (o "Ver como"), nunca amplia. O autor vê a própria inteira.
        """
        _, member = await self._access(actor, institution_id)
        row = await self._publication(institution_id, publication_id)
        allowed = ALL_VIEWS if row.published_by == actor else _membership(member).visible_views()
        views = allowed & requested if requested is not None else allowed
        return SharedAnalysisAccess(row.published_by, row.source_job_id, views)

    async def unpublish(self, actor: UUID, institution_id: UUID, publication_id: UUID) -> None:
        _, member = await self._access(actor, institution_id)
        row = await self._publication(institution_id, publication_id)
        if row.published_by != actor:
            _membership(member).require_admin()
        await self._s.delete(row)
        await self._s.flush()

    async def prepare_account_deletion(self, actor: UUID) -> None:
        await self._lock_actor(actor)
        ids = list(
            await self._s.scalars(
                select(Member.institution_id)
                .where(Member.user_id == actor)
                .order_by(Member.institution_id)
            )
        )
        for institution_id in ids:
            _, member = await self._access(actor, institution_id)
            await self._protect_admin(member)
        await self._s.execute(delete(Publication).where(Publication.published_by == actor))
        await self._s.execute(delete(Invite).where(Invite.created_by == actor))
        await self._s.execute(delete(Member).where(Member.user_id == actor))
