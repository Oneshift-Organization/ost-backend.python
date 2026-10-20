"""preço que some da API fica, marcado como ausente

O sync apagava o SKU que a coleta nova não trazia. Com o ciclo de vida
(PLANO_CICLO_DE_VIDA_DO_PRECO §6, decisão 4 do usuário em 16/10), ele fica e
envelhece até ADUBO: faturas podem seguir mostrando o SKU. A cotação continua
ignorando o ausente, como fazia ao apagar.

Rode ANTES do deploy do código: o modelo passa a ler a coluna. A migração é
aditiva, e o código antigo segue funcionando com ela aplicada.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_price_points",
        sa.Column("ausente_desde", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_price_points", "ausente_desde")
