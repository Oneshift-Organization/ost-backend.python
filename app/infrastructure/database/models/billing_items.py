from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base

NUMERIC_18_6 = Numeric(18, 6)


class BillingItemModel(Base):
    __tablename__ = "billing_items"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("billing_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    service: Mapped[str] = mapped_column(String(200), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)

    cost: Mapped[Decimal] = mapped_column(NUMERIC_18_6, nullable=False)
    cost_discounted: Mapped[Decimal | None] = mapped_column(NUMERIC_18_6, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    usage_amount: Mapped[Decimal | None] = mapped_column(NUMERIC_18_6, nullable=True)
    usage_unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    usage_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)

    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_correction: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_estimate: Mapped[bool] = mapped_column(default=False, nullable=False)
    line_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_billing_items_user_period_service",
            "user_id",
            "billing_period",
            "service",
        ),
        Index("ix_billing_items_report_id", "report_id"),
    )
