from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.entities.analysis_job import AnalysisJob
from app.domain.repositories.i_analysis_job_repo import IAnalysisJobRepository
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.schemas.analysis_v2 import AnalysisV2Response
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)

# Shorthand used inside _add_financials to silently ignore bad Decimal conversions
_suppress = contextlib.suppress


@dataclass(frozen=True)
class RunAnalysisPipelineInput:
    user_id: UUID
    report_id: UUID


def _aggregate_pools_by_family(
    pools: tuple, pool_equivalents: dict
) -> tuple[list[dict], list[str]]:
    """Aggregate per-project pools into per-family pools for the LLM payload.

    Groups by (machine_family, region) and sums capacity/costs. Keeps
    source_projects so the family entry is traceable back to original projects.
    Equivalents are aggregated by (provider, service_name) summing cost_delta
    and estimated_monthly_cost — the math is preserved exactly.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for p in pools:
        if p.capacity.vcpus <= 0 and p.capacity.ram_gib <= 0:
            continue
        key = (p.pool_key.machine_family, p.pool_key.region)
        if key not in buckets:
            buckets[key] = {
                "family": p.pool_key.machine_family,
                "region": p.pool_key.region,
                "vcpus": Decimal("0"),
                "ram_gib": Decimal("0"),
                "compute_base": Decimal("0"),
                "os_licensing": Decimal("0"),
                "source_projects": [],
                "pool_keys": [],
            }
        b = buckets[key]
        b["vcpus"] += p.capacity.vcpus
        b["ram_gib"] += p.capacity.ram_gib
        b["compute_base"] += p.costs.compute_base
        b["os_licensing"] += p.costs.os_licensing
        b["source_projects"].append(p.pool_key.project_id)
        b["pool_keys"].append(
            f"{p.pool_key.project_id}/{p.pool_key.machine_family}/{p.pool_key.region}"
        )

    compute_pools: list[dict] = []
    missing_equiv_family_ids: list[str] = []

    for (family, region), b in buckets.items():
        # Aggregate equivalents from all constituent project pools
        agg_equivs: dict[tuple[str, str], dict] = {}
        for pool_key in b["pool_keys"]:
            for e in pool_equivalents.get(pool_key, []):
                agg_key = (e.provider, e.service_name)
                if agg_key not in agg_equivs:
                    agg_equivs[agg_key] = _serialize_equivalent(e)
                    # reset financials to accumulate
                    fin = agg_equivs[agg_key].get("financials")
                    if fin:
                        agg_equivs[agg_key]["financials"] = dict(fin)
                else:
                    existing = agg_equivs[agg_key]
                    new_fin = getattr(e, "financials", None)
                    if new_fin and existing.get("financials"):
                        _add_financials(existing["financials"], new_fin)
                    inst = getattr(e, "instances", 1)
                    if inst > existing.get("instances", 1):
                        existing["instances"] = inst

        family_id = f"{family}/{region}"
        if not agg_equivs:
            missing_equiv_family_ids.append(family_id)

        compute_pools.append({
            "family": family,
            "region": region,
            "source_projects": b["source_projects"],
            "capacity": {
                "vcpus": round(float(b["vcpus"]), 2),
                "ram_gib": round(float(b["ram_gib"]), 2),
            },
            "costs": {
                "compute_base": round(float(b["compute_base"]), 2),
                "os_licensing": round(float(b["os_licensing"]), 2),
            },
            "equivalents": list(agg_equivs.values()),
        })

    return compute_pools, missing_equiv_family_ids


def _add_financials(existing: dict, new_fin) -> None:
    """Accumulate estimated_monthly_cost and cost_delta in place (Decimal-safe)."""
    for field_name in ("estimated_monthly_cost", "cost_delta"):
        existing_val = existing.get(field_name)
        new_val = getattr(new_fin, field_name, None)
        if existing_val is not None and new_val is not None:
            with _suppress():
                existing[field_name] = str(Decimal(str(existing_val)) + Decimal(str(new_val)))


def _serialize_pools(ctx) -> tuple[str, list[str]]:
    """Serialize capacity pools aggregated by family into the LLM payload."""
    pools = getattr(ctx, "pools", ())
    auxiliary = getattr(ctx, "auxiliary", None)
    pool_equivalents: dict = getattr(ctx, "pool_equivalents", {})
    database_equivalents: list = getattr(ctx, "database_equivalents", [])

    compute_pools, missing_equiv_pool_ids = _aggregate_pools_by_family(
        pools, pool_equivalents
    )

    payload: dict[str, object] = {"compute_pools": compute_pools}

    if auxiliary is not None and auxiliary.any_positive:
        aux_payload: dict[str, object] = {}
        if auxiliary.gke_control_plane > 0:
            aux_payload["gke_control_plane"] = round(float(auxiliary.gke_control_plane), 2)
        if auxiliary.storage_ssd > 0:
            aux_payload["storage_ssd"] = round(float(auxiliary.storage_ssd), 2)
        if auxiliary.storage_hdd > 0:
            aux_payload["storage_hdd"] = round(float(auxiliary.storage_hdd), 2)
        if auxiliary.network_egress > 0:
            aux_payload["network_egress"] = round(float(auxiliary.network_egress), 2)
        if auxiliary.database > 0:
            aux_payload["database"] = round(float(auxiliary.database), 2)
            if database_equivalents:
                aux_payload["database_equivalents"] = [
                    _serialize_equivalent(e) for e in database_equivalents
                ]
        if auxiliary.orphan > 0:
            aux_payload["unmapped"] = {
                "cost": round(float(auxiliary.orphan), 2),
                "skus": list(auxiliary.orphan_skus),
            }
        payload["auxiliary_costs"] = aux_payload

    mapping_confidence = getattr(ctx, "mapping_confidence", None)
    if mapping_confidence:
        payload["mapping_confidence"] = {
            level: round(float(cost), 2) for level, cost in mapping_confidence.items()
        }

    return json.dumps(payload, default=str), missing_equiv_pool_ids


def build_pipeline_llm_input_payload(ctx) -> tuple[dict, list[str]]:
    pools_json, missing_equiv_ids = _serialize_pools(ctx)
    context_payload = json.loads(pools_json)
    context_payload["source_provider"] = ctx.source_provider
    context_payload["billing_period"] = ctx.billing_period
    context_payload["total_spend"] = float(ctx.total_spend)
    context_payload["currency"] = ctx.currency
    return context_payload, missing_equiv_ids


def _serialize_equivalent(equiv) -> dict:
    """Serialize a single EquivalentService to a JSON-safe dict."""
    d: dict[str, object] = {
        "provider": equiv.provider,
        "service_name": equiv.service_name,
        "category": equiv.category,
        "service_type": equiv.service_type,
        "match_basis": getattr(equiv, "match_basis", "pool_capacity"),
    }
    instances = getattr(equiv, "instances", 1)
    if instances > 1:
        d["instances"] = instances

    tm = getattr(equiv, "technical_match", None)
    if tm is not None:
        d["technical_match"] = {
            k: str(v) for k, v in [("vcpus", tm.vcpus), ("ram_gb", tm.ram_gb)] if v is not None
        }

    fin = getattr(equiv, "financials", None)
    if fin is not None:
        fin_dict: dict[str, object] = {}
        if fin.estimated_monthly_cost is not None:
            fin_dict["estimated_monthly_cost"] = str(fin.estimated_monthly_cost)
        if fin.cost_delta is not None:
            fin_dict["cost_delta"] = str(fin.cost_delta)
        fin_dict["pricing_confidence"] = fin.pricing_confidence
        d["financials"] = fin_dict

    meta = getattr(equiv, "metadata", None)
    if meta:
        d["metadata"] = meta

    docs = getattr(equiv, "docs_costs", None)
    if docs:
        d["docs_costs"] = docs

    return d


def serialize_as_legacy(ctx) -> dict:
    """Serialize MigrationContext into the legacy workload JSON format.

    Converts capacity pools + pool_equivalents into the ``workloads[]`` schema
    that was the original LLM input format, so the legacy pipeline sends a
    familiar payload to the model. See docs/refactor/analysis-v2-spec.md §5.
    """
    workloads: list[dict] = []
    pool_equivalents: dict = getattr(ctx, "pool_equivalents", {})
    pools = getattr(ctx, "pools", ())
    auxiliary = getattr(ctx, "auxiliary", None)
    db_equivalents: list = getattr(ctx, "database_equivalents", [])

    for pool in pools:
        if pool.capacity.vcpus <= 0 and pool.capacity.ram_gib <= 0:
            continue
        pool_key = (
            f"{pool.pool_key.project_id}/"
            f"{pool.pool_key.machine_family}/"
            f"{pool.pool_key.region}"
        )
        equivs = pool_equivalents.get(pool_key, [])
        workloads.append({
            "canonical_service": "virtual_server",
            "category": "compute",
            "service_name": (
                f"{pool.pool_key.machine_family} Compute ({pool.pool_key.project_id})"
            ),
            "service_type": "virtual_server",
            "source_usage": {
                "sku_id": pool_key,
                "billed_cost": round(float(pool.monthly_cost), 6),
                "usage_quantity": round(float(pool.capacity.vcpus), 2),
                "currency": ctx.currency,
            },
            "equivalents": [_equiv_to_legacy(e) for e in equivs],
        })

    if auxiliary and auxiliary.database > 0 and db_equivalents:
        workloads.append({
            "canonical_service": "relational_database",
            "category": "database",
            "service_name": "Database Services",
            "service_type": "relational_database",
            "source_usage": {
                "sku_id": "database",
                "billed_cost": round(float(auxiliary.database), 6),
                "currency": ctx.currency,
            },
            "equivalents": [_equiv_to_legacy(e) for e in db_equivalents],
        })

    return {
        "source_provider": ctx.source_provider,
        "billing_period": ctx.billing_period,
        "currency": ctx.currency,
        "total_spend": float(ctx.total_spend),
        "company_context": {
            "company_size": "unknown",
            "industry": "",
            "regulated_environment": False,
            "critical_workloads": False,
            "global_operations": False,
            "high_availability_required": False,
            "disaster_recovery_required": False,
            "security_sensitive": False,
            "cloud_maturity": "unknown",
            "migration_goal": "unknown",
        },
        "workloads": workloads,
    }


def _equiv_to_legacy(e) -> dict:
    tm = getattr(e, "technical_match", None)
    fin = getattr(e, "financials", None)
    return {
        "provider": e.provider,
        "service_name": e.service_name,
        "technical_match": {
            "vcpus": float(tm.vcpus) if tm and tm.vcpus is not None else None,
            "ram_gb": float(tm.ram_gb) if tm and tm.ram_gb is not None else None,
        } if tm else None,
        "financials": {
            "estimated_monthly_cost": float(fin.estimated_monthly_cost)
            if fin and fin.estimated_monthly_cost is not None else None,
            "cost_delta": float(fin.cost_delta)
            if fin and fin.cost_delta is not None else None,
            "pricing_confidence": fin.pricing_confidence if fin else "low",
        } if fin else None,
        "metadata": getattr(e, "metadata", None),
    }


class RunAnalysisPipelineUseCase:
    """Orquestra a pipeline de análise em background.

    1. Marca o job como PROCESSING.
    2. Executa BuildMigrationContextUseCase para gerar o contexto de migração.
    3. Envia contexto + prompt para o LLMOrchestrator.
    4. Persiste o resultado (structured + text) como COMPLETED ou FAILED.
    """

    def __init__(
        self,
        analysis_job_repo: IAnalysisJobRepository,
        report_repo: IBillingReportRepository,
        item_repo: IBillingEnrichedRepository,
        metadata_repo: PGServiceMetadataRepository,
        comparison_repo: PGServiceComparisonRepository,
        sku_repo: ICloudSkuRepository,
        llm_orchestrator: LLMOrchestrator,
    ) -> None:
        self._job_repo = analysis_job_repo
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo
        self._sku_repo = sku_repo
        self._orchestrator = llm_orchestrator

    async def execute(self, job: AnalysisJob) -> None:
        job.mark_processing()
        await self._job_repo.update(job)

        try:
            context_uc = BuildMigrationContextUseCase(
                self._report_repo,
                self._item_repo,
                self._metadata_repo,
                self._comparison_repo,
                sku_repo=self._sku_repo,
            )

            ctx = await context_uc.execute(
                BuildMigrationContextInput(
                    user_id=job.user_id,
                    report_id=job.report_id,
                )
            )

            pools_json, missing_equiv_ids = _serialize_pools(ctx)
            # Capture payload for persistence before sending to LLM
            llm_input, _ = build_pipeline_llm_input_payload(ctx)

            ui_report = await self._orchestrator.generate_report(
                context=ctx,
                source_provider=ctx.source_provider,
                billing_period=ctx.billing_period,
                total_spend=float(ctx.total_spend),
                currency=ctx.currency,
                pools_json=pools_json,
            )

            if not isinstance(ui_report, AnalysisV2Response):
                ui_report = AnalysisV2Response.resilient_parse(
                    ui_report.model_dump() if hasattr(ui_report, "model_dump") else {}
                )

            flags = ui_report.flags
            if ctx.missing_sku_ids:
                flags.setdefault("catalog", []).extend(
                    {"code": "MISSING_SKU_DEFINITION",
                     "message": f"SKU '{sku_id}' without vCPU/RAM definition in catalog",
                     "impact_area": "catalog"}
                    for sku_id in ctx.missing_sku_ids
                )
            if missing_equiv_ids:
                flags.setdefault("equivalents", []).extend(
                    {"code": "MISSING_EQUIVALENT",
                     "message": f"Pool '{pool_id}' has no cross-cloud equivalent in catalog",
                     "impact_area": "catalog"}
                    for pool_id in missing_equiv_ids
                )

            llm_output = (
                f"Viability score: {ui_report.viability_score}/1000. "
                f"Recommendation: {ui_report.recommendation}. "
                f"Recommended provider: {ui_report.recommended_provider}. "
                f"Rationale: {ui_report.recommendation_rationale}. "
                f"Monthly savings: ${ui_report.savings_summary.total_monthly_savings_usd:.2f}. "
                f"Reduction: {ui_report.savings_summary.percentage_reduction}%."
            )

            job.mark_completed(
                output=llm_output,
                structured=ui_report.model_dump(),
                llm_input=llm_input,
            )
        except Exception as exc:
            job.mark_failed(str(exc))

        await self._job_repo.update(job)
