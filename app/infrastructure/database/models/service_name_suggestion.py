"""Fila de curadoria de nomes de serviço (fatura como fonte, etapa A, decisão 2.11).

Um nome que as faturas trazem e o DE-PARA não reconhece. Só contagens: nenhum
identificador de cliente, conta, custo ou recurso chega aqui (o DE-PARA é de todos).
Aprovado, vira apelido da linha do DE-PARA escolhida pela curadoria da OneShift.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class ServiceNameSuggestionModel(Base):
    __tablename__ = "service_name_suggestions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    # Como a fatura escreve, e a chave normalizada (`service_key`) que casa
    nome_na_fatura: Mapped[str] = mapped_column(String(200), nullable=False)
    chave: Mapped[str] = mapped_column(String(200), nullable=False)
    alvo_sugerido: Mapped[str | None] = mapped_column(String(200), nullable=True)
    semelhanca: Mapped[float | None] = mapped_column(Float, nullable=True)
    faturas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clientes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pendente | aprovada | recusada
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pendente")
    alvo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decidido_por: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacao: Mapped[str | None] = mapped_column(String(500), nullable=True)
    visto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("provider", "chave", name="uq_service_name_suggestions"),)
