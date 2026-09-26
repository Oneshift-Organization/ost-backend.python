"""XLSX format support for the 4 provider parsers.

We don't commit binary .xlsx fixtures — the conftest converts the existing CSV
fixtures to XLSX in tmp_path. The point of these tests is that going through
the XLSX code path preserves: provider detection by content, Decimal-typed
cost columns, and the same row count as the CSV original.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.infrastructure.file_ingestion.detector import detect_parser
from app.infrastructure.file_ingestion.parsers import (
    AWSParser,
    AzureParser,
    OCIParser,
)


def test_detector_recognizes_xlsx_as_aws(aws_cur2_xlsx, user_id, job_id):
    parser = detect_parser(aws_cur2_xlsx, user_id, job_id)
    assert isinstance(parser, AWSParser)


def test_detector_recognizes_xlsx_as_azure(azure_payg_xlsx, user_id, job_id):
    parser = detect_parser(azure_payg_xlsx, user_id, job_id)
    assert isinstance(parser, AzureParser)


def test_detector_recognizes_xlsx_as_oci(oci_cost_xlsx, user_id, job_id):
    parser = detect_parser(oci_cost_xlsx, user_id, job_id)
    assert isinstance(parser, OCIParser)


def test_aws_xlsx_round_trip(aws_cur2_xlsx: Path, aws_cur2_csv: Path, user_id, job_id):
    """XLSX-fed AWS parse must yield the same row count and Decimal cost as CSV-fed."""
    parser_csv = AWSParser(user_id=user_id, job_id=job_id)
    parser_xlsx = AWSParser(user_id=user_id, job_id=job_id)
    df_csv = parser_csv.parse(aws_cur2_csv)
    df_xlsx = parser_xlsx.parse(aws_cur2_xlsx)

    assert len(df_xlsx) == len(df_csv)
    assert all(isinstance(c, Decimal) for c in df_xlsx["cost"])
    # 12.345600 (faturado) e 11.000000 (líquido) passam exatos pelo XLSX.
    assert df_xlsx.iloc[0]["cost_billed"] == Decimal("12.345600")
    assert df_xlsx.iloc[0]["cost"] == Decimal("11.000000")


def test_azure_xlsx_decimal_preserved(azure_payg_xlsx, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    df = parser.parse(azure_payg_xlsx)
    assert all(isinstance(c, Decimal) for c in df["cost"])
    assert df.iloc[0]["cost"] == Decimal("18.240000")


def test_oci_xlsx_correction_logic_still_applied(oci_cost_xlsx, user_id, job_id):
    """ref-003 must be REPLACED by ref-004 even when input comes from XLSX."""
    parser = OCIParser(user_id=user_id, job_id=job_id)
    df = parser.parse(oci_cost_xlsx)
    refs = {r.get("lineItem/referenceNo") for r in df["raw_payload"]}
    assert "ref-003" not in refs
    assert "ref-004" in refs
