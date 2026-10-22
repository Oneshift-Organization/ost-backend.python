from copy import deepcopy

import pytest

from app.domain.entities.institution_dashboard import dashboard_snapshot


def test_dashboard_is_projected_without_private_evidence_and_independent():
    raw = {
        "agent_views": {
            "finops": {
                "summary": "Economia preliminar",
                "key_findings": ["Preço oficial", None],
                "financial_flags": [{"message": "Validar", "theme": "custo", "secret": "private"}],
                "raw_payload": "private",
            },
            "risk_migration": {
                "recommended_migration_sequence": [
                    {
                        "name": "Validar",
                        "duration": None,
                        "detail": "Teste",
                        "resource_id": "private",
                    }
                ]
            },
        },
        "decision_support": {
            "workload_strategies": [
                {
                    "workload_id": "private",
                    "workload": "Compute",
                    "provider": "oci",
                    "strategy": "rehost",
                    "status": "candidate",
                    "rationale": "Validar aplicação",
                    "missing_information": ["Dependências"],
                    "evidence": [{"resource_id": "private"}],
                }
            ]
        },
    }
    before = deepcopy(raw)
    result = dashboard_snapshot(raw)
    assert raw == before
    assert "private" not in str(result)
    assert result["workload_strategies"][0]["strategy"] == "rehost"
    assert result["agent_views"]["finops"]["key_findings"] == ["Preço oficial"]
    raw["decision_support"]["workload_strategies"][0]["missing_information"].append("changed")
    assert result["workload_strategies"][0]["missing_information"] == ["Dependências"]


@pytest.mark.parametrize("value", [None, [], "invalid", 0])
def test_missing_or_malformed_sections_are_not_invented(value):
    result = dashboard_snapshot({"agent_views": value, "decision_support": value})
    assert result == {"agent_views": None, "workload_strategies": []}


def test_legacy_flags_and_unknown_strategy_remain_safe():
    result = dashboard_snapshot(
        {
            "agent_views": {
                "finops": {
                    "financial_flags": ["Legacy", None, {"message": "Unknown", "theme": []}]
                },
                "cloud_engineering": {"technical_flags": None},
            },
            "decision_support": {
                "workload_strategies": [
                    None,
                    {},
                    {
                        "workload": "DB",
                        "provider": "aws",
                        "strategy": "invented",
                        "status": "invented",
                        "alternative_targets": ["Database", 1],
                        "missing_information": None,
                    },
                ]
            },
        }
    )
    assert len(result["agent_views"]["finops"]["financial_flags"]) == 2
    assert result["agent_views"]["finops"]["financial_flags"][1]["theme"] == "outro"
    row = result["workload_strategies"][0]
    assert row["strategy"] is None and row["status"] == "undetermined"
    assert row["alternative_targets"] == ["Database"]
