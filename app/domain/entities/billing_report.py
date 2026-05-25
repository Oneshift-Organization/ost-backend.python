from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.core.exceptions import ValidationError
from app.domain.value_objects.cloud_provider import CloudProvider


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class BillingReport:
    """Parent entity. One per uploaded file.

    Lifecycle:
        QUEUED → PROCESSING → DONE | FAILED

    The actual billing line items live in `BillingItem` rows linked by `id`.
    """

    user_id: UUID
    provider: CloudProvider | None = None
    schema_version: str | None = None
    source_filename: str | None = None
    status: JobStatus = JobStatus.QUEUED
    error_message: str | None = None
    row_count: int = 0
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValidationError("user_id must be UUID")

    def mark_processing(self) -> None:
        if self.status is not JobStatus.QUEUED:
            raise ValidationError(f"cannot move to processing from {self.status}")
        self.status = JobStatus.PROCESSING
        self.updated_at = datetime.now(UTC)

    def mark_done(self, *, row_count: int, provider: CloudProvider, schema_version: str) -> None:
        if self.status is not JobStatus.PROCESSING:
            raise ValidationError(f"cannot move to done from {self.status}")
        self.status = JobStatus.DONE
        self.row_count = row_count
        self.provider = provider
        self.schema_version = schema_version
        self.updated_at = datetime.now(UTC)

    def mark_failed(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.error_message = error[:2000]
        self.updated_at = datetime.now(UTC)
