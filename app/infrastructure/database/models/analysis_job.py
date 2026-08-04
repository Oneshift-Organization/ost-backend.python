from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class AnalysisJobModel(Base):
    __tablename__ = "analysis_jobs"

    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    report_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    external_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_structured_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    llm_input_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_analysis_jobs_external_id", "external_id"),
        # IDEMPOTÊNCIA NO BANCO, não na rota. Um retry do agente — comportamento
        # normal dele — gerava uma segunda análise PAGA. Com o índice parcial, o
        # próprio Postgres impede um segundo job ativo para o mesmo report, e a
        # garantia vale para /start, /legacy/start e qualquer rota futura.
        Index(
            "uq_analysis_jobs_report_ativo",
            "user_id",
            "report_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','PROCESSING')"),
        ),
        # Chave de idempotência fornecida pelo cliente.
        Index(
            "uq_analysis_jobs_external_id",
            "user_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )
