"""add_billing_report_alias

Nome dado pelo usuário ao billing (M9). Sem alias, a tela mostra o nome do arquivo.
Coluna nula, sem preenchimento. Rode antes do deploy do código: o modelo novo lê a
coluna em toda consulta de billing; o código antigo segue funcionando com ela.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_reports", sa.Column("alias", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("billing_reports", "alias")
