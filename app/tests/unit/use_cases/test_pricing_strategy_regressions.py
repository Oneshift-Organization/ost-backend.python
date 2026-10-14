"""Synthetic regressions for missing prices, false mappings and actionable 7 Rs."""

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.analysis import ServiceComparison, ServiceMetadata
from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.services.category_calculator import CategoryRate, estimate_by_cost_ratio
from app.domain.services.decision_support import build_decision_support
from app.domain.services.migration_strategy import classify_workload_strategy, technical_evidence
from app.domain.services.service_identity import is_financial_component, service_key
from app.schemas.analysis_v2 import AnalysisV2Response
from app.use_cases.analysis.agent_result import receber_analise_do_agente
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
    _reviewed_billing_identity,
)
from app.use_cases.analysis.decision_support import impose_decision, prepare_decision_payload
from app.use_cases.analysis.equivalence_pricing import reconcile_pricing
from app.use_cases.analysis.equivalence_projection import project_equivalences


def workload(name="S3", cost=100, kind="object_storage"):
    return {
        "name": name, "cost": cost, "category": "storage", "service_type": kind,
        "source_metadata": {}, "type_source": "curado",
        "targets": [{"provider": "oci", "service_name": "Object Storage",
                     "service_type": kind, "metadata": {}}],
    }


@pytest.fixture
def priced_payload():
    return {
        "provedor_origem": "aws", "moeda": "USD",
        "decision_workloads": [workload()],
        "custos_auxiliares": {
            "armazenamento_objetos": 100,
            "equivalentes_armazenamento_objetos": [{
                "provedor": "oci", "custo_mensal_estimado": 60,
                "delta_custo": -40, "confianca_preco": "reference",
                "taxa_origem": "0.10", "taxa_destino": "0.06", "unidade": "gb_month",
            }],
        },
        "resumo_executivo": {"custo_mensal_atual": 150, "por_provedor": [{
            "provedor": "oci", "custo_estimado_total": 110, "economia_mensal": 40,
            "cobertura": 0.6667, "confianca": "reference",
        }]},
    }


def test_service_prices_reconcile_with_graph_and_unchanged_balance(priced_payload):
    original = deepcopy(priced_payload)
    rows = project_equivalences(priced_payload)
    assert priced_payload == original
    assert len(rows) == 1
    row = rows[0]
    assert (row.estimated_monthly_cost, row.cost_delta, row.source_monthly_cost) == (60, -40, 100)
    assert row.pricing_method == "category_rate_ratio"
    assert row.pricing_evidence["unidade"] == "gb_month"
    ledger = reconcile_pricing(rows, priced_payload)[0]
    assert ledger["status"] == "reconciled"
    assert ledger["unchanged_source_balance"] == 50
    response = impose_decision(AnalysisV2Response(), priced_payload)
    assert response.pricing_reconciliation[0] == ledger


def test_cent_allocation_alternatives_and_ordering(priced_payload):
    priced_payload["decision_workloads"] = [workload(n, c) for n, c in [("A", 33), ("B", 67)]]
    for w in priced_payload["decision_workloads"]:
        w["targets"].append({**w["targets"][0], "service_name": "Alternative storage"})
    expected = project_equivalences(priced_payload)
    assert len(expected) == 4
    included = [r for r in expected if r.included_in_estimate]
    assert sum(r.estimated_monthly_cost for r in included) == 60
    assert sum(r.source_monthly_cost for r in included) == 100
    priced_payload["decision_workloads"].reverse()
    for w in priced_payload["decision_workloads"]:
        w["targets"].reverse()
    assert project_equivalences(priced_payload) == expected


@pytest.mark.parametrize("change", ["partial", "inferred", "different_capability"])
def test_unattributable_category_stays_visible_without_fake_service_price(priced_payload, change):
    w = priced_payload["decision_workloads"][0]
    if change == "partial":
        w["cost"] = 40
    elif change == "inferred":
        w["type_source"] = "inferido"
    else:
        w["service_type"] = "container_registry"
    rows = project_equivalences(priced_payload)
    service = next(r for r in rows if r.workload == "S3")
    aggregate = next(r for r in rows if r.mapping_basis == "category_budget")
    assert service.estimated_monthly_cost is None
    assert service.pricing_reason
    assert aggregate.estimated_monthly_cost == 60
    assert reconcile_pricing(rows, priced_payload)[0]["status"] == "reconciled"


def test_unreconciled_snapshot_does_not_claim_consistency(priced_payload):
    priced_payload["resumo_executivo"]["por_provedor"][0]["custo_estimado_total"] = 1
    assert reconcile_pricing(project_equivalences(priced_payload), priced_payload)[0][
        "status"
    ] == "review_required"


def test_incompatible_units_cannot_produce_ratio():
    rates = {p: CategoryRate(p, Decimal(1), u) for p, u in [("aws", "gb"), ("oci", "gb_month")]}
    assert estimate_by_cost_ratio(
        source_provider="aws", source_cost=Decimal(100), rates=rates,
    ) == []


@pytest.mark.parametrize("kind,expected", [
    ("virtual_server", "rehost"), ("object_storage", "replatform"),
    ("relational_database", "replatform"), ("container_registry", "replatform"),
])
def test_capability_candidates_do_not_invent_technical_facts(kind, expected):
    w = workload(kind=kind)
    result = classify_workload_strategy(w, "oci")
    assert result.strategy == expected
    assert result.status == "candidate"
    assert result.confidence == "low"
    assert result.assumptions and result.missing_information
    assert technical_evidence(w, w["targets"][0]) == (None, None)


def test_alternatives_keep_candidate_but_conflicts_are_explicit():
    w = workload()
    w["targets"].append({**w["targets"][0], "service_name": "Alternative"})
    assert classify_workload_strategy(w, "oci").strategy == "replatform"
    w["targets"][1]["metadata"] = {"requires_refactoring": True}
    result = classify_workload_strategy(w, "oci")
    assert result.strategy is None
    assert result.rule == "7r.alternatives_disagree"
    assert len(result.alternative_targets) == 2


def test_financial_charges_excluded_even_with_wrong_compute_category(priced_payload):
    priced_payload["decision_workloads"].append(workload("Savings Plans for AWS Compute usage"))
    assert len(project_equivalences(priced_payload)) == 1
    assert len(build_decision_support(priced_payload).workload_strategies) == 1
    assert not is_financial_component("AmazonEC2")


@pytest.mark.parametrize("name", ["Amazon Elastic Container Registry Public", "AmazonECR"])
async def test_registry_alias_overrides_block_storage_and_no_usage_does_not_remove_mapping(name):
    uid, rid = uuid4(), uuid4()
    item = BillingEnriched(
        user_id=uid, report_id=rid, provider="aws", billing_period="2026-03",
        service_name=name, cost=Decimal(100), currency="USD", source_line_hash="fixture",
        pricing_source="fixture", pricing_confidence="low", category="storage",
        service_type="block_storage", usage_amount=None,
    )
    comparisons = [
        ServiceComparison(uuid4(), "compute", "container_registry", "aws", "Amazon ECR"),
        ServiceComparison(uuid4(), "compute", "container_registry", "gcp", "Artifact Registry"),
        ServiceComparison(uuid4(), "storage", "block_storage", "gcp", "Persistent Disk"),
    ]
    metadata = ServiceMetadata(uuid4(), "gcp", "compute", "ArtifactRegistry",
                               {"requires_refactoring": False})
    repo = AsyncMock()
    repo.list_by_provider.return_value = comparisons[:1]
    repo.list_all.return_value = comparisons
    repo.find_equivalents.return_value = comparisons[1:]
    metas = AsyncMock()
    metas.list_by_providers.side_effect = [[], [metadata]]
    reports = AsyncMock()
    reports.get_by_id.return_value = SimpleNamespace(
        status="done", provider=SimpleNamespace(value="aws"),
    )
    items = AsyncMock()
    items.list_by_report.return_value = [item]
    ctx = await BuildMigrationContextUseCase(reports, items, metas, repo).execute(
        BuildMigrationContextInput(user_id=uid, report_id=rid),
    )
    service = ctx.categories[0].services[0]
    assert service.service_type == "container_registry"
    assert [e.service_name for e in service.equivalents] == ["Artifact Registry"]
    assert service.equivalents[0].metadata == metadata.metadata
    assert service_key("aws", name) == service_key("aws", "Amazon ECR")
    corrected = _reviewed_billing_identity(item, "aws")
    assert corrected.component_type == "unclassified"
    assert item.service_type == "block_storage"  # persisted input untouched


@pytest.mark.parametrize("kind,expected", [
    ("managed_relational_database", "replatform"), ("managed_kubernetes", "replatform"),
    ("archive_cold_storage", "replatform"), ("translation", "refactor"),
    ("natural_language_processing", "refactor"), ("managed_airflow", "replatform"),
])
def test_production_catalog_capability_names(kind, expected):
    result = classify_workload_strategy(workload(kind=kind), "oci")
    assert result.strategy == expected
    assert result.confidence == "low"


def test_declared_and_conflicting_alternatives_do_not_weaken_safety():
    w = workload()
    w["targets"].append({**w["targets"][0], "service_name": "Alternative"})
    result = classify_workload_strategy(w, "oci", {"retain_required": True})
    assert result.strategy == "retain" and result.status == "declared"
    w["type_source"] = "inferido"
    assert classify_workload_strategy(w, "oci").strategy is None


def test_class_hypothesis_remains_separate_from_technical_score():
    w = workload(kind="interactive_query")
    w["migration"] = {"classe_migracao": "refactor", "fonte": "classe"}
    assert classify_workload_strategy(w, "oci").strategy == "refactor"
    assert technical_evidence(w, w["targets"][0]) == (None, None)


def test_both_entrypoints_preserve_pricing_and_ignore_model_financials(priced_payload):
    draft = {"pricing_reconciliation": [{"status": "reconciled", "estimated_total": -999}],
             "cross_cloud_equivalents": []}
    submitted = receber_analise_do_agente(prepare_decision_payload(priced_payload), draft).resposta
    expected = impose_decision(AnalysisV2Response(), priced_payload)
    assert submitted["pricing_reconciliation"] == expected.pricing_reconciliation
    assert submitted["cross_cloud_equivalents"] == [
        e.model_dump() for e in expected.cross_cloud_equivalents
    ]


@pytest.mark.parametrize("name,kind", [
    ("Amazon Elastic Container Registry Public", "container_registry"),
    ("Amazon Glacier", "archive_cold_storage"),
    ("Amazon Elastic Container Service for Kubernetes", "managed_kubernetes"),
    ("Amazon Managed Workflows for Apache Airflow", "managed_airflow"),
    ("AmazonRDS", "managed_relational_database"), ("AmazonS3", "object_storage"),
])
def test_aliases_resolve_against_actual_shipped_catalog(name, kind):
    catalog = Path(__file__).resolve().parents[3] / (
        "infrastructure/database/models/cloud_services_mapping.csv"
    )
    with catalog.open() as file:
        entries = list(csv.DictReader(file))
    matches = [e for e in entries if e["provider"] == "aws"
               and service_key("aws", e["service_name"]) == service_key("aws", name)]
    assert any(e["service_type"] == kind for e in matches)


def test_compute_commitment_leaves_the_source_with_the_pools(priced_payload):
    # O destino pagou on-demand as horas que o Savings Plan cobria: o plano sai da
    # origem junto, em vez de ficar no saldo mantido ao custo atual.
    priced_payload["resumo_executivo"]["custo_mensal_atual"] = 170
    priced_payload["premissas"] = {"compromisso_de_compute": {"por_provedor": {"oci": 20}}}
    ledger = reconcile_pricing(project_equivalences(priced_payload), priced_payload)[0]
    assert (ledger["status"], ledger["source_covered"], ledger["unchanged_source_balance"]) == (
        "reconciled",
        120,
        50,
    )


def test_payload_without_commitment_premise_reconciles_as_before(priced_payload):
    # Snapshot preparado antes da premissa existir: a mesma reconciliação de
    # sempre. É o que permite trocar o hash sem subir AUTHORITY_VERSION.
    rows = project_equivalences(priced_payload)
    sem = reconcile_pricing(rows, priced_payload)
    priced_payload["premissas"] = {"fx": {}}
    assert reconcile_pricing(rows, priced_payload) == sem
    assert sem[0]["source_covered"] == 100
