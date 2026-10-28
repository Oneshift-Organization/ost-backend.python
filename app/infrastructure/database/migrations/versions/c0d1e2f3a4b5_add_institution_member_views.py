"""Visões combináveis por membro (financeira, técnica, estratégica) no lugar do perfil único.

`profile` continua gravado (derivado das visões) para a versão anterior seguir lendo.
O default do banco cobre a inserção feita por código que ainda não conhece a coluna.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "institution_members",
        sa.Column(
            "views",
            postgresql.ARRAY(sa.String(20)),
            nullable=False,
            server_default=sa.text("ARRAY['strategic']::varchar[]"),
        ),
    )
    op.execute(
        """
        UPDATE institution_members SET views = CASE profile
            WHEN 'administrative' THEN ARRAY['financial','technical','strategic']::varchar[]
            WHEN 'financial' THEN ARRAY['financial']::varchar[]
            WHEN 'economist' THEN ARRAY['financial']::varchar[]
            WHEN 'technical' THEN ARRAY['technical']::varchar[]
            ELSE ARRAY['strategic']::varchar[]
        END
        """
    )
    op.create_check_constraint(
        "ck_institution_member_views",
        "institution_members",
        "cardinality(views) >= 1"
        " AND views <@ ARRAY['financial','technical','strategic']::varchar[]",
    )


def downgrade() -> None:
    op.drop_constraint("ck_institution_member_views", "institution_members", type_="check")
    op.drop_column("institution_members", "views")
