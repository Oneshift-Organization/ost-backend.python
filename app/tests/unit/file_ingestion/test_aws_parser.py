from decimal import Decimal

import pandas as pd

from app.infrastructure.file_ingestion.parsers import AWSParser


def test_aws_parser_can_parse_cur2(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    sample = aws_cur2_csv.read_bytes()[:2048]
    assert parser.can_parse(aws_cur2_csv, sample) is True


def test_aws_parser_produces_decimal_cost(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_cur2_csv)
    assert not df.empty
    assert all(isinstance(c, Decimal) for c in df["cost"])
    # First row: 12.345600 in fixture
    assert df.iloc[0]["cost"] == Decimal("12.345600")


def test_aws_parser_marks_estimate_when_invoice_missing(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_cur2_csv)
    # li-004 has empty bill_invoice_id → is_estimate=True
    li004 = df[df["raw_payload"].apply(lambda r: r.get("identity_line_item_id") == "li-004")]
    assert bool(li004.iloc[0]["is_estimate"]) is True


def test_aws_parser_carries_user_id(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_cur2_csv)
    assert (df["user_id"] == str(user_id)).all()


def test_aws_parser_no_float_columns(aws_cur2_csv, user_id, job_id):
    """Cost column must NOT be float64 — float in DataFrames is forbidden."""
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_cur2_csv)
    assert df["cost"].dtype == object  # Decimal stored as Python objects


def test_aws_parser_preserves_raw_payload(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_cur2_csv)
    assert isinstance(df.iloc[0]["raw_payload"], dict)
    assert df.iloc[0]["raw_payload"]["identity_line_item_id"] == "li-001"


def test_aws_parser_schema_version(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    _ = parser.parse(aws_cur2_csv)
    assert parser.get_schema_version() == "AWS_CUR2.0"


def test_aws_parser_billing_period_yyyy_mm(aws_cur2_csv, user_id, job_id):
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_cur2_csv)
    assert all(pd.Series(df["billing_period"]).str.match(r"^\d{4}-\d{2}$"))
