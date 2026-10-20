"""Global weights change without rewriting saved results or internal criteria."""
from copy import deepcopy
from decimal import Decimal

import pytest

from app.domain.services.decision_support import build_decision_support
from app.schemas.decision_support import DecisionSupport
from app.tests.unit.domain.test_decision_support import (
    decision_payload as decision_payload,  # noqa: PLC0414 -- pytest fixture
)


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp", "oci"])
def test_new_weights_apply_to_every_destination(decision_payload, provider):
    decision_payload["resumo_executivo"]["por_provedor"][0]["provedor"] = provider
    decision_payload["decision_workloads"][0]["targets"][0]["provider"] = provider
    score = build_decision_support(decision_payload).provider_scores[0]
    assert [a.weight for a in score.axes] == [0.35, 0.5, 0.15]
    assert sum(Decimal(str(a.weight)) for a in score.axes) == Decimal(1)
    assert score.value == 96.5
    assert [f.weight for f in score.axes[1].factors] == [0.4, 0.3, 0.2, 0.1]
    assert [f.weight for f in score.axes[2].factors] == [0.3, 0.4, 0.3]


def test_conservative_partial_keeps_new_weights_without_redistribution(decision_payload):
    decision_payload["decision_workloads"][0]["cost"] = 100
    decision_payload["decision_workloads"].append(
        {"name": "Unknown", "category": "compute", "cost": 900, "targets": []}
    )
    score = build_decision_support(decision_payload).score
    # F=90, technical minimum=10, coverage=46. Missing 90% is not extrapolated.
    assert score.assessment_method == "conservative_bound"
    assert [a.contribution for a in score.axes] == [31.5, 5, 6.9]
    assert score.value == score.lower_bound == 43.4


@pytest.mark.parametrize("version", ["score-7rs-v4", "score-7rs-v4.1"])
def test_historical_weights_are_not_rebalanced_on_read(decision_payload, version):
    saved = build_decision_support(decision_payload).model_dump(mode="json")
    saved["policy_version"] = version
    for score in [saved["score"], *saved["provider_scores"]]:
        score.update(value=95, lower_bound=95, upper_bound=95)
        for axis, weight, contribution in zip(
            score["axes"], [0.5, 0.4, 0.1], [45, 40, 10], strict=True
        ):
            axis.update(weight=weight, contribution=contribution)
    original = deepcopy(saved)
    assert DecisionSupport.model_validate(saved).model_dump(mode="json") == original
    assert saved == original
