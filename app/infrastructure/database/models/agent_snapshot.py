"""Tabelas do snapshot do agente (PLANO_SNAPSHOT_AGENTE, §3).

Apagar o relatório apaga os snapshots e, com eles, os envios (``ON DELETE
CASCADE``): é o que faz a exclusão de conta alcançar estas tabelas sem entrar na
lista de tabelas-raiz.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class AgentSnapshotModel(Base):
    __tablename__ = "agent_snapshots"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    report_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("billing_reports.id", ondelete="CASCADE"), nullable=False
    )
    inventory_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    inventory_marker: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pools_sem_equivalente: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_agent_snapshots_conteudo",
            "user_id",
            "report_id",
            "inventory_id",
            "fingerprint",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_agent_snapshots_expires_at", "expires_at"),
    )


class AgentSubmissionModel(Base):
    __tablename__ = "agent_submissions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    report_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    campos_sobrescritos: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("snapshot_id", "request_hash", name="uq_agent_submissions_conteudo"),
        UniqueConstraint("snapshot_id", "revision", name="uq_agent_submissions_revisao"),
        Index("ix_agent_submissions_user_report_created", "user_id", "report_id", "created_at"),
    )
