"""Add opt-in institutional collaboration; personal tables remain unchanged.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "institution_members",
        sa.Column(
            "institution_id",
            sa.Uuid(),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("profile", sa.String(20), nullable=False),
        sa.Column("can_publish", sa.Boolean(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin','member')", name="ck_institution_member_role"),
        sa.CheckConstraint(
            "profile IN ('administrative','financial','strategic','economist','technical')",
            name="ck_institution_member_profile",
        ),
    )
    op.create_index("ix_institution_members_user_id", "institution_members", ["user_id"])
    op.create_table(
        "institution_invites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "institution_id",
            sa.Uuid(),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_institution_invites_institution_id", "institution_invites", ["institution_id"]
    )
    op.create_index("ix_institution_invites_created_by", "institution_invites", ["created_by"])
    op.create_table(
        "institution_publications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "institution_id",
            sa.Uuid(),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("published_by", sa.Uuid(), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "institution_id", "source_job_id", name="uq_institution_publication_job"
        ),
    )
    op.create_index(
        "ix_institution_publications_institution_id", "institution_publications", ["institution_id"]
    )
    op.create_index(
        "ix_institution_publications_published_by", "institution_publications", ["published_by"]
    )


def downgrade() -> None:
    op.drop_table("institution_publications")
    op.drop_table("institution_invites")
    op.drop_table("institution_members")
    op.drop_table("institutions")
