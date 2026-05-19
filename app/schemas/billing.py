from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities.billing_report import JobStatus
from app.domain.value_objects.cloud_provider import CloudProvider


class BillingFileUploadResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID = Field(..., description="Use this id to poll job status.")
    status: JobStatus
    source_filename: str
    created_at: datetime


class BillingJobStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    status: JobStatus
    provider: CloudProvider | None = None
    schema_version: str | None = None
    row_count: int = 0
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
