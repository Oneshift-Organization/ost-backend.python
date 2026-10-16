"""ciclo de vida do preço

Estado e score em cada ponto de preço, as observações que não são o sync oficial
(troca de valor, billing, agente) e as regras do ciclo em tabela
(PLANO_CICLO_DE_VIDA_DO_PRECO, P3.1). Nada lê o estado para decidir número.

Rode ANTES do deploy do código: o modelo de `cloud_price_points` passa a ler as
colunas novas, e o código novo sem elas quebra toda leitura de preço. A migração
é aditiva (colunas anuláveis, tabelas novas), então o código antigo segue
funcionando com ela aplicada.

Os valores iniciais das regras e dos pesos são os do plano (§4) e moram nas
tabelas, não no código.

Revision ID: c3d4e5f6a7b8
Revises: e7f8a9b0c1d2
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c3d4e5f6a7b8"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None

# (padrão do tipo, padrão do nome, meia-vida em dias). A regra mais específica vence.
_REGRAS = (
    ("*", None, 90),
    ("virtual_server", None, 90),
    ("database", None, 90),
    ("storage_*", None, 180),
    ("object_storage", None, 180),
    ("network_*", None, 180),
    ("inter_zone_transfer", None, 180),
    ("load_balancer*", None, 180),
    ("nat_gateway*", None, 180),
    ("public_ip", None, 180),
    ("vpn", None, 180),
    ("*", "*preview*", 30),
)
# (fonte, peso, confirma, descrição)
_PESOS = (
    ("oficial", "1", True, "API de preço do provedor (sync)"),
    ("billing", "0.9", True, "Preço de lista na fatura de um cliente, sob demanda (P3.3)"),
    ("referencia", "0.7", True, "Valor conferido na página oficial e digitado no seed"),
    ("agente", "0", False, "Resposta da IA: aparece ao lado do preço ADUBO, nunca o revive"),
)


def upgrade() -> None:
    op.add_column("cloud_price_points", sa.Column("estado", sa.String(20), nullable=True))
    op.add_column(
        "cloud_price_points", sa.Column("confianca_score", sa.Numeric(5, 4), nullable=True)
    )
    op.add_column(
        "cloud_price_points",
        sa.Column("confirmado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("cloud_price_points", sa.Column("ultima_fonte", sa.String(20), nullable=True))
    op.add_column(
        "cloud_price_points",
        sa.Column("versao", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "price_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("sku_key", sa.String(120), nullable=False),
        sa.Column("region", sa.String(60), nullable=False),
        sa.Column("fonte", sa.String(20), nullable=False),
        sa.Column("preco", sa.Numeric(18, 8), nullable=False),
        sa.Column("moeda", sa.String(3), nullable=False),
        sa.Column("unidade", sa.String(30), nullable=False),
        sa.Column("sob_demanda", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("observado_em", sa.DateTime(timezone=True), nullable=False),
        # Hash com sal do usuário, só para contar quórum (P3.4); nunca o id
        sa.Column("usuario_hash", sa.String(64), nullable=True),
        sa.Column("detalhe", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_price_observations_ponto", "price_observations", ["provider", "sku_key", "region"]
    )

    regras = op.create_table(
        "price_lifecycle_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("padrao_tipo", sa.String(100), nullable=False),
        sa.Column("padrao_nome", sa.String(200), nullable=True),
        sa.Column("meia_vida_dias", sa.Numeric(8, 2), nullable=False),
        sa.Column("limiar_vivo", sa.Numeric(5, 4), nullable=False, server_default="0.8"),
        sa.Column("limiar_maduro", sa.Numeric(5, 4), nullable=False, server_default="0.6"),
        sa.Column("limiar_decomposicao", sa.Numeric(5, 4), nullable=False, server_default="0.3"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_price_lifecycle_rules_padrao",
        "price_lifecycle_rules",
        ["padrao_tipo", "padrao_nome"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    pesos = op.create_table(
        "price_source_weights",
        sa.Column("fonte", sa.String(20), primary_key=True),
        sa.Column("peso", sa.Numeric(5, 4), nullable=False),
        sa.Column("confirma", sa.Boolean(), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.bulk_insert(
        regras,
        [
            {"id": uuid.uuid4(), "padrao_tipo": t, "padrao_nome": n, "meia_vida_dias": d}
            for t, n, d in _REGRAS
        ],
    )
    op.bulk_insert(
        pesos,
        [{"fonte": f, "peso": p, "confirma": c, "descricao": desc} for f, p, c, desc in _PESOS],
    )


def downgrade() -> None:
    op.drop_table("price_source_weights")
    op.drop_index("uq_price_lifecycle_rules_padrao", table_name="price_lifecycle_rules")
    op.drop_table("price_lifecycle_rules")
    op.drop_index("ix_price_observations_ponto", table_name="price_observations")
    op.drop_table("price_observations")
    for coluna in ("versao", "ultima_fonte", "confirmado_em", "confianca_score", "estado"):
        op.drop_column("cloud_price_points", coluna)
