from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.entities.cloud_sku import CloudSku
from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.sku_matcher import SkuMatcher, extract_family


def _sku(sku_id: str, **kw: object) -> CloudSku:
    defaults: dict[str, object] = {
        "provider": "gcp",
        "service_name": "Compute Engine",
        "currency": "USD",
        "service_type": "virtual_server",
        "category": "compute",
        "component_type": "unclassified",
        "vcpus": Decimal("2"),
        "ram_gb": Decimal("8"),
    }
    defaults.update(kw)
    return CloudSku(sku_id=sku_id, **defaults)  # type: ignore[arg-type]


def _rule(pattern: str, component_type: str, **kw: object) -> SkuClassificationRule:
    defaults: dict[str, object] = {"provider": "gcp", "kind": "glob", "priority": 100}
    defaults.update(kw)
    return SkuClassificationRule(
        pattern=pattern, component_type=component_type, **defaults  # type: ignore[arg-type]
    )


CATALOG = [
    _sku("e2-standard-2", service_name="Compute Engine"),
    _sku("db-custom-2-3840", service_name="Cloud SQL", service_type="relational_database",
         category="database", vcpus=Decimal("2"), ram_gb=Decimal("3.75")),
]

RULES = [
    _rule("*cloud sql*", "database", priority=20, confidence="high"),
    _rule("*egress*", "network_egress", priority=30, confidence="high"),
    _rule("*instance core running in*", "core", priority=70, confidence="high"),
    _rule("*instance ram running in*", "ram", priority=71, confidence="high"),
]


def _matcher() -> SkuMatcher:
    return SkuMatcher(CATALOG, RULES, fuzzy_threshold=88)


def test_l0_exact_sku_id() -> None:
    m = _matcher().match("e2-standard-2")
    assert (m.match_layer, m.mapping_confidence) == ("L0", "high")
    assert m.sku_id == "e2-standard-2"
    assert m.component_type == "core"  # derived from virtual_server
    assert m.vcpus == Decimal("2")


def test_l1_normalized_match_key() -> None:
    m = _matcher().match("  E2-STANDARD-2  ")
    assert m.match_layer == "L1"
    assert m.mapping_confidence == "high"
    assert m.sku_id == "e2-standard-2"


def test_l2_rule_with_family_extraction() -> None:
    m = _matcher().match("E2 Instance Core running in Americas")
    assert m.match_layer == "L2"
    assert m.component_type == "core"
    assert m.mapping_confidence == "high"
    assert m.family == "E2"


def test_l2_database_rule() -> None:
    m = _matcher().match("Cloud SQL for PostgreSQL: Zonal - vCPU")
    assert m.match_layer == "L2"
    assert m.component_type == "database"


def test_l3_family_plus_group_fallback() -> None:
    # no rule matches this phrasing, but family + 'ram' token is present
    matcher = SkuMatcher(CATALOG, [], fuzzy_threshold=88)
    m = matcher.match("N2 custom memory ram allocation")
    assert m.match_layer == "L3"
    assert m.component_type == "ram"
    assert m.family == "N2"


def test_l4_fuzzy_match() -> None:
    matcher = SkuMatcher(CATALOG, [], fuzzy_threshold=80)
    m = matcher.match("Cloud SQL")  # close to catalog service_name "Cloud SQL"
    assert m.match_layer == "L4"
    assert m.mapping_confidence == "low"
    assert m.component_type == "database"


def test_l5_unmapped() -> None:
    matcher = SkuMatcher(CATALOG, [], fuzzy_threshold=95)
    m = matcher.match("totally unrelated support charge xyz")
    assert m.match_layer == "L5"
    assert m.component_type == "unclassified"
    assert m.mapping_confidence == "missing"


def test_empty_descriptor_is_unmapped() -> None:
    assert _matcher().match("").match_layer == "L5"
    assert _matcher().match(None).match_layer == "L5"


def test_priority_order_first_wins() -> None:
    rules = [
        _rule("*core*", "ram", priority=200),  # generic, later
        _rule("*instance core*", "core", priority=10),  # specific, first
    ]
    m = SkuMatcher(CATALOG, rules).match("E2 Instance Core running in Americas")
    assert m.component_type == "core"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("E2 Instance Core running in Americas", "E2"),
        ("n2d custom", "N2D"),
        ("N1 Predefined", "N1"),
        ("no family here", None),
    ],
)
def test_extract_family(text: str, expected: str | None) -> None:
    assert extract_family(text) == expected
