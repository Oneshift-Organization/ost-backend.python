from decimal import Decimal

from app.infrastructure.file_ingestion.parsers import AzureParser


def test_azure_parser_detects_payg(azure_payg_csv, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    sample = azure_payg_csv.read_bytes()[:2048]
    assert parser.can_parse(azure_payg_csv, sample) is True


def test_azure_parser_normalizes_three_rows(azure_payg_csv, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    df = parser.parse(azure_payg_csv)
    assert len(df) == 3


def test_azure_parser_decimal_cost(azure_payg_csv, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    df = parser.parse(azure_payg_csv)
    assert all(isinstance(c, Decimal) for c in df["cost"])
    assert df.iloc[0]["cost"] == Decimal("18.240000")


def test_azure_parser_provider_label(azure_payg_csv, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    df = parser.parse(azure_payg_csv)
    assert (df["provider"] == "azure").all()


def test_azure_parser_currency_3char(azure_payg_csv, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    df = parser.parse(azure_payg_csv)
    assert all(df["currency"].str.len() == 3)


def test_azure_parser_schema_version_payg(azure_payg_csv, user_id, job_id):
    parser = AzureParser(user_id=user_id, job_id=job_id)
    parser.parse(azure_payg_csv)
    assert parser.get_schema_version() == "AZURE_PAYG"
