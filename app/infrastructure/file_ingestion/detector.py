from __future__ import annotations

import gzip
import zipfile
from pathlib import Path
from uuid import UUID

import pandas as pd

from app.core.exceptions import ProviderDetectionError, UnsupportedFormatError
from app.core.logging import get_logger
from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import (
    XLSX_EXTS,
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
