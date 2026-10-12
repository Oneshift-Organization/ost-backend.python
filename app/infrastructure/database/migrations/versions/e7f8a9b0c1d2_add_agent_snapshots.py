"""snapshots do agente e envios

O agente lê um snapshot gravado e envia a análise apontando para ele
(PLANO_SNAPSHOT_AGENTE). Só tabelas novas: nada do que existe muda, e as rotas
que as usam ficam atrás de AGENT_SNAPSHOT_ENABLED (padrão false), então dá para
fazer o deploy antes desta migração.

Exige PostgreSQL 15+ (`NULLS NOT DISTINCT` no índice de reaproveitamento).

Revision ID: e7f8a9b0c1d2
Revises: a1b2c3d4e5f7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "e7f8a9b0c1d2"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("billing_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inventory_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inventory_marker", sa.String(200), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("versions", JSONB(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("pools_sem_equivalente", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_agent_snapshots_conteudo",
        "agent_snapshots",
        ["user_id", "report_id", "inventory_id", "fingerprint"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_agent_snapshots_expires_at", "agent_snapshots", ["expires_at"])

    op.create_table(
        "agent_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("agent_raw", JSONB(), nullable=False),
        sa.Column("analysis", JSONB(), nullable=False),
        sa.Column("campos_sobrescritos", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("snapshot_id", "request_hash", name="uq_agent_submissions_conteudo"),
        sa.UniqueConstraint("snapshot_id", "revision", name="uq_agent_submissions_revisao"),
    )
    op.create_index(
        "ix_agent_submissions_user_report_created",
        "agent_submissions",
        ["user_id", "report_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_submissions_user_report_created", table_name="agent_submissions")
    op.drop_table("agent_submissions")
    op.drop_index("ix_agent_snapshots_expires_at", table_name="agent_snapshots")
    op.drop_index("uq_agent_snapshots_conteudo", table_name="agent_snapshots")
    op.drop_table("agent_snapshots")
