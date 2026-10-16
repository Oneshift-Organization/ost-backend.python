from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.analysis import (
    BillingItemSummary,
    EquivalentService,
    MigrationContext,
    ServiceCategoryGroup,
)
from app.domain.entities.analysis_job import AnalysisJob
from app.domain.services.decision_support import build_decision_support, number
from app.domain.services.migration_strategy import classify_strategy
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.schemas.analysis_v2 import AnalysisV2Response, ProviderComparison
from app.schemas.decision_support import AnalysisStartOptions, DecisionContext, DecisionSupport
from app.use_cases.analysis.agent_result import receber_analise_do_agente
from app.use_cases.analysis.decision_support import impose_decision, prepare_decision_payload
from app.use_cases.analysis.run_analysis_pipeline import RunAnalysisPipelineUseCase
from app.use_cases.terraform.build_blueprint import validate_migration_strategy


@pytest.fixture
def decision_payload() -> dict:
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "resumo_executivo": {
            "custo_mensal_atual": 1000,
            "recomendado": "oci",
            "por_provedor": [
                {
                    "provedor": "oci",
                    "custo_estimado_total": 600,
                    "economia_mensal": 400,
                    "cobertura": 1,
                    "confianca": "official",
                }
            ],
        },
        "premissas": {"confianca_analise": {"nivel": "high"}},
        "decision_workloads": [
            {
                "name": "Storage",
                "service_type": "fixture_storage",
                "type_source": "curado",
                "category": "storage",
                "cost": 1000,
                "migration": {"requer_refatoracao": False, "lock_in": "low"},
                "targets": [
                    {
                        "provider": "oci",
                        "service_type": "fixture_storage",
                        "service_name": "Object Storage",
                        "metadata": {
                            "requires_refactoring": False,
                            "vendor_lock_in_level": "low",
                        },
                    }
                ],
            }
        ],
        "topologia": {
            "detalhe": {
                "servicos": {
                    "Storage": {"nivel": "low", "cobertura": 1, "motivo": "Fixture completa"}
                }
            }
        },
        "decision_context": {
            "governance": [
                {
                    "provider": "oci",
                    "evidence": "Revisão do responsável — fixture sintética",
                    "region_approved": True,
                    "compliance_approved": True,
                    "operations_ready": True,
                }
            ]
        },
    }


def test_complete_score_and_exact_contributions(decision_payload):
    result = build_decision_support(decision_payload)
    assert result.score.status == "complete"
    assert result.score.value == 95  # F=90 * .5 + T=100 * .4 + cobertura=100 * .1
    assert sum(a.contribution for a in result.score.axes) == result.score.value
    assert result.score.axes[0].value == 90
    assert result.workload_strategies[0].strategy == "rehost"
    assert result.workload_strategies[0].status == "candidate"
    assert result.confidence == "high"
    assert len(result.input_fingerprint) == 64


def test_governance_is_not_a_score_axis(decision_payload):
    decision_payload.pop("decision_context")
    result = build_decision_support(decision_payload)
    assert [a.id for a in result.score.axes] == ["financial", "technical", "coverage"]
    assert result.score.status == "complete"
    assert result.score.value == 95
    assert result.score.coverage == 1.0
    assert result.confidence == "high"  # separate from score/data completeness


@pytest.mark.parametrize(("lock", "expected"), [("low", 95), ("medium", 91), ("high", 87)])
def test_lock_in_stays_in_technical_and_coverage_is_ten_percent(decision_payload, lock, expected):
    workload = decision_payload["decision_workloads"][0]
    workload["migration"]["lock_in"] = lock
    workload["targets"][0]["metadata"]["vendor_lock_in_level"] = lock
    result = build_decision_support(decision_payload)
    assert result.policy_version == "score-7rs-v4"
    axes = {a.id: a for a in result.score.axes}
    assert {k: v.weight for k, v in axes.items()} == {
        "financial": 0.5,
        "technical": 0.4,
        "coverage": 0.1,
    }
    assert [f.id for f in axes["technical"].factors] == [
        "compatibility",
        "refactoring",
        "lock_in",
        "dependencies",
    ]
    assert [f.id for f in axes["financial"].factors] == ["savings"]
    assert result.score.value == expected


def test_unknown_lock_in_is_pending_not_zero_technical_grade(decision_payload):
    workload = decision_payload["decision_workloads"][0]
    workload["migration"].pop("lock_in")
    workload["targets"][0]["metadata"].pop("vendor_lock_in_level")
    score = build_decision_support(decision_payload).score
    assert score.status == "partial"
    technical = score.axes[1]
    assert technical.factors[2].value is None
    assert technical.value == 100
    assert technical.coverage == 0.8
    assert score.value == 94.4
    assert score.coverage == 0.94


def test_benefits_cannot_be_injected_or_change_economics(decision_payload):
    expected = build_decision_support(decision_payload)
    decision_payload["corporate_benefits"] = {"approved_credits": 1000000}
    decision_payload["decision_support"] = {"corporate_benefits": {"score_effect": "bonus"}}
    actual = build_decision_support(decision_payload)
    assert actual == expected
    assert actual.corporate_benefits.score_effect == "none"
    assert all(o.eligibility == "not_verified" for o in actual.corporate_benefits.opportunities)


@pytest.mark.parametrize("version", ["score-7rs-v1", "score-7rs-v2", "score-7rs-v3"])
def test_old_decisions_keep_their_policy_and_have_no_new_benefits(decision_payload, version):
    saved = build_decision_support(decision_payload).model_dump(mode="json")
    saved["policy_version"] = version
    saved.pop("corporate_benefits")
    for scenario in [saved["score"], *saved["provider_scores"]]:
        scenario.pop("assessment_method")
        scenario.pop("missing_information")
        if version == "score-7rs-v3":
            scenario["axes"] = scenario["axes"][:2]
            scenario["axes"][1]["weight"] = 0.5
            scenario["axes"][1]["contribution"] = 50
        else:
            scenario["axes"][2]["id"] = "enterprise"
            scenario["axes"][2]["label"] = "Governança"
    old = DecisionSupport.model_validate(saved)
    assert old.policy_version == version
    assert old.corporate_benefits is None
    assert old.score.assessment_method == "conservative_bound"
    assert old.score.value == saved["score"]["value"]
    assert old.model_dump(mode="json")["score"]["axes"] == saved["score"]["axes"]


def test_stable_under_ordering_and_non_decision_fields(decision_payload):
    original = deepcopy(decision_payload)
    decision_payload["request_id"] = "irrelevant"
    decision_payload["created_at"] = "2099-01-01"
    decision_payload["decision_support"] = {"score": 100}
    decision_payload["decision_workloads"] = list(reversed(decision_payload["decision_workloads"]))
    decision_payload["resumo_executivo"]["por_provedor"].reverse()
    assert build_decision_support(original) == build_decision_support(decision_payload)


def test_changed_economics_change_score_and_fingerprint(decision_payload):
    a = build_decision_support(decision_payload)
    decision_payload["resumo_executivo"]["por_provedor"][0]["economia_mensal"] = 100
    b = build_decision_support(decision_payload)
    assert a.score.value > b.score.value
    assert a.input_fingerprint != b.input_fingerprint


@pytest.mark.parametrize("constraint", ["region_approved", "compliance_approved"])
def test_mandatory_constraint_cannot_be_compensated_by_savings(decision_payload, constraint):
    decision_payload["decision_context"]["governance"][0][constraint] = False
    result = build_decision_support(decision_payload)
    assert result.selected_provider is None
    assert result.score is None
    assert not result.provider_scores[0].eligible


@pytest.mark.parametrize("covered", [0, 0.49, None])
def test_insufficient_pricing_does_not_recommend(decision_payload, covered):
    decision_payload["resumo_executivo"]["por_provedor"][0]["cobertura"] = covered
    assert build_decision_support(decision_payload).selected_provider is None


def test_no_positive_savings_keeps_current_provider(decision_payload):
    decision_payload["resumo_executivo"]["por_provedor"][0]["economia_mensal"] = -1
    result = build_decision_support(decision_payload)
    assert result.selected_provider is None
    assert "manter provisoriamente" in result.selection_reason


def test_tax_is_not_a_workload(decision_payload):
    decision_payload["decision_workloads"].append({"name": "Tax", "category": "tax", "cost": 100})
    assert len(build_decision_support(decision_payload).workload_strategies) == 1


@pytest.mark.parametrize(
    "strategy,facts",
    [
        ("retain", {"retain_required": True}),
        (
            "retire",
            {"unused_confirmed": True, "retirement_approved": True, "dependencies_reviewed": True},
        ),
        (
            "repurchase",
            {
                "saas_replacement_approved": True,
                "functional_fit_confirmed": True,
                "data_migration_planned": True,
            },
        ),
        (
            "relocate",
            {
                "relocation_supported": True,
                "licenses_transferable": True,
                "unchanged_platform_confirmed": True,
            },
        ),
    ],
)
def test_special_rs_require_explicit_evidence(decision_payload, strategy, facts):
    workload = decision_payload["decision_workloads"][0]
    decision = classify_strategy(workload, "oci", workload["targets"][0], facts)
    assert decision.strategy == strategy
    assert decision.status == "declared"
    for key in facts:
        partial = {k: v for k, v in facts.items() if k != key}
        assert (
            classify_strategy(workload, "oci", workload["targets"][0], partial).strategy != strategy
        )


@pytest.mark.parametrize(
    "refactor,lock,strategy",
    [
        (False, "low", "rehost"),
        (False, "medium", "replatform"),
        (True, "high", "refactor"),
    ],
)
def test_catalog_rs_are_candidates_not_certified_migrations(
    decision_payload, refactor, lock, strategy
):
    workload = decision_payload["decision_workloads"][0]
    workload["migration"] = {"requer_refatoracao": refactor, "lock_in": lock}
    result = classify_strategy(workload, "oci", workload["targets"][0])
    assert result.strategy == strategy
    assert result.status == "candidate"
    assert result.missing_information


def test_missing_refactoring_does_not_mean_false(decision_payload):
    workload = decision_payload["decision_workloads"][0]
    workload["migration"] = {"lock_in": "low"}
    assert classify_strategy(workload, "oci", workload["targets"][0]).strategy is None


def test_conflicting_declared_strategies_abstain(decision_payload):
    workload = decision_payload["decision_workloads"][0]
    facts = {
        "unused_confirmed": True,
        "retirement_approved": True,
        "dependencies_reviewed": True,
        "saas_replacement_approved": True,
        "functional_fit_confirmed": True,
        "data_migration_planned": True,
    }
    assert classify_strategy(workload, "oci", None, facts).strategy is None


def test_retain_blocks_unadjusted_cross_cloud_price(decision_payload):
    decision_payload["decision_context"]["workloads"] = [
        {
            "provider": "oci",
            "workload": "Storage",
            "retain_required": True,
            "evidence": "Contrato vigente impede movimentação",
        }
    ]
    result = build_decision_support(decision_payload)
    assert result.selected_provider is None
    assert result.workload_strategies[0].strategy == "retain"


def test_ambiguous_equivalents_do_not_choose_by_list_order(decision_payload):
    workload = decision_payload["decision_workloads"][0]
    workload["targets"].append({"provider": "oci", "service_name": "Other"})
    result = build_decision_support(decision_payload)
    assert result.workload_strategies[0].strategy is None
    assert result.provider_scores[0].status == "not_scored"
    assert result.provider_scores[0].axes[1].factors[0].value is None


@pytest.mark.parametrize("malformed", [float("nan"), float("inf"), True, "bad", None])
def test_invalid_numbers_are_unknown(malformed):
    assert number(malformed) is None


def test_strict_declaration_validation_and_duplicates():
    with pytest.raises(ValidationError):
        DecisionContext.model_validate(
            {
                "governance": [
                    {
                        "provider": "oci",
                        "evidence": "test",
                        "region_approved": "false",
                    }
                ]
            }
        )
    entry = {"provider": "oci", "evidence": "test"}
    with pytest.raises(ValidationError):
        DecisionContext.model_validate({"governance": [entry, entry]})
    with pytest.raises(ValidationError):
        AnalysisStartOptions.model_validate({"score": 100})


def test_model_cannot_forge_score_strategy_or_provider(decision_payload):
    response = AnalysisV2Response(
        viability_score=1000,
        recommended_provider="FAKE",
        provider_comparison=[ProviderComparison(provider="fake")],
    )
    result = impose_decision(response, decision_payload)
    assert result.viability_score == 950
    assert result.recommended_provider == "OCI"
    assert result.oneshift_scores.overall_score == 95
    assert result.provider_comparison[0].provider == "OCI"
    assert result.decision_support.score.value == 95
    assert result.oneshift_scores.enterprise_score == 0
    assert result.provider_comparison[0].enterprise_score == 0
    assert "corporate_score" not in result.provider_comparison[0].model_dump()
    assert AnalysisV2Response.model_validate(result.model_dump()).decision_support


def test_agent_receipt_uses_same_engine_and_keeps_contestation_separate(decision_payload):
    prepared = prepare_decision_payload(decision_payload)
    result = receber_analise_do_agente(
        prepared,
        {"recommendation": "Inventada"},
        [
            {
                "tipo": "contestacao_preco",
                "severidade": "alta",
                "titulo": "Revisar preço",
                "detalhe": "x",
            }
        ],
    )
    assert result.resposta["viability_score"] == 950
    assert result.resposta["decision_support"]["score"]["value"] == 95
    assert result.confianca_final == "medium"
    assert result.resposta["decision_support"]["confidence"] == "high"
    assert result.autoridade.substituicoes


def test_serialization_round_trip_and_input_immutability(decision_payload):
    original = deepcopy(decision_payload)
    result = build_decision_support(decision_payload)
    assert DecisionSupport.model_validate_json(result.model_dump_json()) == result
    assert prepare_decision_payload(
        prepare_decision_payload(decision_payload)
    ) == prepare_decision_payload(decision_payload)
    assert decision_payload == original


@pytest.mark.asyncio
async def test_primary_and_fallback_models_share_deterministic_result(
    decision_payload, monkeypatch
):
    class Client:
        def __init__(self, score, fail=False):
            self.score, self.fail, self.prompt = score, fail, ""

        async def generate(self, prompt, response_model):
            self.prompt = prompt
            if self.fail:
                raise RuntimeError("simulated provider outage")
            return response_model(viability_score=self.score)

    primary, fallback = Client(999), Client(1)
    payload = prepare_decision_payload(decision_payload)
    a = await LLMOrchestrator(primary).generate_from_legacy(payload)
    monkeypatch.setattr(
        "app.infrastructure.llm.orchestrator._should_use_anthropic_fallback", lambda _: True
    )
    b = await LLMOrchestrator(
        Client(900, fail=True), fallback_client=fallback
    ).generate_from_legacy(payload)
    assert a.decision_support == b.decision_support
    assert a.oneshift_scores == b.oneshift_scores
    assert a.viability_score == b.viability_score == 950
    assert "decision_instructions" in fallback.prompt
    assert "decision_workloads" not in fallback.prompt
    # O modelo não devolve o decision_support: o backend impõe (saída cortada em fatura real)
    assert "decision_support inteiro" in fallback.prompt


@pytest.mark.asyncio
async def test_pipeline_prompt_hides_private_topology_but_score_uses_it(decision_payload):
    class Client:
        prompt = ""

        async def generate(self, prompt, response_model):
            self.prompt = prompt
            return response_model(viability_score=999)

    decision_payload["topologia"]["detalhe"]["private_network"] = "PRIVATE-NETWORK-SENTINEL"
    client = Client()
    payload = prepare_decision_payload(decision_payload)
    result = await LLMOrchestrator(client).generate_report(
        context=None, source_provider="aws", billing_period="2026-03", total_spend=1000,
        currency="USD", pools_json="{}", payload=payload,
    )
    assert result.decision_support == build_decision_support(payload)
    assert result.decision_support.score.axes[1].factors[3].coverage == 1
    assert "PRIVATE-NETWORK-SENTINEL" not in client.prompt


def test_legacy_results_remain_readable():
    old = AnalysisV2Response.model_validate({"viability_score": 650})
    assert old.viability_score == 650
    assert old.decision_support is None


def test_raw_metadata_does_not_inherit_default_false(decision_payload):
    workload = decision_payload["decision_workloads"][0]
    workload["source_metadata"] = {"vendor_lock_in_level": "low"}
    result = build_decision_support(decision_payload)
    assert result.score.status == "partial"
    assert result.workload_strategies[0].strategy is None


def test_inferred_service_mapping_is_not_confirmed_compatibility(decision_payload):
    decision_payload["decision_workloads"][0]["type_source"] = "inferido"
    result = build_decision_support(decision_payload)
    assert result.score.status == "not_scored"
    assert result.score.axes[1].factors[0].value is None
    assert result.workload_strategies[0].strategy is None


def test_unmatched_declaration_blocks_instead_of_disappearing(decision_payload):
    decision_payload["decision_context"]["workloads"] = [
        {
            "workload": "typo",
            "provider": "oci",
            "evidence": "stay",
            "retain_required": True,
        }
    ]
    result = build_decision_support(decision_payload)
    assert result.selected_provider is None
    assert "não encontrado" in result.provider_scores[0].blockers[0]


@pytest.mark.parametrize("strategy", ["retain", "retire", "repurchase", "relocate"])
def test_terraform_rejects_strategy_incompatible_with_generic_blueprint(strategy):
    payload = {
        "decision_support": {
            "workload_strategies": [
                {
                    "provider": "oci",
                    "strategy": strategy,
                }
            ]
        }
    }
    with pytest.raises(ValueError, match="blueprint específico"):
        validate_migration_strategy(payload, "oci")
    validate_migration_strategy(payload, "gcp")
    validate_migration_strategy({}, "oci")  # historical payloads retain their contract


def test_terraform_rejects_declared_governance_block(decision_payload):
    decision_payload["decision_context"]["governance"][0]["region_approved"] = False
    with pytest.raises(ValueError, match="Destino bloqueado"):
        validate_migration_strategy(prepare_decision_payload(decision_payload), "oci")


@pytest.mark.asyncio
async def test_real_pipeline_persists_authoritative_snapshot_and_scores(
    decision_payload, monkeypatch
):
    metadata = {"requires_refactoring": False, "vendor_lock_in_level": "low"}
    service = BillingItemSummary(
        service_name="Storage",
        provider="aws",
        service_type="object",
        type_source="curado",
        total_cost=Decimal(1000),
        migration_metadata=metadata,
        migration_expectation={"requer_refatoracao": False, "lock_in": "low"},
        equivalents=[
            EquivalentService(
                provider="oci",
                service_name="Object Storage",
                category="storage",
                service_type="object",
                metadata=metadata,
            )
        ],
    )
    context = MigrationContext(
        source_provider="aws",
        billing_period="2026-09",
        total_spend=Decimal(1000),
        categories=[ServiceCategoryGroup("storage", Decimal(1000), [service])],
        resumo_executivo=decision_payload["resumo_executivo"],
        premissas=decision_payload["premissas"],
    )
    monkeypatch.setattr(
        "app.use_cases.analysis.run_analysis_pipeline.BuildMigrationContextUseCase.execute",
        AsyncMock(return_value=context),
    )
    job = AnalysisJob(job_id=uuid4(), user_id=uuid4(), report_id=uuid4())
    repo = AsyncMock()
    orchestrator = AsyncMock()
    orchestrator.generate_report.return_value = AnalysisV2Response(viability_score=1)
    pipeline = RunAnalysisPipelineUseCase(
        repo,
        None,
        None,
        None,
        None,
        None,
        orchestrator,
    )
    await pipeline.execute(job, decision_context=decision_payload["decision_context"])
    assert job.status == "COMPLETED", job.error
    saved = job.ai_structured_output
    assert saved["decision_support"]["score"]["value"] == 94.7
    assert saved["oneshift_scores"]["overall_score"] == 95
    assert saved["decision_support"] == job.llm_input_payload["decision_support"]
    assert job.llm_input_payload["decision_context"] == decision_payload["decision_context"]
    assert "94.7/100" in job.llm_output
