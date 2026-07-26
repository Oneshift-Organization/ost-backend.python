"""SkuMatcher — the deterministic Matching Ladder (L0-L5).

Given a billing descriptor, returns the structured SKU id (when known), the
``component_type`` role, and the rung that matched with its confidence. The
matcher is **pure**: it receives the catalog + rules already loaded, so it is
fully unit-testable without a database. Wiring (loading from repositories) is
the caller's job.

See ``docs/refactor/sku-accuracy-spec.md`` §4 (the ladder) and §5 (behavior).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz import fuzz

from app.domain.entities.cloud_sku import CloudSku
from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.sku_normalization import match_key

DEFAULT_FUZZY_THRESHOLD = 88

# Family extraction is data/shape-driven, not a per-cloud name list. Two rungs:
#
#  1. Known GCP tokens (high confidence) — these lack the ``family.size`` shape
#     (``e2-standard-4`` uses ``-`` and the size word carries no dot), so a
#     structural rule alone would miss them. Longest first so "n2d" beats "n2".
#  2. Structural fallback — matches the *shape* of an instance-type family token
#     ``<letras><dígito><sufixo>`` seguido de um separador de tamanho (``.``/``-``).
#     Cobre m7g, c7g, r8g, is4gen, c4, db.r8g… de qualquer cloud sem enumerá-los.
#     É isto que evita que a AWS inteira colapse num pool "general".
_FAMILY_TOKENS = (
    "n2d", "n4d", "c2d", "t2d", "t2a",
    "e2", "n1", "n2", "n4", "c2", "c3",
    "m1", "m2", "m3", "g2", "z3", "h3",
)
_KNOWN_FAMILY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_FAMILY_TOKENS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# Shape: 1-4 letras + 1 dígito + até 3 letras, seguido de '.' ou '-' (o começo
# do sufixo de tamanho — .2xlarge, -standard-4). O separador obrigatório é o que
# distingue "m7g.2xlarge" (família) de um token solto sem tamanho.
_FAMILY_SHAPE_PATTERN = re.compile(r"\b([a-z]{1,4}\d[a-z]{0,3})[.\-]", re.IGNORECASE)

# component_type implied by a catalog service_type on an L0/L1 hit.
_SERVICE_TYPE_ROLE = {
    "virtual_server": "core",
    "compute": "core",
    "relational_database": "database",
    "managed_database": "database",
}

# resource-group tokens for the L3 fallback (family + group), matched against
# the descriptor and the usage unit (e.g. "gibibyte hour" -> ram).
_GROUP_TOKENS = (
    ("core", "core"),
    ("cpu", "core"),
    ("ram", "ram"),
    ("memory", "ram"),
    ("gibibyte", "ram"),
)


@dataclass(frozen=True)
class SkuMatch:
    component_type: str
    match_layer: str  # L0..L5
    mapping_confidence: str  # high | medium | low | missing
    sku_id: str | None = None
    service_type: str | None = None
    category: str | None = None
    family: str | None = None
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None


def _unmatched() -> SkuMatch:
    return SkuMatch(
        component_type="unclassified",
        match_layer="L5",
        mapping_confidence="missing",
    )


def extract_family(text: str | None) -> str | None:
    """Return the uppercase machine family token found in ``text``, or None.

    Two rungs: known GCP tokens first (high confidence, no structural shape),
    then a structural ``<letras><dígito><sufixo>.`` shape that generalises to
    any provider's instance-type naming without enumerating families.
    """
    if not text:
        return None
    m = _KNOWN_FAMILY_PATTERN.search(text)
    if m:
        return m.group(1).upper()
    m = _FAMILY_SHAPE_PATTERN.search(text)
    return m.group(1).upper() if m else None


class SkuMatcher:
    def __init__(
        self,
        catalog: list[CloudSku],
        rules: list[SkuClassificationRule],
        *,
        fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
    ) -> None:
        self._by_id: dict[str, CloudSku] = {e.sku_id: e for e in catalog}
        self._by_key: dict[str, CloudSku] = {match_key(e.sku_id): e for e in catalog}
        self._fuzzy_index: list[tuple[str, CloudSku]] = [
            (match_key(e.service_name or e.sku_id), e) for e in catalog
        ]
        self._rules: list[SkuClassificationRule] = sorted(rules, key=lambda r: r.priority)
        self._threshold = fuzzy_threshold

    def match(  # noqa: PLR0911 — the ladder is a cascade of early returns by design
        self,
        descriptor: str | None,
        *,
        usage_unit: str | None = None,
    ) -> SkuMatch:
        if not descriptor:
            return _unmatched()

        # L0 — exact (provider already filtered) structured sku_id
        entry = self._by_id.get(descriptor)
        if entry is not None:
            return self._from_catalog(entry, "L0", "high")

        key = match_key(descriptor)

        # L1 — exact on normalized match_key
        entry = self._by_key.get(key)
        if entry is not None:
            return self._from_catalog(entry, "L1", "high")

        # L2 — data-driven glob/regex rules (priority ASC, first wins)
        for rule in self._rules:
            if self._rule_matches(rule, key):
                return SkuMatch(
                    component_type=rule.component_type,
                    match_layer="L2",
                    mapping_confidence=rule.confidence,
                    family=rule.family or extract_family(key),
                )

        # L3 — machine-family + resource-group token fallback
        l3 = self._match_family_group(key, match_key(usage_unit))
        if l3 is not None:
            return l3

        # L4 — fuzzy match against catalog service names (low confidence)
        l4 = self._fuzzy_match(key)
        if l4 is not None:
            return l4

        # L5 — unmapped
        return _unmatched()

    # ── rungs ────────────────────────────────────────────────────────────
    def _from_catalog(self, entry: CloudSku, layer: str, confidence: str) -> SkuMatch:
        component_type = entry.component_type
        if not component_type or component_type == "unclassified":
            component_type = _SERVICE_TYPE_ROLE.get(entry.service_type or "", "unclassified")
        return SkuMatch(
            component_type=component_type,
            match_layer=layer,
            mapping_confidence=confidence,
            sku_id=entry.sku_id,
            service_type=entry.service_type,
            category=entry.category,
            family=extract_family(entry.sku_id),
            vcpus=entry.vcpus,
            ram_gb=entry.ram_gb,
        )

    @staticmethod
    def _rule_matches(rule: SkuClassificationRule, key: str) -> bool:
        pattern = rule.pattern.casefold()
        if rule.kind == "regex":
            return re.search(pattern, key) is not None
        return fnmatch.fnmatchcase(key, pattern)

    def _match_family_group(self, key: str, unit_key: str) -> SkuMatch | None:
        family = extract_family(key)
        if family is None:
            return None
        for token, component_type in _GROUP_TOKENS:
            if token in key or token in unit_key:
                return SkuMatch(
                    component_type=component_type,
                    match_layer="L3",
                    mapping_confidence="medium",
                    family=family,
                )
        return None

    def _fuzzy_match(self, key: str) -> SkuMatch | None:
        best_entry: CloudSku | None = None
        best_score = 0.0
        for candidate_key, entry in self._fuzzy_index:
            if not candidate_key:
                continue
            score = fuzz.token_set_ratio(key, candidate_key)
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry is None or best_score < self._threshold:
            return None
        return self._from_catalog(best_entry, "L4", "low")
