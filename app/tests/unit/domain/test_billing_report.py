from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ValidationError
from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.value_objects.cloud_provider import CloudProvider


def test_create_defaults_to_queued():
    r = BillingReport(user_id=uuid4())
    assert r.status is JobStatus.QUEUED
    assert r.row_count == 0
    assert isinstance(r.id, UUID)


def test_rejects_non_uuid_user_id():
    with pytest.raises(ValidationError):
        BillingReport(user_id="not-uuid")  # type: ignore[arg-type]


def test_state_machine_queued_to_processing_to_done():
    r = BillingReport(user_id=uuid4())
    r.mark_processing()
    assert r.status is JobStatus.PROCESSING
    r.mark_done(row_count=42, provider=CloudProvider.AWS, schema_version="AWS_CUR2.0")
    assert r.status is JobStatus.DONE
    assert r.row_count == 42
    assert r.provider is CloudProvider.AWS


def test_cannot_skip_processing_step():
    r = BillingReport(user_id=uuid4())
    with pytest.raises(ValidationError):
        r.mark_done(row_count=1, provider=CloudProvider.AWS, schema_version="x")


def test_failed_is_terminal_marker():
    r = BillingReport(user_id=uuid4())
    r.mark_failed("disk full")
    assert r.status is JobStatus.FAILED
    assert r.error_message == "disk full"
