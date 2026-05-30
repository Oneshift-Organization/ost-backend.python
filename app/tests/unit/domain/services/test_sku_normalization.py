from __future__ import annotations

import pytest

from app.domain.services.sku_normalization import match_key, normalize_provider


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GCP", "gcp"),
        ("gcp", "gcp"),
        ("  AWS  ", "aws"),
        ("Oci", "oci"),
        ("Azure", "azure"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_provider(raw: str | None, expected: str) -> None:
    assert normalize_provider(raw) == expected


def test_normalize_provider_is_idempotent() -> None:
    once = normalize_provider("GCP")
    assert normalize_provider(once) == once


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("E2 Instance Core running in Americas", "e2 instance core running in americas"),
        ("  E2   Instance\tCore  ", "e2 instance core"),
        ("RAM running in EMEA", "ram running in emea"),
        ("Already normalized", "already normalized"),
        (None, ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_match_key(raw: str | None, expected: str) -> None:
    assert match_key(raw) == expected


def test_match_key_collapses_internal_whitespace() -> None:
    assert match_key("a\n\n  b\t c") == "a b c"


def test_match_key_is_idempotent() -> None:
    once = match_key("  E2   Instance Core  ")
    assert match_key(once) == once
