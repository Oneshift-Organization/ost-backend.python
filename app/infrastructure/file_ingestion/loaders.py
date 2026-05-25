"""Format-agnostic tabular file readers.

Each provider parser knows its own column schema; this module knows only
how to turn `Path` → `pd.DataFrame[str]` regardless of compression/encoding.

Rule: every value lands as a string in the DataFrame. Decimal conversion is
the parser's job — letting Pandas infer dtypes would round monetary values.

Robustness: files masquerading with the wrong extension (e.g. a TSV renamed
to `.xlsx` — common when users "Save As" from Excel) are detected via magic
bytes and routed to the right reader. Delimiter (`,` vs `\\t`) is sniffed by
Pandas' python engine.
"""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pandas as pd

XLSX_EXTS: frozenset[str] = frozenset({".xlsx", ".xlsm"})
CSV_EXTS: frozenset[str] = frozenset({".csv", ".tsv", ".txt"})

_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_EMPTY_MAGIC = b"PK\x05\x06"

# Characters often leaked into column names by upstream tooling.
# - U+FEFF: byte-order mark, common when a BOM-prefixed CSV gets saved-as XLSX
# - U+200B/U+200C/U+200D: zero-width chars added by some export tools
_INVISIBLE_CHARS = chr(0xFEFF) + chr(0x200B) + chr(0x200C) + chr(0x200D)

# Misencoded UTF-8 BOM: when a BOM-prefixed file is read as latin-1/cp1252
# and re-saved (e.g. opening a UTF-8 TSV in Excel with the wrong encoding),
# the three BOM bytes EF BB BF survive as three separate latin-1 chars
# (U+00EF U+00BB U+00BF, "ï»¿"). Only stripped from the START of a name —
# stripping these chars globally would break Portuguese/Spanish service
# names that legitimately use them.
_MISENCODED_BOM_PREFIX = chr(0x00EF) + chr(0x00BB) + chr(0x00BF)


def is_xlsx(file_path: Path) -> bool:
    return file_path.suffix.lower() in XLSX_EXTS


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip BOM / zero-width chars / wrapping quotes / outer whitespace from
    every column name.

    Why: TSV-with-BOM saved as XLSX leaks the BOM INTO the first column's cell
    value (not just the encoding), so Pandas returns headers like
    `'\\ufeff"Service"'`. Downstream parsers' string comparisons would silently
    miss real matches. Fixing once at the loader saves every parser from
    having to defensively normalize.
    """
    cleaned = []
    for col in df.columns:
        name = str(col)
        # Strip a leading mis-encoded UTF-8 BOM (only at the start).
        if name.startswith(_MISENCODED_BOM_PREFIX):
            name = name[len(_MISENCODED_BOM_PREFIX) :]
        # Strip invisible characters anywhere in the name (defensive).
        for ch in _INVISIBLE_CHARS:
            name = name.replace(ch, "")
        # Strip outer whitespace then wrapping double-quotes then whitespace again.
        name = name.strip().strip('"').strip("'").strip()
        cleaned.append(name)
    df = df.copy()
    df.columns = cleaned
    return df


def is_real_zip_file(file_path: Path) -> bool:
    """True iff the file actually starts with the ZIP magic header.

    Used for both `.xlsx` (which IS a zip) and `.zip` archives. Catches
    text files renamed to `.xlsx` or `.zip` and prevents the unhelpful
    BadZipFile crash deep inside openpyxl.
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    return head.startswith(_ZIP_MAGIC) or head.startswith(_ZIP_EMPTY_MAGIC)


def read_tabular(file_path: Path, *, encoding: str = "utf-8") -> pd.DataFrame:
    """Read any supported tabular format with `dtype=str` and NaN→"" normalization.

    Supports: .csv, .tsv, .txt, .csv.gz, .csv.zip, real .xlsx/.xlsm.
    `.xlsx` extension with non-ZIP content is treated as text (sniffs delimiter).
    """
    suffix = file_path.suffix.lower()

    if suffix in XLSX_EXTS and is_real_zip_file(file_path):
        df = pd.read_excel(file_path, dtype=str, sheet_name=0).fillna("")
        return normalize_columns(df)

    if suffix == ".gz":
        with gzip.open(file_path, "rt", encoding=encoding) as f:
            return normalize_columns(_read_csv_sniffed(f, encoding=encoding))

    if suffix == ".zip" and is_real_zip_file(file_path):
        with zipfile.ZipFile(file_path) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                return pd.DataFrame()
            with z.open(csvs[0]) as fh:
                return normalize_columns(_read_csv_sniffed(fh, encoding=encoding))

    # Any other case (plain .csv/.tsv/.txt, mis-named .xlsx, etc.)
    return normalize_columns(_read_csv_sniffed(file_path, encoding=encoding))


def _read_csv_sniffed(source, *, encoding: str = "utf-8") -> pd.DataFrame:
    """Read CSV/TSV via Pandas' python engine with delimiter sniffing.

    `sep=None` + `engine="python"` uses `csv.Sniffer` to detect `,` vs `\\t` vs `;`.
    Strips UTF-8 BOM via `utf-8-sig`. Falls back to `latin-1` for legacy exports.
    """
    encodings_to_try = ("utf-8-sig", encoding, "latin-1") if encoding != "utf-8-sig" else (
        "utf-8-sig",
        "latin-1",
    )
    last_error: Exception | None = None
    for enc in encodings_to_try:
        try:
            if hasattr(source, "read"):
                # File-like; rewind if possible
                if hasattr(source, "seek"):
                    source.seek(0)
                return (
                    pd.read_csv(source, dtype=str, sep=None, engine="python", encoding=enc)
                    .fillna("")
                )
            return (
                pd.read_csv(source, dtype=str, sep=None, engine="python", encoding=enc)
                .fillna("")
            )
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    return pd.DataFrame()


def read_xlsx_headers_as_csv_bytes(file_path: Path) -> bytes:
    """Synthesize a CSV-style header line from an XLSX so existing
    `parser.can_parse(content_sample)` checks keep working without
    every parser learning a separate xlsx code path.

    If the file is not a real XLSX (wrong magic), return the raw bytes so
    detection falls through to the text-sample path.
    """
    if not is_real_zip_file(file_path):
        return file_path.read_bytes()[:8192]
    df = pd.read_excel(file_path, dtype=str, sheet_name=0, nrows=1).fillna("")
    header_line = ",".join(df.columns.astype(str).tolist())
    return header_line.encode("utf-8")
