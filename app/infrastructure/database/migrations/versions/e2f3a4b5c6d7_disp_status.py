"""disp_status em analysis_jobs — depreciação reversível

Substitui o DELETE que o front pediu. Apagar na hora tira do usuário a chance de
desfazer e leva junto o `llm_input_payload`, que é a fonte do Terraform e de
qualquer reanálise — quem exclui por engano perde o pacote que já baixou.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `server_default="1"` e não default de aplicação: as análises que já existem
    # precisam nascer ativas, e um default só no Python deixaria todas em NULL.
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "disp_status",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
    )
    # Sem esta coluna não há como saber quando o mês de carência começou — a
    # regra "apaga depois de 1 mês" fica sem âncora.
    op.add_column(
        "analysis_jobs",
        sa.Column("disp_status_updated_at", sa.DateTime(), nullable=True),
    )
    # A listagem passa a filtrar por (user_id, disp_status); o expurgo varre por
    # (disp_status, disp_status_updated_at).
    # A idempotência passa a ignorar as depreciadas: depreciar um job travado em
    # PENDING precisa liberar o report, senão o usuário "exclui" e continua sem
    # conseguir reanalisar.
    op.drop_index("uq_analysis_jobs_report_ativo", table_name="analysis_jobs")
    op.create_index(
        "uq_analysis_jobs_report_ativo",
        "analysis_jobs",
        ["user_id", "report_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','PROCESSING') AND disp_status = 1"),
    )
    op.create_index(
        "ix_analysis_jobs_disp",
        "analysis_jobs",
        ["user_id", "disp_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_jobs_disp", table_name="analysis_jobs")
    op.drop_index("uq_analysis_jobs_report_ativo", table_name="analysis_jobs")
    op.create_index(
        "uq_analysis_jobs_report_ativo",
        "analysis_jobs",
        ["user_id", "report_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','PROCESSING')"),
    )
    op.drop_column("analysis_jobs", "disp_status_updated_at")
    op.drop_column("analysis_jobs", "disp_status")
