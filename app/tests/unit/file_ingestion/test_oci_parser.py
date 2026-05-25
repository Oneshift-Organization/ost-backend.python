from decimal import Decimal

from app.infrastructure.file_ingestion.parsers import OCIParser


def test_oci_parser_detects(oci_cost_csv, user_id, job_id):
    parser = OCIParser(user_id=user_id, job_id=job_id)
    sample = oci_cost_csv.read_bytes()[:2048]
    assert parser.can_parse(oci_cost_csv, sample) is True


def test_oci_correction_replaces_original_row(oci_cost_csv, user_id, job_id):
    """ref-004 is a correction of ref-003. After parsing, ref-003 must be GONE."""
    parser = OCIParser(user_id=user_id, job_id=job_id)
    df = parser.parse(oci_cost_csv)

    refs = [r.get("lineItem/referenceNo") for r in df["raw_payload"]]
    assert "ref-003" not in refs  # original was replaced
    assert "ref-004" in refs  # correction kept


def test_oci_parser_cost_is_decimal(oci_cost_csv, user_id, job_id):
    parser = OCIParser(user_id=user_id, job_id=job_id)
    df = parser.parse(oci_cost_csv)
    assert all(isinstance(c, Decimal) for c in df["cost"])


def test_oci_dynamic_tags_columns_become_tags_dict(oci_cost_csv, user_id, job_id):
    parser = OCIParser(user_id=user_id, job_id=job_id)
    df = parser.parse(oci_cost_csv)
    row = df.iloc[0]
    assert row["tags"]["env"] == "prod"
    assert row["tags"]["team"] == "platform"


def test_oci_correction_row_flagged(oci_cost_csv, user_id, job_id):
    parser = OCIParser(user_id=user_id, job_id=job_id)
    df = parser.parse(oci_cost_csv)
    correction_rows = df[df["is_correction"]]
    assert len(correction_rows) == 1
    assert correction_rows.iloc[0]["raw_payload"]["lineItem/referenceNo"] == "ref-004"


def test_oci_schema_version(oci_cost_csv, user_id, job_id):
    parser = OCIParser(user_id=user_id, job_id=job_id)
    parser.parse(oci_cost_csv)
    assert parser.get_schema_version() == "OCI_COST_REPORT"
