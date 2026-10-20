"""fila de curadoria de nomes de serviço

Fatura como fonte, etapa A (decisão 2.11 de 17/10): nomes que as faturas trazem e o
DE-PARA não reconhece; a curadoria da OneShift aprova o apelido. Tabela nova: o
código antigo segue funcionando com ela, e o novo funciona sem ela (sem apelido
aprovado). Rode antes do deploy do código.

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "f7a8b9c0d1e2"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_name_suggestions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("nome_na_fatura", sa.String(200), nullable=False),
        sa.Column("chave", sa.String(200), nullable=False),
        sa.Column("alvo_sugerido", sa.String(200), nullable=True),
        sa.Column("semelhanca", sa.Float(), nullable=True),
        sa.Column("faturas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clientes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pendente"),
        sa.Column("alvo", sa.String(200), nullable=True),
        sa.Column("decidido_por", sa.String(200), nullable=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observacao", sa.String(500), nullable=True),
        sa.Column(
            "visto_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("provider", "chave", name="uq_service_name_suggestions"),
    )


def downgrade() -> None:
    op.drop_table("service_name_suggestions")
