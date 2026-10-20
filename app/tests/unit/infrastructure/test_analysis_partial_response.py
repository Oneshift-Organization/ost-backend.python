"""Exercise real client validation, not a mock returning an already parsed report."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.infrastructure.llm.claude_client import ClaudeLLMClient
from app.infrastructure.llm.nvidia_client import NvidiaLLMClient
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.schemas.analysis_v2 import AnalysisNarrativeDraft, AnalysisV2Response
from app.use_cases.analysis.agent_result import receber_analise_do_agente


@pytest.fixture
def partial_response():
    return {
        "viability_score": None,
        "oneshift_scores": {"technical_score": None},
        "decision_support": {"invalid_model_decision": True},
        "savings_summary": None,
        "recommendation_rationale": "Validar preços e dependências antes da migração.",
        "provider_comparison": [
            {
                "provider": "oci",
                "technical_score": None,
                "estimated_monthly_cost": None,
                "estimated_monthly_savings": None,
                "strengths": ["Portabilidade"],
            }
        ],
        "cross_cloud_equivalents": [
            {
                "workload": "Storage",
                "source_provider": "aws",
                "target_provider": "oci",
                "target_service": None,
                "estimated_monthly_cost": None,
                "cost_delta": None,
                "vendor_lock_in_level": None,
            }
        ],
        "flags": {"pricing_unavailable": True, "lock_in_risk": False, "other": ["Evidence"]},
        "agent_views": {
            "finops": {"financial_flags": [
                {"code": "pricing_unavailable", "message": "Preço ausente",
                 "impact_area": "finops"},
                "Alerta legado",
            ]},
            "cloud_engineering": {"technical_flags": [
                {"code": "unknown", "message": "Validar dependências"},
            ]},
            "risk_migration": {"risk_flags": [
                {"code": "lock_in", "message": "Validar portabilidade", "impact_area": "risk"},
            ]},
        },
    }


@pytest.mark.parametrize("provider", ["claude", "nvidia"])
async def test_client_validation_then_backend_authority(provider, partial_response):
    content = json.dumps(partial_response)
    if provider == "claude":
        client = ClaudeLLMClient(api_key="test-only")
        completion = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=content)],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            stop_reason="end_turn",
        )
        client._client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=completion))
        )
    else:
        client = NvidiaLLMClient(api_key="test-only")
        completion = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))
            ],
        )
        client._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=completion))
            )
        )
    payload = {
        "source_provider": "aws",
        "currency": "USD",
        "total_spend": 1000,
        "workloads": [
            {
                "canonical_service": "storage",
                "source_usage": {
                    "billed_cost": 1000,
                    "currency": "USD",
                },
                "equivalents": [
                    {
                        "provider": "oci",
                        "financials": {
                            "estimated_monthly_cost": 600,
                        },
                    }
                ],
            }
        ],
    }
    result = await LLMOrchestrator(client).generate_from_legacy(payload)
    assert type(result) is AnalysisV2Response
    assert result.recommendation_rationale == partial_response["recommendation_rationale"]
    assert result.provider_comparison[0].estimated_monthly_cost == 600
    assert result.provider_comparison[0].estimated_monthly_savings == 400
    assert result.provider_comparison[0].strengths == ["Portabilidade"]
    assert isinstance(result.provider_comparison[0].technical_score, int)
    assert result.decision_support.contract_version == "oneshift-decision-v1"
    assert result.cross_cloud_equivalents[0].estimated_monthly_cost is None
    assert result.cross_cloud_equivalents[0].cost_delta is None
    assert result.cross_cloud_equivalents[0].target_service is None
    assert result.flags["pricing_unavailable"]
    assert result.flags["lock_in_risk"] == []
    assert result.flags["other"] == ["Evidence"]
    assert result.agent_views.finops.financial_flags[0].message == "Preço ausente"
    # Alerta em texto (formato antigo) vira alerta estruturado, com título e tema neutro
    assert result.agent_views.finops.financial_flags[1].message == "Alerta legado"
    assert result.agent_views.finops.financial_flags[1].theme == "outro"
    assert result.agent_views.cloud_engineering.technical_flags[0].code == "unknown"
    assert result.agent_views.risk_migration.risk_flags[0].impact_area == "risk"
    assert AnalysisV2Response.model_validate_json(result.model_dump_json()) == result


def test_transport_does_not_mutate_input_or_accept_bad_narrative(partial_response):
    original = deepcopy(partial_response)
    AnalysisNarrativeDraft.model_validate(partial_response)
    assert partial_response == original
    partial_response["action_plan"] = 123
    with pytest.raises(ValidationError):
        AnalysisNarrativeDraft.model_validate(partial_response)


def test_public_scores_stay_strict_and_real_zero_is_preserved():
    with pytest.raises(ValidationError):
        AnalysisV2Response.model_validate({"provider_comparison": [{"technical_score": None}]})
    result = AnalysisV2Response.model_validate(
        {
            "cross_cloud_equivalents": [
                {
                    "estimated_monthly_cost": 0,
                    "cost_delta": 0,
                },
                {},
            ]
        }
    )
    assert result.cross_cloud_equivalents[0].estimated_monthly_cost == 0
    assert result.cross_cloud_equivalents[1].estimated_monthly_cost is None


async def test_invalid_dict_must_not_become_blank_success():
    client = SimpleNamespace(generate=AsyncMock(return_value={"action_plan": 123}))
    with pytest.raises(ValidationError):
        await LLMOrchestrator(client).generate_from_legacy({})


def test_agent_submission_keeps_narrative_and_unknown_prices(partial_response):
    response = receber_analise_do_agente({"decision_support": {}}, partial_response).resposta
    assert response["recommendation_rationale"] == partial_response["recommendation_rationale"]
    assert response["cross_cloud_equivalents"] == []  # no trusted mapping snapshot
    assert response["agent_views"]["finops"]["financial_flags"][0]["code"] == "pricing_unavailable"
    assert response["provider_comparison"] == []  # no trustworthy price snapshot
    assert response["flags"]["pricing_unavailable"]


def test_draft_preserves_payback_not_owned_by_score_engine(partial_response):
    partial_response["savings_summary"] = {
        "payback_period_months": 12,
        "total_monthly_savings_usd": None,
    }
    assert (
        AnalysisNarrativeDraft.model_validate(
            partial_response
        ).savings_summary.payback_period_months
        == 12
    )
