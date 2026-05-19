"""Format-agnostic tabular file readers.

Each provider parser knows its own column schema; this module knows only
how to turn `Path` → `pd.DataFrame[str]` regardless of compression/encoding.

Rule: every value lands as a string in the DataFrame. Decimal conversion is
the parser's job — letting Pandas infer dtypes would round monetary values.
"""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pandas as pd

XLSX_EXTS: frozenset[str] = frozenset({".xlsx", ".xlsm"})
CSV_EXTS: frozenset[str] = frozenset({".csv", ".tsv", ".txt"})


def is_xlsx(file_path: Path) -> bool:
    return file_path.suffix.lower() in XLSX_EXTS


def read_tabular(file_path: Path, *, encoding: str = "utf-8") -> pd.DataFrame:
    """Read any supported tabular format with `dtype=str` and NaN→"" normalization.

    Supports: .csv, .csv.gz, .csv.zip, .xlsx, .xlsm.
    """
    suffix = file_path.suffix.lower()

    if suffix in XLSX_EXTS:
        # First sheet only; dtype=str keeps cost columns precise.
        return pd.read_excel(file_path, dtype=str, sheet_name=0).fillna("")

    if suffix == ".gz":
        with gzip.open(file_path, "rt", encoding=encoding) as f:
            return pd.read_csv(f, dtype=str, low_memory=False).fillna("")

    if suffix == ".zip":
        with zipfile.ZipFile(file_path) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                return pd.DataFrame()
            with z.open(csvs[0]) as fh:
                return pd.read_csv(fh, dtype=str, low_memory=False).fillna("")

    return pd.read_csv(file_path, dtype=str, low_memory=False, encoding=encoding).fillna("")


def read_xlsx_headers_as_csv_bytes(file_path: Path) -> bytes:
    """Synthesize a CSV-style header line from an XLSX so existing
    `parser.can_parse(content_sample)` checks keep working without
    every parser learning a separate xlsx code path.
    """
    df = pd.read_excel(file_path, dtype=str, sheet_name=0, nrows=1).fillna("")
    header_line = ",".join(df.columns.astype(str).tolist())
    return header_line.encode("utf-8")
