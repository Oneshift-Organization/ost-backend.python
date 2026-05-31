from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class SkuClassificationRuleModel(Base):
    __tablename__ = "sku_classification_rules"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    component_type: Mapped[str] = mapped_column(String(30), nullable=False)
    family: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, server_default="medium")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_sku_rules_provider_priority",
            "provider",
            "priority",
            postgresql_where=active,
        ),
    )
