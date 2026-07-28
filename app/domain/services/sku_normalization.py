"""Deterministic text normalization for SKU matching.

Pure domain helpers (zero framework imports). The single choke point for
provider casing and for the ``match_key`` used by the Matching Ladder (L1+),
so trivial textual variance never blocks a match.

See ``docs/refactor/sku-accuracy-spec.md`` §3.3 and §5.
"""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def normalize_provider(provider: str | None) -> str:
    """Canonical lowercase provider token (``'GCP'`` / ``'gcp'`` -> ``'gcp'``).

    The single normalization point for provider casing (spec §5, rule 4).
    Returns ``""`` for ``None``/blank so callers can branch deterministically.
    """
    if not provider:
        return ""
    return provider.strip().casefold()


def match_key(raw: str | None) -> str:
    """Normalized matching key: ``collapse_whitespace(trim(lowercase(casefold(raw))))``.

    Order is fixed and deterministic (spec §3.3): casefold, strip, then
    collapse internal whitespace runs to a single space. Applied to both the
    billing descriptor and the catalog key/description before comparison.
    """
    if not raw or not isinstance(raw, str):
        return ""
    folded = raw.casefold().strip()
    return _WHITESPACE.sub(" ", folded).strip()


def squash_key(raw: str | None) -> str:
    """Chave insensível a espaço e pontuação: ``AmazonRDS`` == ``Amazon RDS``.

    Os parsers de billing emitem nomes concatenados (``AmazonRDS``, ``AmazonS3``)
    enquanto o catálogo curado usa nomes com espaço (``Amazon RDS``). ``match_key``
    apenas colapsa espaços, então não faz essa ponte — e o casamento falhava,
    caindo num fallback por categoria que atribuía o ``service_type`` errado
    (object storage virava block storage).

    Mantém só alfanuméricos: é a forma mais forte de normalização textual antes
    de partir para heurísticas (substring/fuzzy), e não depende de nome de nuvem.
    """
    if not raw or not isinstance(raw, str):
        return ""
    return "".join(ch for ch in raw.casefold() if ch.isalnum())
