"""AWS Cost Explorer (pivoted-by-service) parser.

The fixture mimics a real export: UTF-8 BOM, TAB-separated, comma-decimal,
a 'Service total' rollup row that must be dropped, and a 'Total costs($)'
sum column that must NOT be emitted as a service.
"""

from __future__ import annotations

from decimal import Decimal

from app.infrastructure.file_ingestion.detector import detect_parser
from app.infrastructure.file_ingestion.parsers import AWSCostExplorerParser
from app.infrastructure.file_ingestion.parsers.aws_cost_explorer_parser import (
    _locale_to_decimal,
)


def test_detector_picks_cost_explorer(aws_ce_tsv, user_id, job_id):
    parser = detect_parser(aws_ce_tsv, user_id, job_id)
    assert isinstance(parser, AWSCostExplorerParser)


def test_cost_explorer_unpivots_to_long_form(aws_ce_tsv, user_id, job_id):
    """Fixture has 2 date rows x 3 service columns = 6 records (Total dropped)."""
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_tsv)
    assert len(df) == 6


def test_cost_explorer_drops_service_total_row(aws_ce_tsv, user_id, job_id):
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_tsv)
    # No row should originate from the 'Service total' summary
    assert all(r.get("date") != "Service total" for r in df["raw_payload"])


def test_cost_explorer_decimal_comma_preserved(aws_ce_tsv, user_id, job_id):
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_tsv)
    assert all(isinstance(c, Decimal) for c in df["cost"])
    # 60,2500 from EC2-Instances on 2026-04-01
    ec2_d1 = df[(df["service"] == "EC2-Instances") & (df["billing_period"] == "2026-04")][
        "cost"
    ].tolist()
    assert Decimal("60.2500") in ec2_d1


def test_cost_explorer_schema_version(aws_ce_tsv, user_id, job_id):
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    parser.parse(aws_ce_tsv)
    assert parser.get_schema_version() == "AWS_CE_DAILY_BY_SERVICE"


def test_cost_explorer_provider_is_aws(aws_ce_tsv, user_id, job_id):
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_tsv)
    assert (df["provider"] == "aws").all()
    assert (df["currency"] == "USD").all()


def test_cost_explorer_strips_dollar_suffix_from_service(aws_ce_tsv, user_id, job_id):
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_tsv)
    assert "EC2-Instances" in set(df["service"])
    assert "EC2-Instances($)" not in set(df["service"])
    # service_raw keeps the original column name for traceability
    assert "EC2-Instances($)" in set(df["service_raw"])


def test_locale_parser_handles_pt_br_decimal():
    assert _locale_to_decimal("1369,5165049893") == Decimal("1369.5165049893")


def test_locale_parser_handles_en_us_decimal():
    assert _locale_to_decimal("1369.5165049893") == Decimal("1369.5165049893")


def test_locale_parser_handles_thousands_separator():
    # pt-BR style: 1.369,5165 — dot is thousands, comma is decimal
    assert _locale_to_decimal("1.369,5165") == Decimal("1369.5165")
    # en-US style: 1,369.5165 — comma is thousands, dot is decimal
    assert _locale_to_decimal("1,369.5165") == Decimal("1369.5165")


def test_locale_parser_zero_and_empty():
    assert _locale_to_decimal("") == Decimal("0")
    assert _locale_to_decimal("0") == Decimal("0")
    assert _locale_to_decimal(None) == Decimal("0")
    assert _locale_to_decimal("garbage") == Decimal("0")


# Zero-cost emission policy (AI agent migration signal) ----------------------


def test_active_service_emits_per_period_zero(aws_ce_with_zeros_tsv, user_id, job_id):
    """RDS has cost=0 on 2026-04-01 and cost>0 on 2026-04-02 → both days
    must be emitted; the zero day signals 'service zeroed in this period'."""
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_with_zeros_tsv)

    rds = df[df["service"] == "RDS"].sort_values("usage_start")
    assert len(rds) == 2, "active service must emit row for every date, including zeros"
    assert rds.iloc[0]["cost"] == Decimal("0")
    assert rds.iloc[1]["cost"] == Decimal("25.1234")
    # Per-period zero is a daily observation, NOT flagged as global_zero
    assert all(r.get("scope") != "global_zero" for r in rds["raw_payload"])


def test_global_zero_service_emits_single_summary_row(aws_ce_with_zeros_tsv, user_id, job_id):
    """CloudWatch has cost=0 every day → emit exactly ONE row per billing_period,
    not per day."""
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_with_zeros_tsv)

    cw = df[df["service"] == "CloudWatch"]
    assert len(cw) == 1, "global-zero service must collapse to one summary row per month"
    row = cw.iloc[0]
    assert row["cost"] == Decimal("0")
    assert row["billing_period"] == "2026-04"


def test_global_zero_marker_present_in_raw_payload(aws_ce_with_zeros_tsv, user_id, job_id):
    """The AI agent distinguishes summary rows by `raw_payload.scope`."""
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_with_zeros_tsv)

    cw = df[df["service"] == "CloudWatch"].iloc[0]
    assert cw["raw_payload"]["scope"] == "global_zero"
    assert "date_range" in cw["raw_payload"]
    assert cw["raw_payload"]["date_range"] == "2026-04-01..2026-04-02"


def test_global_zero_summary_spans_full_export_range(aws_ce_with_zeros_tsv, user_id, job_id):
    """usage_start/usage_end of the summary row cover the export window."""
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_with_zeros_tsv)

    cw = df[df["service"] == "CloudWatch"].iloc[0]
    assert cw["usage_start"].date().isoformat() == "2026-04-01"
    assert cw["usage_end"].date().isoformat() == "2026-04-02"


def test_record_count_matches_active_plus_global_zero(aws_ce_with_zeros_tsv, user_id, job_id):
    """3 active services x 2 days + 1 global-zero service x 1 summary = 7 rows."""
    parser = AWSCostExplorerParser(user_id=user_id, job_id=job_id)
    df = parser.parse(aws_ce_with_zeros_tsv)
    assert len(df) == 7
