from __future__ import annotations

import gzip
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import pandas as pd

from app.core.exceptions import ProviderDetectionError, UnsupportedFormatError
from app.core.logging import get_logger
from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import (
    XLSX_EXTS,
    is_parquet,
    ler_colunas,
    read_xlsx_headers_as_csv_bytes,
)
from app.infrastructure.file_ingestion.parsers import (
    AWSCostExplorerParser,
    AwsDetailedBillingParser,
    AWSParser,
    AzureParser,
    GcpCostTableParser,
    GCPParser,
    OCIParser,
)

_log = get_logger(__name__)

# Order matters when signatures overlap. AWS CUR is checked before Cost
# Explorer because CUR's signature (`line_item_unblended_cost`) is more
# specific. Cost Explorer's signature is generic (any "Service" + "Total
# costs($)" header).  AwsDetailedBilling and GcpCostTable are checked last
# — their signatures are unique and won't conflict.
PARSERS: list[type[BaseParser]] = [
    AWSParser,
    AWSCostExplorerParser,
    AzureParser,
    GCPParser,
    GcpCostTableParser,
    OCIParser,
    AwsDetailedBillingParser,
]


def detect_and_parse(
    file_path: Path, user_id: UUID, job_id: UUID
) -> tuple[BaseParser, pd.DataFrame]:
    """Detect the right parser then parse the file. Returns (parser, normalized_df)."""
    parser = detect_parser(file_path, user_id, job_id)
    df = parser.parse(file_path)
    return parser, df


def detect_parser(file_path: Path, user_id: UUID, job_id: UUID) -> BaseParser:
    """Pick the right parser by inspecting file content (never by extension).

    Raises UnsupportedFormatError if compression/extension is unknown.
    Raises ProviderDetectionError if no parser recognizes the content.
    """
    sample = _read_sample(file_path)

    for parser_cls in PARSERS:
        parser = parser_cls(user_id=user_id, job_id=job_id)
        try:
            if parser.can_parse(file_path, sample):
                return parser
        except Exception as e:
            _log.debug("parser_can_parse_failed", parser=parser_cls.__name__, err=str(e))
            continue

    raise ProviderDetectionError(
        "File format not recognized. Supported: AWS CUR 2.0, Azure Cost Export, "
        "GCP Billing Export, OCI Cost Report."
    )


def _read_sample(file_path: Path, size: int = 8192) -> bytes:
    """Read first bytes of file (transparently decompressing .gz / .zip / .xlsx).

    For .xlsx we synthesize a CSV-style header line so every parser's existing
    `can_parse(sample_bytes)` keeps working without learning a new code path.
    """
    if not file_path.exists():
        raise UnsupportedFormatError(f"file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if is_parquet(file_path):
        # Mesmo truque do XLSX: o cabeçalho vira uma linha CSV, e o `can_parse`
        # de cada parser continua funcionando sem saber de Parquet.
        return ",".join(ler_colunas(file_path)).encode("utf-8")
    if suffix in XLSX_EXTS:
        return read_xlsx_headers_as_csv_bytes(file_path)
    if suffix == ".gz":
        with gzip.open(file_path, "rb") as f:
            return f.read(size)
    if suffix == ".zip":
        with zipfile.ZipFile(file_path) as z:
            names = z.namelist()
            if not names:
                raise UnsupportedFormatError("empty zip archive")
            with z.open(names[0]) as f:
                return f.read(size)
    return file_path.read_bytes()[:size]


# Formato que cada parser grava em `schema_version`. O AWS e o GCP decidem na
# leitura (CUR 2.0 ou legado; BigQuery padrão ou detalhado); aqui a amostra decide.
_FORMATO_DO_PARSER = {
    "AWSCostExplorerParser": "AWS_CE_DAILY_BY_SERVICE",
    "AwsDetailedBillingParser": "AWS_DETAILED_BILLING",
    "GcpCostTableParser": "GCP_COST_TABLE",
    "AzureParser": "AZURE_PAYG",
    "OCIParser": "OCI_COST_REPORT",
}


# Arquivos que parecem fatura e não são: reconhecidos para dizer o que enviar no lugar
_NAO_E_FATURA = (
    # Relatório de uso da OCI: mesmas colunas `lineItem/` do de custos, sem nenhuma `cost/`
    ("OCI_USAGE_REPORT", (b"lineItem/referenceNo", b"usage/consumedQuantity"), b"cost/"),
)


def formato_que_nao_e_fatura(file_path: Path) -> str | None:
    """O formato conhecido que o detector recusa com razão, ou None."""
    try:
        amostra = _read_sample(file_path)
    except UnsupportedFormatError:
        return None
    for formato, exige, proibe in _NAO_E_FATURA:
        if all(t in amostra for t in exige) and proibe not in amostra:
            return formato
    return None


def inspecionar_formato(file_path: Path) -> tuple[str, bool | None]:
    """(formato, traz ID de recurso?) pela amostra do arquivo, sem ler tudo.

    ``None`` no ID quando a amostra não basta para dizer. Levanta as mesmas
    exceções de ``detect_parser`` quando o formato não é reconhecido.
    """
    parser = detect_parser(file_path, uuid4(), uuid4())
    amostra = _read_sample(file_path)
    nome = type(parser).__name__
    if nome == "AWSParser":
        formato = "AWS_CUR2.0" if b"line_item_" in amostra else "AWS_CUR_LEGACY"
        return formato, b"line_item_resource_id" in amostra or b"lineItem/ResourceId" in amostra
    if nome == "GCPParser":
        detalhado = b'"resource"' in amostra or b"resource.name" in amostra
        return ("GCP_BILLING_DETAILED" if detalhado else "GCP_BILLING_STD"), detalhado or None
    return _FORMATO_DO_PARSER.get(nome, nome), None
