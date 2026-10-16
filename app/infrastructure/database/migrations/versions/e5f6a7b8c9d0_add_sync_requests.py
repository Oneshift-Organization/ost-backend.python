"""pedidos de sync vindos da contestação do agente

A contestação de preço que não se verifica contra o snapshot (preço velho, de
outra região, de referência) vira pedido de sync (lote da autoridade v4, V4.1).
`scripts/sync_prices.py --pedidos` atende os abertos. Tabela nova: o código
antigo segue funcionando com ela; rode antes do deploy do código.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pedidos_de_sync",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("region", sa.String(60), nullable=True),
        sa.Column("service_type", sa.String(100), nullable=False),
        sa.Column("sku_key", sa.String(120), nullable=False),
        sa.Column("motivo", sa.String(300), nullable=False),
        sa.Column("snapshot_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("atendido_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pedidos_de_sync_abertos",
        "pedidos_de_sync",
        ["provider", "region"],
        postgresql_where=sa.text("atendido_em IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_pedidos_de_sync_abertos", table_name="pedidos_de_sync")
    op.drop_table("pedidos_de_sync")
