from __future__ import annotations

import json
from dataclasses import dataclass
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
from app.schemas.analysis_ui import Flag
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)


@dataclass(frozen=True)
class RunAnalysisPipelineInput:
    user_id: UUID
    report_id: UUID


def _serialize_pools(ctx) -> str:
    """Serialize capacity pools into a compact prompt payload."""
    pools = getattr(ctx, "pools", ())
    auxiliary = getattr(ctx, "auxiliary", None)

    payload: dict[str, object] = {
        "compute_pools": [
            {
                "project_id": p.pool_key.project_id,
                "family": p.pool_key.machine_family,
                "region": p.pool_key.region,
                "capacity": {
                    "vcpus": round(float(p.capacity.vcpus), 2),
                    "ram_gib": round(float(p.capacity.ram_gib), 2),
                },
                "costs": {
                    "compute_base": round(float(p.costs.compute_base), 2),
                    "os_licensing": round(float(p.costs.os_licensing), 2),
                },
            }
            for p in pools
            if p.capacity.vcpus > 0 or p.capacity.ram_gib > 0
        ],
    }

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
        if auxiliary.orphan > 0:
            aux_payload["unmapped"] = {
                "cost": round(float(auxiliary.orphan), 2),
                "skus": list(auxiliary.orphan_skus),
            }
        payload["auxiliary_costs"] = aux_payload

    return json.dumps(payload, default=str)


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

            pools_json = _serialize_pools(ctx)

            ui_report = await self._orchestrator.generate_report(
                context=ctx,
                source_provider=ctx.source_provider,
                billing_period=ctx.billing_period,
                total_spend=float(ctx.total_spend),
                currency=ctx.currency,
                pools_json=pools_json,
            )

            if ctx.missing_sku_ids:
                catalog_flags = ui_report.flags.setdefault("catalog", [])
                for sku_id in ctx.missing_sku_ids:
                    catalog_flags.append(
                        Flag(
                            code="MISSING_SKU_DEFINITION",
                            message=f"SKU '{sku_id}' without vCPU/RAM definition in catalog; no equivalents computed",
                            impact_area="catalog",
                        )
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
            )
        except Exception as exc:
            job.mark_failed(str(exc))

        await self._job_repo.update(job)
