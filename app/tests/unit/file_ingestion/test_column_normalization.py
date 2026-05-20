"""Regression tests for header normalization in read_tabular.

Real-world XLSX files often arrive with junk in column names — most commonly
a UTF-8 BOM that survived a wrong-encoding round-trip. read_tabular must
clean these up so downstream parsers see canonical column names.
"""

from __future__ import annotations

import pandas as pd

from app.infrastructure.file_ingestion.loaders import normalize_columns, read_tabular


def test_normalize_strips_unicode_bom():
    df = pd.DataFrame({"﻿Service": [1, 2], "Cost($)": [10, 20]})
    out = normalize_columns(df)
    assert "Service" in out.columns
    assert "﻿Service" not in out.columns


def test_normalize_strips_misencoded_bom():
    """\\xEF\\xBB\\xBF as three latin-1 chars (ï»¿) — what you get when a UTF-8
    TSV is opened with the wrong codepage and re-saved as XLSX."""
    misencoded_bom = "ï»¿"
    df = pd.DataFrame({f'{misencoded_bom}"Service"': [1], "EC2($)": [5]})
    out = normalize_columns(df)
    assert "Service" in out.columns


def test_normalize_keeps_legit_accented_characters():
    """Stripping mis-encoded BOM must NOT touch real accented chars elsewhere
    in the name (Portuguese/Spanish service names)."""
    df = pd.DataFrame({"Categoria": [1], "Região($)": [10], "ï_in_middle": [2]})
    out = normalize_columns(df)
    assert "Região($)" in out.columns
    # `ï` is NOT at the start in a BOM-like sequence, so it stays.
    assert "ï_in_middle" in out.columns


def test_normalize_strips_wrapping_quotes_and_whitespace():
    df = pd.DataFrame({'  "Service"  ': [1], "  EC2($)  ": [2]})
    out = normalize_columns(df)
    assert "Service" in out.columns
    assert "EC2($)" in out.columns


def test_normalize_strips_zero_width_chars():
    zws = chr(0x200B)  # zero-width space
    zwnj = chr(0x200C)  # zero-width non-joiner
    df = pd.DataFrame({f"Service{zws}": [1], f"{zwnj}EC2($)": [2]})
    out = normalize_columns(df)
    assert "Service" in out.columns
    assert "EC2($)" in out.columns


def test_read_tabular_handles_misencoded_bom_in_csv(tmp_path):
    """End-to-end: file with mis-encoded BOM in header parses correctly."""
    p = tmp_path / "fake_export.csv"
    # Write as latin-1 bytes so the BOM bytes (EF BB BF) are stored as three
    # separate bytes rather than the single UTF-8 BOM codepoint.
    p.write_bytes(b'\xef\xbb\xbf"Service",EC2($)\nService total,10\n2026-01-01,5\n')
    df = read_tabular(p)
    assert "Service" in df.columns
    assert "EC2($)" in df.columns
