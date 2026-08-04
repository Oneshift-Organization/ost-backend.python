"""job lifecycle (started_at/updated_at) + idempotência de análise

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-08-04 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a6b7c8d9e0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analysis_jobs', sa.Column('started_at', sa.DateTime(), nullable=True))
    op.add_column('analysis_jobs', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # Jobs pré-existentes: sem histórico de transição, usamos created_at como
    # referência para o reaper não os tratar como "travados agora".
    op.execute("UPDATE analysis_jobs SET updated_at = created_at WHERE updated_at IS NULL")

    # Antes de criar o índice único parcial, encerra duplicatas ativas que já
    # existem — senão a criação falha. Mantém o job mais recente por report.
    op.execute(
        """
        UPDATE analysis_jobs SET status = 'FAILED',
               error = 'encerrado ao aplicar idempotência: job ativo duplicado'
        WHERE status IN ('PENDING','PROCESSING')
          AND job_id NOT IN (
            SELECT DISTINCT ON (user_id, report_id) job_id
            FROM analysis_jobs
            WHERE status IN ('PENDING','PROCESSING')
            ORDER BY user_id, report_id, created_at DESC
          )
        """
    )
    # Idem para external_id repetido no mesmo usuário.
    op.execute(
        """
        UPDATE analysis_jobs SET status = 'FAILED',
               error = 'encerrado ao aplicar idempotência: external_id duplicado'
        WHERE external_id IS NOT NULL
          AND job_id NOT IN (
            SELECT DISTINCT ON (user_id, external_id) job_id
            FROM analysis_jobs
            WHERE external_id IS NOT NULL
            ORDER BY user_id, external_id, created_at DESC
          )
        """
    )

    op.create_index(
        'uq_analysis_jobs_report_ativo',
        'analysis_jobs',
        ['user_id', 'report_id'],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','PROCESSING')"),
    )
    op.create_index(
        'uq_analysis_jobs_external_id',
        'analysis_jobs',
        ['user_id', 'external_id'],
        unique=True,
        postgresql_where=sa.text('external_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_analysis_jobs_external_id', table_name='analysis_jobs')
    op.drop_index('uq_analysis_jobs_report_ativo', table_name='analysis_jobs')
    op.drop_column('analysis_jobs', 'updated_at')
    op.drop_column('analysis_jobs', 'started_at')
