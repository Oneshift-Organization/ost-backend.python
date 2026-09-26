import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pandas as pd
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "billing"
_RAIZ = Path(__file__).resolve().parents[2]
FIXTURES_INTEGRACAO = _RAIZ / "app/tests/integration/fixtures"


def carregar_gerador_de_mocks() -> ModuleType:
    """`scripts/gerar_mocks_billing.py` como módulo (scripts/ não é pacote)."""
    nome = "gerar_mocks_billing"
    if nome in sys.modules:
        return sys.modules[nome]
    spec = importlib.util.spec_from_file_location(nome, _RAIZ / "scripts/gerar_mocks_billing.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    # `@dataclass` procura o módulo em sys.modules durante a execução.
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


@dataclass(frozen=True)
class MocksBilling:
    cur: Path
    bigquery: Path
    bigquery_csv: Path
    azure: Path
    oci: Path
    # Totais esperados dos mocks sintéticos (Azure/OCI), devolvidos pelo gerador.
    resumo_azure: object = None
    resumo_oci: object = None


def gerar_mocks(destino: Path) -> MocksBilling:
    """Gera os mocks de export detalhado em ``destino``.

    Não são versionados: passam de 500 KB (limite do pre-commit do projeto) e
    o gerador é determinístico — gerar é tão reproduzível quanto guardar.
    """
    gerador = carregar_gerador_de_mocks()
    cur = destino / "mock_aws_cur2_2026-03.csv.gz"
    bigquery = destino / "mock_gcp_bigquery_2025-12.json.gz"
    bigquery_csv = destino / "mock_gcp_bigquery_achatado_2025-12.csv"
    azure = destino / "mock_azure_cost_details_2026-03.csv"
    oci = destino / "mock_oci_cost_report_2026-03.csv.gz"
    gerador.gerar_cur_aws(FIXTURES_INTEGRACAO / "aws_cost_explorer_full.csv", cur)
    gerador.gerar_bigquery_gcp(
        FIXTURES_INTEGRACAO / "gcp_cost_table_full.csv", bigquery, bigquery_csv
    )
    return MocksBilling(
        cur=cur,
        bigquery=bigquery,
        bigquery_csv=bigquery_csv,
        azure=azure,
        oci=oci,
        resumo_azure=gerador.gerar_azure(azure),
        resumo_oci=gerador.gerar_oci(oci),
    )


@pytest.fixture(scope="session")
def mocks_billing(tmp_path_factory) -> MocksBilling:
    """Mocks gerados uma vez por sessão de testes (~7 s)."""
    return gerar_mocks(tmp_path_factory.mktemp("mocks_billing"))


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
def aws_ce_tsv() -> Path:
    """AWS Cost Explorer 'Daily costs by service' TSV (BOM + comma-decimal pt-BR)."""
    return FIXTURES_DIR / "aws_ce_daily_by_service.tsv"


@pytest.fixture
def aws_ce_with_zeros_tsv() -> Path:
    """Cost Explorer TSV exercising the zero-cost policy:

    - RDS: cost=0 on 2026-04-01, cost>0 on 2026-04-02 → 'active' service,
      per-period zero must be emitted.
    - CloudWatch: cost=0 on every date → 'global zero' service, emitted as a
      single summary row per billing_period with scope='global_zero'.
    """
    return FIXTURES_DIR / "aws_ce_with_zeros.tsv"


@pytest.fixture
def aws_cur2_xlsx(tmp_path: Path, aws_cur2_csv: Path) -> Path:
    return _csv_to_xlsx(aws_cur2_csv, tmp_path / "aws_cur2_sample.xlsx")


@pytest.fixture
def azure_payg_xlsx(tmp_path: Path, azure_payg_csv: Path) -> Path:
    return _csv_to_xlsx(azure_payg_csv, tmp_path / "azure_payg_sample.xlsx")


@pytest.fixture
def oci_cost_xlsx(tmp_path: Path, oci_cost_csv: Path) -> Path:
    return _csv_to_xlsx(oci_cost_csv, tmp_path / "oci_cost_sample.xlsx")
