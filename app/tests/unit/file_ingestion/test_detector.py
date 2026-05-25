from pathlib import Path

import pytest

from app.core.exceptions import ProviderDetectionError
from app.infrastructure.file_ingestion.detector import detect_parser
from app.infrastructure.file_ingestion.parsers import (
    AWSParser,
    AzureParser,
    GCPParser,
    OCIParser,
)


def test_detector_recognizes_aws_by_content(aws_cur2_csv, user_id, job_id):
    parser = detect_parser(aws_cur2_csv, user_id, job_id)
    assert isinstance(parser, AWSParser)


def test_detector_recognizes_azure_by_content(azure_payg_csv, user_id, job_id):
    parser = detect_parser(azure_payg_csv, user_id, job_id)
    assert isinstance(parser, AzureParser)


def test_detector_recognizes_gcp_by_content(gcp_billing_json, user_id, job_id):
    parser = detect_parser(gcp_billing_json, user_id, job_id)
    assert isinstance(parser, GCPParser)


def test_detector_recognizes_oci_by_content(oci_cost_csv, user_id, job_id):
    parser = detect_parser(oci_cost_csv, user_id, job_id)
    assert isinstance(parser, OCIParser)


def test_detector_rejects_unknown_content(tmp_path: Path, user_id, job_id):
    fake = tmp_path / "garbage.csv"
    fake.write_text("foo,bar,baz\n1,2,3\n")
    with pytest.raises(ProviderDetectionError):
        detect_parser(fake, user_id, job_id)


def test_detector_ignores_extension_renaming(tmp_path: Path, aws_cur2_csv, user_id, job_id):
    """Renaming the file does not change detection — content rules."""
    renamed = tmp_path / "totally_not_aws.txt"
    renamed.write_bytes(aws_cur2_csv.read_bytes())
    parser = detect_parser(renamed, user_id, job_id)
    assert isinstance(parser, AWSParser)
