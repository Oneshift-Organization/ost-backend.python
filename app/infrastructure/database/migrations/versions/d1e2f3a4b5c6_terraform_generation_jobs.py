"""terraform_generation_jobs

Tabela própria para a geração de Terraform. Ver o docstring do modelo para o
porquê de não serem colunas em `analysis_jobs`.

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d1e2f3a4b5c6"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "terraform_generation_jobs",
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_job_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("target_provider", sa.String(length=20), nullable=False),
        sa.Column("region", sa.String(length=40), nullable=False),
        sa.Column("project_name", sa.String(length=60), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=True),
        sa.Column("blueprint", postgresql.JSONB(), nullable=True),
        sa.Column("generated_files", postgresql.JSONB(), nullable=True),
        sa.Column("validation_result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("generation_id"),
    )
    op.create_index(
        "ix_tf_gen_user", "terraform_generation_jobs", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_tf_gen_analysis", "terraform_generation_jobs", ["analysis_job_id"]
    )
    # O reaper do startup varre por (status, created_at) para encerrar geração
    # órfã — a que ficou em PROCESSING quando o processo web reiniciou.
    op.create_index(
        "ix_tf_gen_status", "terraform_generation_jobs", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tf_gen_status", table_name="terraform_generation_jobs")
    op.drop_index("ix_tf_gen_analysis", table_name="terraform_generation_jobs")
    op.drop_index("ix_tf_gen_user", table_name="terraform_generation_jobs")
    op.drop_table("terraform_generation_jobs")
