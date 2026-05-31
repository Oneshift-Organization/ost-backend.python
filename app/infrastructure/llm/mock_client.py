"""Mock LLM client — returns AnalysisV2Response from real context data.

No network calls. Parses the serialized billing data from the prompt and derives
scores, savings and recommendation from actual equivalents/cost_deltas.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.schemas.analysis_v2 import (
    AgentViews,
    AnalysisV2Response,
    CloudEngineeringView,
    CrossCloudEquivalent,
    FinOpsView,
    OneshiftScores,
    ProviderComparison,
    RiskMigrationView,
    SavingsSummaryV2,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


class MockLLMClient:
    """Derives a realistic V2 analysis from the billing/pool data in the prompt."""

    async def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        payload = _parse_prompt(prompt)
        billing = payload.get("billing_data", payload)  # handles both direct and wrapped

        pools = billing.get("compute_pools", [])
        source_provider = billing.get("source_provider", "unknown")
        total_spend = _dec(billing.get("total_spend", 0))

        # ── Aggregate savings per provider from pool equivalents ──────────
        savings_by_provider: dict[str, Decimal] = {}
        cost_by_provider: dict[str, Decimal] = {}
        cross_cloud: list[CrossCloudEquivalent] = []

        total_vcpus = Decimal("0")
        total_ram = Decimal("0")

        for pool in pools:
            cap = pool.get("capacity", {})
            total_vcpus += _dec(cap.get("vcpus"))
            total_ram += _dec(cap.get("ram_gib"))
            workload_name = (
                f"{pool.get('project_id','?')}/{pool.get('family','?')}/{pool.get('region','?')}"
            )

            for eq in pool.get("equivalents", []):
                prov = str(eq.get("provider", "")).upper()
                fin = eq.get("financials", {})
                delta = _dec(fin.get("cost_delta"))
                est = _dec(fin.get("estimated_monthly_cost"))
                savings_by_provider[prov] = savings_by_provider.get(prov, Decimal("0")) + delta
                cost_by_provider[prov] = cost_by_provider.get(prov, Decimal("0")) + est

                cross_cloud.append(
                    CrossCloudEquivalent(
                        workload=workload_name,
                        source_provider=source_provider,
                        source_service=pool.get("family", ""),
                        target_provider=prov.lower(),
                        target_service=str(eq.get("service_name", "")),
                        equivalence_type="hardware_match"
                        if eq.get("match_basis") == "pool_capacity"
                        else "curated_only",
                        estimated_monthly_cost=float(est),
                        cost_delta=float(delta),
                        portability="high",
                        migration_risk="low",
                        requires_refactoring=False,
                        vendor_lock_in_level="low",
                    )
                )

        # Also handle legacy workloads format
        for wl in billing.get("workloads", []):
            for eq in wl.get("equivalents", []):
                prov = str(eq.get("provider", "")).upper()
                fin = eq.get("financials") or {}
                delta = _dec(fin.get("cost_delta"))
                est = _dec(fin.get("estimated_monthly_cost"))
                savings_by_provider[prov] = savings_by_provider.get(prov, Decimal("0")) + delta
                cost_by_provider[prov] = cost_by_provider.get(prov, Decimal("0")) + est

        # ── Best provider ─────────────────────────────────────────────────
        best_provider, best_delta = "", Decimal("0")
        if savings_by_provider:
            best_provider, best_delta = min(
                savings_by_provider.items(), key=lambda kv: kv[1]
            )

        total_savings = -best_delta
        pct = float(total_savings / total_spend * 100) if total_spend > 0 else 0.0
        viability = min(1000, max(0, int(400 + pct * 10 + len(pools) * 5)))

        # ── Provider comparison ───────────────────────────────────────────
        provider_comparison: list[ProviderComparison] = []
        for prov, delta in sorted(savings_by_provider.items(), key=lambda kv: kv[1]):
            savings = float(-delta)
            est_cost = float(cost_by_provider.get(prov, Decimal("0")))
            fin_score = min(100, max(0, int(50 + savings / max(1, float(total_spend)) * 100)))
            provider_comparison.append(
                ProviderComparison(
                    provider=prov,
                    financial_score=fin_score,
                    technical_score=75,
                    enterprise_score=65,
                    overall_score=int((fin_score + 75 + 65) / 3),
                    estimated_monthly_cost=est_cost,
                    estimated_monthly_savings=savings,
                    migration_complexity="medium",
                    portability="high",
                    confidence_level="low",
                    strengths=[f"Maior economia: ${savings:,.0f}/mês"]
                    if savings > 0
                    else [],
                    weaknesses=["Preços baseados em catálogo público on-demand"],
                )
            )

        # ── Scores ───────────────────────────────────────────────────────
        fin_score = min(100, max(0, int(50 + pct * 2)))
        oneshift_scores = OneshiftScores(
            financial_score=fin_score,
            technical_score=75,
            enterprise_score=65,
            overall_score=int((fin_score + 75 + 65) / 3),
        )

        # ── Recommendation ────────────────────────────────────────────────
        if best_provider and total_savings > 0:
            recommendation = "MIGRATE"
            rationale = (
                f"{best_provider} apresenta a maior economia agregada "
                f"(${float(total_savings):,.0f}/mês, {pct:.0f}%) baseada em preços públicos "
                f"on-demand. Workloads: {len(pools)} pools, {float(total_vcpus):.0f} vCPUs / "
                f"{float(total_ram):.0f} GiB."
            )
        else:
            recommendation = "STAY"
            best_provider = ""
            rationale = "Nenhum equivalente com economia projetada encontrado no catálogo."

        provider_summary = "; ".join(
            f"{p}: ${float(-d):,.0f}"
            for p, d in sorted(savings_by_provider.items(), key=lambda kv: kv[1])
            if d < 0
        )

        return AnalysisV2Response(
            viability_score=viability,
            oneshift_scores=oneshift_scores,
            provider_comparison=provider_comparison,
            cross_cloud_equivalents=cross_cloud[:20],
            recommended_provider=best_provider.upper(),
            recommendation=recommendation,
            recommendation_rationale=rationale,
            analysis_confidence="low",
            agent_views=AgentViews(
                finops=FinOpsView(
                    summary=f"Economia potencial: {provider_summary or 'sem dados'}.",
                    confidence_level="low",
                    key_findings=[
                        f"Total spend: ${float(total_spend):,.2f}",
                        f"Economia projetada: ${float(total_savings):,.2f}/mês ({pct:.0f}%)",
                    ],
                    financial_flags=["Preços on-demand; reservas podem ampliar economia"],
                ),
                cloud_engineering=CloudEngineeringView(
                    summary=f"{len(pools)} pools identificados com equivalentes catalogados.",
                    confidence_level="low",
                    migration_complexity="medium",
                    technical_flags=[],
                    technical_recommendations=[
                        "Validar sizing com utilização real (pico vs. média).",
                        "Avaliar compatibilidade de SO e licenças.",
                    ],
                ),
                risk_migration=RiskMigrationView(
                    summary="Migração viável com risco controlado.",
                    confidence_level="low",
                    risk_flags=["pricing_confidence=low (catálogo público)"],
                    blocked_or_uncertain_items=[
                        "Custos auxiliares (GKE, egress, storage) sem equivalentes diretos."
                    ],
                    recommended_migration_sequence=[
                        "1. PoC com workload não-crítica.",
                        "2. Validar custo por 30 dias.",
                        "3. Migrar em lotes por projeto.",
                    ],
                ),
            ),
            savings_summary=SavingsSummaryV2(
                total_monthly_savings_usd=round(float(total_savings), 2),
                percentage_reduction=round(pct, 1),
                payback_period_months=6 if total_savings > 0 else None,
                confidence_level="low",
            ),
            flags={},
            strategic_notes=[
                f"Pools analisados: {len(pools)} ({float(total_vcpus):.0f} vCPUs).",
                "Preços on-demand públicos. Reservas/CUDs podem ampliar a economia.",
                "Custos auxiliares (GKE, egress, storage) requerem equivalência separada.",
            ],
            action_plan=[
                "Validar sizing com métricas de utilização real.",
                "Solicitar cotação de instâncias reservadas.",
                "Executar PoC com workload não-crítica.",
                "Monitorar custo por 30 dias pós-migração.",
            ],
        )


def _parse_prompt(prompt: str) -> dict:
    try:
        return json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return {}


def _dec(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal("0")
