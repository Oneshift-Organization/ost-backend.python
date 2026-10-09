"""Regression: model omissions, invented rows and input ordering cannot change mappings."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.analysis import (
    BillingItemSummary,
    EquivalentService,
    MigrationContext,
    ServiceCategoryGroup,
)
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.schemas.analysis_v2 import AnalysisV2Response
from app.use_cases.analysis.agent_result import receber_analise_do_agente
from app.use_cases.analysis.decision_support import prepare_decision_payload
from app.use_cases.analysis.equivalence_projection import project_equivalences
from app.use_cases.analysis.run_analysis_pipeline import build_pipeline_llm_input_payload


@pytest.fixture
def snapshot():
    return {
        "provedor_origem": "aws", "moeda": "USD", "gasto_total": 1000,
        "resumo_executivo": {
            "custo_mensal_atual": 1000,
            "por_provedor": [{"provedor": "oci", "custo_estimado_total": 600,
                              "economia_mensal": 400, "cobertura": 1, "confianca": "low"}],
        },
        "pools_computacao": [{
            "familia": family, "regiao": "us-east-1",
            "equivalentes": [{"provedor": "OCI", "nome_servico": "OCI Compute",
                              "sku": family + "-target", "financeiro": {
                                  "custo_mensal_estimado": 100, "delta_custo": -20,
                                  "confianca_preco": "reference"}}],
        } for family in ["M7G", "M8G", "C7G", "R7G", "IS4GEN"]],
        "decision_workloads": [{
            "name": name, "category": category, "service_type": kind, "cost": 100,
            "type_source": "curado", "source_metadata": {"requires_refactoring": False},
            "targets": [{"provider": p, "service_name": f"{p}-{name}",
                         "metadata": {"requires_refactoring": False,
                                      "vendor_lock_in_level": "medium"}}
                        for p in ["oci", "azure", "gcp"]],
        } for name, category, kind in [
            ("AmazonEC2", "compute", "virtual_server"),
            ("AmazonRDS", "database", "relational_database"),
            ("AmazonS3", "storage", "object_storage"),
            ("AmazonEKS", "compute", "kubernetes"),
        ]],
        "custos_auxiliares": {"equivalentes_banco_dados": [
            {"provedor": "oci", "custo_mensal_estimado": 99999},
        ]},
    }


def test_full_mapping_retains_families_services_missing_prices_and_alternatives(snapshot):
    rows = project_equivalences(snapshot)
    assert len(rows) == 24  # 5 family + 3 named services, across all 3 target clouds
    assert len({r.workload_id for r in rows}) == 8
    oci = [r for r in rows if r.target_provider == "OCI"]
    assert len(oci) == 8
    assert not any(r.workload == "AmazonEC2" for r in rows)  # no aggregate/pool duplication
    rds = next(r for r in oci if r.workload == "AmazonRDS")
    assert rds.target_service == "oci-AmazonRDS"
    assert rds.estimated_monthly_cost is None  # never copy aggregate category prices per service
    assert rds.requires_refactoring is False
    assert rds.vendor_lock_in_level == "medium"
    pool = next(r for r in oci if r.source_service == "M7G")
    assert pool.target_sku == "M7G-target"
    assert pool.estimated_monthly_cost == 100
    assert pool.cost_delta == -20
    assert pool.pricing_confidence == "reference"
    unmapped = next(r for r in rows if r.source_service == "M7G" and r.target_provider == "GCP")
    assert unmapped.target_service is None
    assert unmapped.estimated_monthly_cost is None


def test_projection_stable_with_shuffled_order_and_duplicate_entries(snapshot):
    original = deepcopy(snapshot)
    expected = project_equivalences(snapshot)
    assert snapshot == original
    snapshot["pools_computacao"].reverse()
    snapshot["decision_workloads"].reverse()
    for workload in snapshot["decision_workloads"]:
        workload["targets"].reverse()
        workload["targets"].append(deepcopy(workload["targets"][0]))
    assert project_equivalences(snapshot) == expected
    pool = snapshot["pools_computacao"][0]
    alternative = deepcopy(pool["equivalentes"][0])
    alternative["sku"] = "alternative-sku"
    pool["equivalentes"].append(alternative)
    assert len(project_equivalences(snapshot)) == len(expected) + 1


def test_real_zero_commitments_and_inferred_metadata(snapshot):
    pool = snapshot["pools_computacao"][0]
    pool["custo_coberto_por_compromisso"] = True
    pool["equivalentes"][0]["financeiro"]["custo_mensal_estimado"] = 0
    rows = project_equivalences(snapshot)
    row = next(r for r in rows if r.source_service == "M7G" and r.target_provider == "OCI")
    assert row.estimated_monthly_cost == 0
    assert row.cost_delta is None
    snapshot["decision_workloads"][1]["type_source"] = "inferido"
    row = next(r for r in project_equivalences(snapshot) if r.workload == "AmazonRDS")
    assert row.requires_refactoring is None
    assert row.vendor_lock_in_level is None
    assert row.mapping_basis == "inferred_catalog"


@pytest.mark.parametrize("model_rows", [[], None, [{"workload": "invented", "cost_delta": "bad"}]])
async def test_both_entrypoints_restore_all_backend_rows(snapshot, model_rows):
    payload = prepare_decision_payload(snapshot)
    draft = {"recommendation_rationale": "Narrativa preservada",
             "cross_cloud_equivalents": model_rows}
    client = SimpleNamespace(generate=AsyncMock(return_value=draft))
    result = await LLMOrchestrator(client).generate_from_legacy(payload)
    expected = project_equivalences(payload)
    assert result.cross_cloud_equivalents == expected
    assert result.recommendation_rationale == draft["recommendation_rationale"]
    assert AnalysisV2Response.model_validate_json(result.model_dump_json()) == result
    submitted = receber_analise_do_agente(payload, draft).resposta
    assert submitted["cross_cloud_equivalents"] == [r.model_dump(mode="json") for r in expected]
    assert submitted["decision_support"] == result.decision_support.model_dump(mode="json")


def test_context_serializer_does_not_truncate_catalog_or_drop_zero_cost_service():
    targets = [EquivalentService(
        provider="oci", service_name=f"Object Storage {i}",
        category="storage", service_type="object_storage",
    ) for i in range(4)]
    services = [BillingItemSummary(
        service_name=name, provider="aws", total_cost=Decimal(cost),
        service_type="object_storage", equivalents=targets,
    ) for name, cost in [("AmazonS3", "100"), ("Free tier storage", "0")]]
    context = MigrationContext(
        source_provider="aws", billing_period="2026-03", total_spend=Decimal(100),
        categories=[ServiceCategoryGroup("storage", Decimal(100), services)],
    )
    payload, _ = build_pipeline_llm_input_payload(context)
    assert len(payload["servicos"][0]["equivalentes"]) == 4
    assert payload["decision_workloads"][0]["service_type"] == "object_storage"
    rows = project_equivalences(payload)
    assert len(rows) == 8
    assert len({r.workload_id for r in rows}) == 2
    assert all(r.estimated_monthly_cost is None for r in rows)
