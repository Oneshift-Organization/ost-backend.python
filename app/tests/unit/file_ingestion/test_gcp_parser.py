from decimal import Decimal

from app.infrastructure.file_ingestion.parsers import GCPParser


def test_gcp_parser_detects(gcp_billing_json, user_id, job_id):
    parser = GCPParser(user_id=user_id, job_id=job_id)
    sample = gcp_billing_json.read_bytes()[:2048]
    assert parser.can_parse(gcp_billing_json, sample) is True


def test_gcp_parser_reads_ndjson(gcp_billing_json, user_id, job_id):
    parser = GCPParser(user_id=user_id, job_id=job_id)
    df = parser.parse(gcp_billing_json)
    assert len(df) == 3


def test_gcp_cost_is_decimal_not_float(gcp_billing_json, user_id, job_id):
    """The whole point of the GCP parser: NEVER let cost be float."""
    parser = GCPParser(user_id=user_id, job_id=job_id)
    df = parser.parse(gcp_billing_json)
    for c in df["cost"]:
        assert isinstance(c, Decimal)
    # Compute Engine: cost 15.234567 com crédito DISCOUNT de -1.5. RN1 do
    # PLANO_CUR_BIGQUERY: desconto estrutural entra no custo efetivo; o valor
    # antes dos créditos fica preservado nos atributos.
    assert df.iloc[0]["cost"] == Decimal("13.734567")
    assert df.iloc[0]["attributes"]["cost_before_credits"] == "15.234567"


def test_gcp_credits_applied_to_cost_discounted(gcp_billing_json, user_id, job_id):
    parser = GCPParser(user_id=user_id, job_id=job_id)
    df = parser.parse(gcp_billing_json)
    # First row has credit -1.5 → cost_discounted = 15.234567 - 1.5 = 13.734567
    assert df.iloc[0]["cost_discounted"] == Decimal("13.734567")


def test_gcp_labels_become_tags(gcp_billing_json, user_id, job_id):
    parser = GCPParser(user_id=user_id, job_id=job_id)
    df = parser.parse(gcp_billing_json)
    assert df.iloc[0]["tags"] == {"env": "prod"}


def test_gcp_billing_period_from_invoice_month(gcp_billing_json, user_id, job_id):
    parser = GCPParser(user_id=user_id, job_id=job_id)
    df = parser.parse(gcp_billing_json)
    assert (df["billing_period"] == "2026-05").all()
