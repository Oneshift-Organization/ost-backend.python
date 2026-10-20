"""recepção da análise do agente: bruto + sugestões

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-11 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # O que o agente mandou, INTOCADO. `ai_structured_output` guarda o resultado
    # já corrigido pela autoridade do backend; sem o bruto ao lado não há como
    # medir o quanto o modelo erra, nem justificar depois por que um número
    # mudou na tela.
    op.add_column(
        'analysis_jobs',
        sa.Column('agent_raw_output', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Contestações, oportunidades e riscos que o agente levantou, com
    # `efeito_backend` preenchido por nós. Vai para a tela como label de
    # sugestões e serve de fila para o sync de preços.
    op.add_column(
        'analysis_jobs',
        sa.Column('sugestoes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('analysis_jobs', 'sugestoes')
    op.drop_column('analysis_jobs', 'agent_raw_output')
