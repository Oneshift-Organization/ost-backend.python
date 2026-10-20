"""Retention is a decision, not a failed analysis. Synthetic provider-neutral inputs."""

from copy import deepcopy

import pytest

from app.domain.services.decision_support import build_decision_support
from app.schemas.analysis_v2 import AnalysisV2Response
from app.tests.unit.domain.test_decision_support import (
    decision_payload as decision_payload,  # noqa: PLC0414
)
from app.use_cases.analysis.decision_support import impose_decision


@pytest.mark.parametrize("source", ["aws", "azure", "gcp", "oci"])
def test_more_expensive_destinations_recommend_staying_with_explainable_score(
    decision_payload, source
):
    decision_payload["provedor_origem"] = source
    target = "azure" if source == "oci" else "oci"
    row = decision_payload["resumo_executivo"]["por_provedor"][0]
    row.update(provedor=target, custo_estimado_total=1800, economia_mensal=-800)
    decision_payload.pop("topologia")
    result = build_decision_support(decision_payload)
    assert result.outcome == "retain"
    assert result.selected_provider is None  # not a migration destination
    assert result.score.value is not None
    assert result.score.axes[0].value == 0  # negative savings affect financial score
    assert "Permanecer" in result.selection_reason
    assert result.comparison_basis[0].cost_delta == 800
    assert result.comparison_basis[0].current_monthly_cost == 1000
    assert result.comparison_basis[0].target_monthly_cost == 1800
    assert result.score == result.provider_scores[0]


def test_insufficient_technical_evidence_gets_conservative_not_extrapolated_grade(decision_payload):
    decision_payload["decision_workloads"] = []
    result = build_decision_support(decision_payload)
    assert result.score.status == "partial"
    assert result.score.assessment_method == "conservative_bound"
    assert result.score.value == result.score.lower_bound
    assert result.score.missing_information
    assert abs(sum(a.contribution for a in result.score.axes) - result.score.value) < 0.02


def test_absent_quotes_are_inconclusive_not_proof_of_retention(decision_payload):
    decision_payload["resumo_executivo"]["por_provedor"] = []
    result = build_decision_support(decision_payload)
    assert result.outcome == "inconclusive"
    assert result.score is None
    assert result.comparison_basis == []


def test_blocked_cheaper_destination_is_not_called_more_expensive(decision_payload):
    decision_payload["decision_context"]["governance"][0]["compliance_approved"] = False
    result = build_decision_support(decision_payload)
    assert result.outcome == "inconclusive"
    assert result.score is not None
    assert result.selected_provider is None


def test_retention_is_order_independent_and_does_not_mutate_inputs(decision_payload):
    rows = decision_payload["resumo_executivo"]["por_provedor"]
    rows[0].update(custo_estimado_total=1200, economia_mensal=-200)
    rows.append(
        {**rows[0], "provedor": "gcp", "custo_estimado_total": 1400, "economia_mensal": -400}
    )
    before = deepcopy(decision_payload)
    first = build_decision_support(decision_payload)
    assert decision_payload == before
    rows.reverse()
    assert first == build_decision_support(decision_payload)
    assert first.score.provider == "oci"  # closest economic alternative, not a recommendation


def test_model_cannot_replace_retention_with_migration_or_fake_savings(decision_payload):
    row = decision_payload["resumo_executivo"]["por_provedor"][0]
    row.update(custo_estimado_total=1800, economia_mensal=-800)
    response = impose_decision(
        AnalysisV2Response(recommended_provider="OCI", recommendation="Migrate"), decision_payload
    )
    assert response.recommended_provider == ""
    assert response.decision_support.outcome == "retain"
    assert response.decision_support.score.value is not None
    assert "Permanecer" in response.recommendation
    assert response.savings_summary.total_monthly_savings_usd == 0
    assert response.provider_comparison[0].estimated_monthly_savings == -800


@pytest.mark.parametrize("cost,coverage", [(None, 1), (1200, 0), (-1, 1)])
def test_invalid_or_unpriced_comparison_is_not_proof_of_retention(decision_payload, cost, coverage):
    row = decision_payload["resumo_executivo"]["por_provedor"][0]
    row.update(custo_estimado_total=cost, economia_mensal=-200, cobertura=coverage)
    assert build_decision_support(decision_payload).outcome == "inconclusive"
