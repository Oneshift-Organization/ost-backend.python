from copy import deepcopy

import pytest

from app.domain.services.acoplamento_por_servico import montar_acoplamento_por_servico
from app.domain.services.decision_support import build_decision_support
from app.domain.services.migration_strategy import classify_strategy
from app.domain.services.technical_assessment import assess


def workload(source="medium", target="low"):
    return {
        "name": "Database",
        "category": "database",
        "cost": 100,
        "service_type": "managed_relational_database",
        "type_source": "curado",
        "source_metadata": {"vendor_lock_in_level": source, "requires_refactoring": False},
        "migration": {},
        "targets": [
            {
                "provider": "oci",
                "service_name": "DB",
                "service_type": "managed_relational_database",
                "metadata": {"vendor_lock_in_level": target, "requires_refactoring": False},
            }
        ],
    }


@pytest.mark.parametrize(
    "coverage,estimated,level,expected,provisional",
    [
        (0.2, False, "high", 0, False),
        (0.9, False, "low", 100, False),
        (0.89, False, "low", 50, False),
        (1, True, "low", 50, False),
        (1, True, "high", 0, True),
    ],
)
def test_inventory_lock_in_preserves_evidence_thresholds(
    coverage, estimated, level, expected, provisional
):
    w = workload()
    inventory = {
        "Database": {
            "cobertura": coverage,
            "cost_estimated": estimated,
            "nivel": "low",
            "exclusividade": {"nivel": level, "motivo": "Engine"},
        }
    }
    before = deepcopy(w)
    result = assess(w, "oci", inventory)
    assert result["lock_in"] == (expected, 1, provisional)
    assert result["evidence"]["lock_in_motivo"]
    assert w == before
    assert classify_strategy(w, "oci", w["targets"][0]) == classify_strategy(
        before, "oci", before["targets"][0]
    )


def test_class_is_provisional_unless_stronger_target_decides():
    w = workload(None)
    w["migration"] = {"fonte": "classe", "lock_in": "medium"}
    assert assess(w, "oci", {})["lock_in"] == (50, 1, True)
    assert assess(w, "oci", {})["evidence"]["lock_in_fonte"] == "classe"
    w["targets"][0]["metadata"]["vendor_lock_in_level"] = "high"
    assert assess(w, "oci", {})["lock_in"] == (0, 1, False)


def test_inferred_type_and_missing_target_do_not_gain_lock_score():
    w = workload()
    w["type_source"] = "inferido"
    assert assess(w, "oci", {})["lock_in"][0] is None
    w["type_source"] = "curado"
    w["targets"] = []
    assert assess(w, "oci", {})["lock_in"][0] is None


def test_proven_alternative_at_same_worst_level_is_not_provisional():
    w = workload(None)
    w["migration"] = {"fonte": "classe", "lock_in": "medium"}
    alternative = deepcopy(w["targets"][0])
    alternative["service_name"] = "Alternative DB"
    alternative["metadata"]["vendor_lock_in_level"] = "medium"
    w["targets"].append(alternative)
    assert assess(w, "oci", {})["lock_in"] == (50, 1, False)


def test_inventory_changes_score_and_coupling_but_not_financial_axis_or_7rs():
    w = workload()
    payload = {
        "provedor_origem": "aws",
        "decision_workloads": [w],
        "resumo_executivo": {
            "custo_mensal_atual": 100,
            "por_provedor": [
                {
                    "provedor": "oci",
                    "custo_estimado_total": 60,
                    "economia_mensal": 40,
                    "cobertura": 1,
                    "confianca": "official",
                }
            ],
        },
    }
    before = build_decision_support(payload)
    payload["topologia"] = {
        "resumo": {"cobertura": {"custo_estimado": 0}},
        "detalhe": {
            "servicos": {
                "Database": {
                    "cobertura": 1,
                    "nivel": "low",
                    "exclusividade": {"nivel": "high", "motivo": "Engine"},
                }
            }
        },
    }
    after = build_decision_support(payload)
    assert before.workload_strategies == after.workload_strategies
    axes_before = {a.id: a for a in before.provider_scores[0].axes}
    axes_after = {a.id: a for a in after.provider_scores[0].axes}
    assert axes_before["financial"] == axes_after["financial"]
    assert axes_before["technical"] != axes_after["technical"]
    payload["decision_support"] = after.model_dump()
    line = montar_acoplamento_por_servico(payload).servicos[0]
    assert (line.nivel, line.fonte, line.por_destino["oci"]) == ("high", "inventario", "high")


def test_score_partial_and_coupling_uses_same_source_but_old_policy_is_unchanged():
    w = workload(None)
    w["migration"] = {"fonte": "classe", "lock_in": "medium"}
    payload = {
        "provedor_origem": "aws",
        "decision_workloads": [w],
        "resumo_executivo": {
            "custo_mensal_atual": 100,
            "por_provedor": [
                {
                    "provedor": "oci",
                    "custo_estimado_total": 60,
                    "economia_mensal": 40,
                    "cobertura": 1,
                    "confianca": "official",
                }
            ],
        },
    }
    decision = build_decision_support(payload)
    assert decision.policy_version == "score-7rs-v5"
    assert decision.score.status == "partial"
    payload["decision_support"] = decision.model_dump()
    line = montar_acoplamento_por_servico(payload).servicos[0]
    assert (line.nivel, line.fonte, line.por_destino["oci"]) == ("medium", "classe", "medium")
    payload["decision_support"]["policy_version"] = "score-7rs-v4.2"
    line = montar_acoplamento_por_servico(payload).servicos[0]
    assert line.nivel is None
    assert line.por_destino["oci"] == "low"
