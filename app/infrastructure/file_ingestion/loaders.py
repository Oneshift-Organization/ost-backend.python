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

import csv
import gzip
import json
import math
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

XLSX_EXTS: frozenset[str] = frozenset({".xlsx", ".xlsm"})
CSV_EXTS: frozenset[str] = frozenset({".csv", ".tsv", ".txt"})

_PARQUET_MAGIC = b"PAR1"
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

    Supports: .csv, .tsv, .txt, .csv.gz, .csv.zip, real .xlsx/.xlsm, .parquet.
    `.xlsx` extension with non-ZIP content is treated as text (sniffs delimiter).
    """
    suffix = file_path.suffix.lower()

    if is_parquet(file_path):
        return normalize_columns(_parquet_para_texto(pq.read_table(file_path).to_pandas()))

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
    encodings_to_try = (
        ("utf-8-sig", encoding, "latin-1")
        if encoding != "utf-8-sig"
        else (
            "utf-8-sig",
            "latin-1",
        )
    )
    last_error: Exception | None = None
    for enc in encodings_to_try:
        try:
            if hasattr(source, "read"):
                # File-like; rewind if possible
                if hasattr(source, "seek"):
                    source.seek(0)
                return pd.read_csv(
                    source, dtype=str, sep=None, engine="python", encoding=enc
                ).fillna("")
            return pd.read_csv(source, dtype=str, sep=None, engine="python", encoding=enc).fillna(
                ""
            )
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    return pd.DataFrame()


def is_parquet(file_path: Path) -> bool:
    """Parquet pelo conteúdo (magic `PAR1`), não só pela extensão."""
    try:
        with open(file_path, "rb") as f:
            return f.read(4) == _PARQUET_MAGIC
    except OSError:
        return False


def _parquet_para_texto(df: pd.DataFrame) -> pd.DataFrame:
    """Parquet traz tipos; o contrato do loader é texto.

    Número vira ``str(valor)`` — a representação mais curta que volta ao mesmo
    double, que é o que ``Decimal(str(x))`` do parser espera. Mapa (tags,
    `product`) vira JSON, o mesmo formato do CSV do CUR 2.0.
    """

    def _texto(valor: object) -> str:  # noqa: PLR0911 - um retorno por tipo
        if valor is None:
            return ""
        if isinstance(valor, float):
            return "" if math.isnan(valor) else repr(valor)
        if isinstance(valor, list):  # map<string,string> chega como lista de pares
            try:
                return json.dumps(dict(valor), ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                return json.dumps(valor, ensure_ascii=False, default=str)
        if isinstance(valor, dict):
            return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(valor, (datetime, pd.Timestamp)):
            return pd.Timestamp(valor).isoformat()
        return str(valor)

    return pd.DataFrame({c: [_texto(v) for v in df[c].tolist()] for c in df.columns})


def ler_colunas(file_path: Path) -> list[str]:
    """Só o cabeçalho, já normalizado. Para decidir o que ler antes de ler."""
    suffix = file_path.suffix.lower()
    if is_parquet(file_path):
        return [c.strip() for c in pq.ParquetFile(file_path).schema_arrow.names]
    if suffix in XLSX_EXTS and is_real_zip_file(file_path):
        return list(normalize_columns(pd.read_excel(file_path, dtype=str, nrows=0)).columns)
    primeira = _primeira_linha(file_path)
    sep = _sniff_sep(primeira)
    nomes = next(csv.reader([primeira], delimiter=sep), [])
    return list(normalize_columns(pd.DataFrame(columns=nomes)).columns)


def iter_tabular(
    file_path: Path,
    *,
    colunas: set[str] | None = None,
    tamanho: int = 200_000,
) -> Iterator[pd.DataFrame]:
    """Lê em blocos, só as colunas pedidas, tudo como texto.

    Um CUR tem ~125 colunas e o parser usa ~40; um CUR horário tem milhões de
    linhas. Ler tudo de uma vez (como ``read_tabular``) não cabe na memória de
    um pod. Colunas pedidas que não existem no arquivo são ignoradas — quem
    decide se uma coluna ausente é degradação ou erro é o parser.
    """
    if is_parquet(file_path):
        yield from _iter_parquet(file_path, colunas, tamanho)
        return
    suffix = file_path.suffix.lower()
    if suffix in XLSX_EXTS and is_real_zip_file(file_path):
        df = read_tabular(file_path)
        yield df[[c for c in df.columns if colunas is None or c in colunas]]
        return

    primeira = _primeira_linha(file_path)
    sep = _sniff_sep(primeira)

    def _usar(nome: str) -> bool:
        return colunas is None or _limpar_nome(nome) in colunas

    fonte: Any
    if suffix == ".zip" and is_real_zip_file(file_path):
        with zipfile.ZipFile(file_path) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                return
            with z.open(csvs[0]) as fonte:
                yield from _blocos_csv(fonte, sep, _usar, tamanho)
        return
    fonte = file_path
    yield from _blocos_csv(fonte, sep, _usar, tamanho)


def _blocos_csv(
    fonte: Any, sep: str, usar: Callable[[str], bool], tamanho: int
) -> Iterator[pd.DataFrame]:
    leitor = pd.read_csv(
        fonte,
        dtype=str,
        sep=sep,
        usecols=usar,
        keep_default_na=False,
        chunksize=tamanho,
        encoding="utf-8-sig",
        encoding_errors="replace",
        compression="infer",
    )
    for bloco in leitor:
        yield normalize_columns(bloco)


def _iter_parquet(
    file_path: Path, colunas: set[str] | None, tamanho: int
) -> Iterator[pd.DataFrame]:
    arquivo = pq.ParquetFile(file_path)
    nomes = arquivo.schema_arrow.names
    escolhidas = [n for n in nomes if colunas is None or n.strip() in colunas]
    for lote in arquivo.iter_batches(batch_size=tamanho, columns=escolhidas):
        yield normalize_columns(_parquet_para_texto(lote.to_pandas()))


def _primeira_linha(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".gz":
        with gzip.open(file_path, "rt", encoding="utf-8-sig", errors="replace") as f:
            return f.readline()
    if suffix == ".zip" and is_real_zip_file(file_path):
        with zipfile.ZipFile(file_path) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                return ""
            with z.open(csvs[0]) as f:
                return f.readline().decode("utf-8-sig", errors="replace")
    with open(file_path, encoding="utf-8-sig", errors="replace") as f:
        return f.readline()


def _sniff_sep(linha: str) -> str:
    """Delimitador pela contagem no cabeçalho. `csv.Sniffer` erra com cabeçalho
    de 125 colunas cheio de `_` e `/`; contar é suficiente e previsível."""
    contagem = {sep: linha.count(sep) for sep in (",", ";", "\t")}
    return max(contagem, key=lambda s: contagem[s]) if any(contagem.values()) else ","


def _limpar_nome(nome: str) -> str:
    return str(next(iter(normalize_columns(pd.DataFrame(columns=[nome])).columns)))


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
