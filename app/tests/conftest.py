from pathlib import Path
from uuid import UUID

import pandas as pd
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "billing"


def _csv_to_xlsx(csv_path: Path, target: Path) -> Path:
    """Round-trip a CSV fixture as XLSX so we can validate xlsx ingestion
    without committing binary fixtures to the repo."""
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    df.to_excel(target, index=False, engine="openpyxl")
    return target


@pytest.fixture
def user_id() -> UUID:
    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def job_id() -> UUID:
    return UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def aws_cur2_csv() -> Path:
    return FIXTURES_DIR / "aws_cur2_sample.csv"


@pytest.fixture
def azure_payg_csv() -> Path:
    return FIXTURES_DIR / "azure_payg_sample.csv"


@pytest.fixture
def gcp_billing_json() -> Path:
    return FIXTURES_DIR / "gcp_billing_sample.json"


@pytest.fixture
def oci_cost_csv() -> Path:
    return FIXTURES_DIR / "oci_cost_sample.csv"


@pytest.fixture
def aws_cur2_xlsx(tmp_path: Path, aws_cur2_csv: Path) -> Path:
    return _csv_to_xlsx(aws_cur2_csv, tmp_path / "aws_cur2_sample.xlsx")


@pytest.fixture
def azure_payg_xlsx(tmp_path: Path, azure_payg_csv: Path) -> Path:
    return _csv_to_xlsx(azure_payg_csv, tmp_path / "azure_payg_sample.xlsx")


@pytest.fixture
def oci_cost_xlsx(tmp_path: Path, oci_cost_csv: Path) -> Path:
    return _csv_to_xlsx(oci_cost_csv, tmp_path / "oci_cost_sample.xlsx")
