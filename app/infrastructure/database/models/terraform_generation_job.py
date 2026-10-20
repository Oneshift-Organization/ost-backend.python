from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class TerraformGenerationJobModel(Base):
    """Tabela própria, e não colunas em `analysis_jobs`.

    Os dois têm ciclos de vida diferentes: uma análise pode gerar vários pacotes
    de Terraform (regiões, ambientes, provedores diferentes) e um pacote pode
    falhar sem invalidar a análise. Guardar os arquivos em `analysis_jobs` faria
    a segunda geração sobrescrever a primeira.

    Não há índice único por análise, também de propósito — gerar duas vezes para
    ambientes diferentes é uso legítimo, ao contrário da análise, onde um retry
    duplicado custava uma chamada paga de LLM.
    """

    __tablename__ = "terraform_generation_jobs"

    generation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    analysis_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    report_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    target_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    region: Mapped[str] = mapped_column(String(40), nullable=False)
    project_name: Mapped[str] = mapped_column(String(60), nullable=False)

    request_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Blueprint e arquivos lado a lado: o primeiro explica o segundo. Sem o
    # blueprint gravado não há como responder "por que esta máquina?" depois.
    blueprint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_files: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, onupdate=func.now()
    )

    __table_args__ = (
        # O reaper varre por (status, created_at) e o GET filtra por usuário.
        Index("ix_tf_gen_user", "user_id", "created_at"),
        Index("ix_tf_gen_analysis", "analysis_job_id"),
        Index("ix_tf_gen_status", "status", "created_at"),
    )
