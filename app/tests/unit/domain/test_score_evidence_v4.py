from copy import deepcopy

import pytest

from app.domain.services.decision_support import build_decision_support
from app.tests.unit.domain.test_decision_support import (
    decision_payload as decision_payload,  # noqa: PLC0414 -- pytest fixture re-export
)


def test_billing_only_keeps_observed_grade_but_discloses_missing_inventory(decision_payload):
    decision_payload.pop("topologia")
    result = build_decision_support(decision_payload)
    assert result.score.status == "partial"
    assert result.score.value == 94.7
    assert result.score.axes[1].value == 100
    assert result.score.axes[1].coverage == 0.9
    assert result.score.axes[1].factors[3].value is None
    assert result.score.assessment_method == "evidence_normalized"


def test_small_easy_subset_cannot_generate_overall_grade(decision_payload):
    known = decision_payload["decision_workloads"][0]
    known["cost"] = 100
    unknown = {"name": "Unknown", "category": "compute", "cost": 900, "targets": []}
    decision_payload["decision_workloads"].append(unknown)
    score = build_decision_support(decision_payload).score
    assert score.status == "partial"
    assert score.value == score.lower_bound
    assert score.assessment_method == "conservative_bound"
    assert score.missing_information
    assert score.axes[1].value == 100  # describes the subset, not the whole estate
    assert score.axes[1].coverage == 0.1


def test_excluded_unclassified_cost_cannot_hide_missing_scope(decision_payload):
    decision_payload["decision_workloads"].append(
        {"name": "Unknown", "category": "unclassified", "cost": 9000}
    )
    result = build_decision_support(decision_payload)
    assert result.score.value == result.score.lower_bound
    assert result.score.assessment_method == "conservative_bound"
    assert result.score.axes[2].factors[0].value == 10


def test_class_hypothesis_is_explicit_and_does_not_change_7r_evidence(decision_payload):
    w = decision_payload["decision_workloads"][0]
    w["source_metadata"] = {}
    w["migration"] = {"classe_migracao": "replatform"}
    w["targets"][0]["metadata"] = {}
    result = build_decision_support(decision_payload)
    factor = result.score.axes[1].factors[1]
    assert factor.value == 60
    assert factor.provisional
    assert factor.evidence[0]["requires_refactoring"] is None
    assert result.score.status == "partial"
    assert result.workload_strategies[0].requires_refactoring is None


def test_same_capability_alternatives_use_worst_evidence_not_list_order(decision_payload):
    w = decision_payload["decision_workloads"][0]
    other = deepcopy(w["targets"][0])
    other["service_name"] = "Alternative"
    other["metadata"]["vendor_lock_in_level"] = "high"
    w["targets"].append(other)
    a = build_decision_support(decision_payload)
    w["targets"].reverse()
    b = build_decision_support(decision_payload)
    assert a == b
    assert a.score.axes[1].factors[2].value == 0
    assert a.score.value == 87


def test_partial_inventory_counts_only_correlated_cost_and_changes_fingerprint(decision_payload):
    before = build_decision_support(decision_payload)
    d = decision_payload["topologia"]["detalhe"]["servicos"]["Storage"]
    d.update(nivel="high", cobertura=0.25)
    after = build_decision_support(decision_payload)
    factor = after.score.axes[1].factors[3]
    assert factor.value == 0
    assert factor.coverage == 0.25
    assert after.score.axes[1].factors[2].value == 100  # dependency is NOT lock-in
    assert before.input_fingerprint != after.input_fingerprint
    assert after.score.status == "partial"


def test_known_refactoring_constraint_wins_over_unknown_alternative(decision_payload):
    w = decision_payload["decision_workloads"][0]
    w["source_metadata"] = {}
    w["migration"] = {"classe_migracao": "rehost"}
    w["targets"][0]["metadata"]["requires_refactoring"] = True
    other = deepcopy(w["targets"][0])
    other["metadata"] = {}
    w["targets"].append(other)
    factor = build_decision_support(decision_payload).score.axes[1].factors[1]
    assert factor.value == 30
    assert not factor.provisional


@pytest.mark.parametrize("invalid", [None, True, -1, 2, "bad", "NaN"])
def test_invalid_inventory_coverage_is_not_evidence(decision_payload, invalid):
    decision_payload["topologia"]["detalhe"]["servicos"]["Storage"]["cobertura"] = invalid
    factor = build_decision_support(decision_payload).score.axes[1].factors[3]
    assert factor.value is None
    assert factor.coverage == 0


def test_price_coverage_changes_coverage_pillar_not_economic_grade(decision_payload):
    before = build_decision_support(decision_payload)
    decision_payload["resumo_executivo"]["por_provedor"][0]["cobertura"] = 0.5
    after = build_decision_support(decision_payload)
    assert before.score.axes[0] == after.score.axes[0]
    assert after.score.axes[2].value == 80
    assert after.score.value == 93
    assert after.score.status == "partial"


def test_no_technical_evidence_has_no_grade_even_with_large_savings(decision_payload):
    decision_payload.pop("topologia")
    w = decision_payload["decision_workloads"][0]
    w["source_metadata"] = {}
    w["targets"][0]["metadata"] = {}
    score = build_decision_support(decision_payload).score
    assert score.axes[1].coverage == 0.4  # capability match alone is insufficient
    assert score.status == "partial"
    assert score.value == score.lower_bound
    assert score.assessment_method == "conservative_bound"


def test_estimated_inventory_cost_keeps_grade_preliminary(decision_payload):
    decision_payload["topologia"]["resumo"] = {"cobertura": {"custo_estimado": 50}}
    score = build_decision_support(decision_payload).score
    assert score.status == "partial"
    assert score.axes[1].factors[3].provisional


@pytest.mark.parametrize("coverage,scored", [(0.4999, False), (0.5, True), (0.99999, True)])
def test_price_coverage_gate_and_rounding_do_not_certify_completeness(
    decision_payload, coverage, scored
):
    decision_payload["resumo_executivo"]["por_provedor"][0]["cobertura"] = coverage
    score = build_decision_support(decision_payload).provider_scores[0]
    assert score.value is not None
    assert (score.assessment_method == "evidence_normalized") is scored
    assert score.status != "complete"
