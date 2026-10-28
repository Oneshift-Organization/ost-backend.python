from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class InstitutionModel(Base):
    __tablename__ = "institutions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstitutionMemberModel(Base):
    __tablename__ = "institution_members"
    institution_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))
    profile: Mapped[str] = mapped_column(String(20))
    views: Mapped[list[str]] = mapped_column(ARRAY(String(20)))
    can_publish: Mapped[bool] = mapped_column(Boolean)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="ck_institution_member_role"),
        CheckConstraint(
            "profile IN ('administrative','financial','strategic','economist','technical')",
            name="ck_institution_member_profile",
        ),
        CheckConstraint(
            "cardinality(views) >= 1"
            " AND views <@ ARRAY['financial','technical','strategic']::varchar[]",
            name="ck_institution_member_views",
        ),
    )


class InstitutionInviteModel(Base):
    __tablename__ = "institution_invites"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InstitutionPublicationModel(Base):
    __tablename__ = "institution_publications"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), index=True
    )
    published_by: Mapped[UUID] = mapped_column(Uuid, index=True)
    source_job_id: Mapped[UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(500))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("institution_id", "source_job_id", name="uq_institution_publication_job"),
    )
