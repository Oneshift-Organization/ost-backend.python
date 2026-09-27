"""Attach deterministic decisions before inference and impose them after inference."""

from __future__ import annotations

from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal

from app.domain.services.decision_support import build_decision_support, number
from app.schemas.analysis_v2 import AnalysisV2Response
from app.schemas.decision_support import DecisionSupport
from app.use_cases.analysis.backend_authority import apply_backend_authority
from app.use_cases.analysis.topologia_no_payload import impor_topologia


def _legacy_summary(payload: dict) -> dict:
    """Direct callers supply their own priced scenarios; do not invent absent prices."""
    current = number(payload.get("total_spend"))
    if current is None or current <= 0:
        return {}
    tallies: dict[str, list[Decimal]] = {}
    for workload in payload.get("workloads") or []:
        source = workload.get("source_usage") or {}
        cost = number(source.get("billed_cost"))
        if (
            cost is None
            or cost <= 0
            or source.get("currency", payload.get("currency")) != payload.get("currency")
        ):
            continue
        targets = workload.get("equivalents") or []
        for target in targets:
            provider = str(target.get("provider") or "").lower()
            if provider not in {"aws", "azure", "gcp", "oci"}:
                continue
            if sum(str(t.get("provider", "")).lower() == provider for t in targets) != 1:
                continue
            price = number((target.get("financials") or {}).get("estimated_monthly_cost"))
            if price is None or price < 0:
                continue
            totals = tallies.setdefault(provider, [Decimal(0), Decimal(0)])
            totals[0] += cost
            totals[1] += price
    rows = []
    for provider, (covered, price) in sorted(tallies.items()):
        if covered > current:
            continue  # inconsistent/double-counted source costs cannot support a scenario
        rows.append(
            {
                "provedor": provider,
                "custo_estimado_total": float(current - covered + price),
                "economia_mensal": float(covered - price),
                "cobertura": float(covered / current),
                "confianca": "low",
            }
        )
    return {"custo_mensal_atual": float(current), "por_provedor": rows, "recomendado": None}


def prepare_decision_payload(payload: dict) -> dict:
    result = deepcopy(payload)
    if not result.get("resumo_executivo") and "workloads" in result:
        result["resumo_executivo"] = _legacy_summary(result)
    decision = build_decision_support(result)
    result["decision_support"] = decision.model_dump(mode="json")
    if result.get("resumo_executivo"):
        result["resumo_executivo"]["recomendado"] = decision.selected_provider
        result["resumo_executivo"]["justificativa"] = decision.selection_reason
    return result


def _legacy_int(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def impose_decision(response: AnalysisV2Response, payload: dict) -> AnalysisV2Response:
    """The response's decision_support is untrusted. Recompute from the input snapshot."""
    prepared = prepare_decision_payload(payload)
    decision = DecisionSupport.model_validate(prepared["decision_support"])
    response = apply_backend_authority(response, prepared.get("resumo_executivo"))
    scores = {s.provider: s for s in decision.provider_scores}
    providers = []
    for pc in response.provider_comparison:
        score = scores.get(pc.provider.lower())
        if score is None:
            continue
        axes = {a.id: a for a in score.axes}
        providers.append(
            pc.model_copy(
                update={
                    **{f"{key}_score": _legacy_int(axis.lower_bound) for key, axis in axes.items()},
                    "overall_score": _legacy_int(score.lower_bound),
                }
            )
        )
    selected = decision.score
    numeric = {f"{key}_score": 0 for key in ("financial", "technical", "enterprise", "overall")}
    if selected:
        numeric.update({f"{a.id}_score": _legacy_int(a.lower_bound) for a in selected.axes})
        numeric["overall_score"] = _legacy_int(selected.lower_bound)
    # Legacy integers remain parseable. New clients MUST use status/value/bounds;
    # the lower bound is NOT an imputed value for missing evidence.
    update = {
        "oneshift_scores": response.oneshift_scores.model_copy(update=numeric),
        "viability_score": numeric["overall_score"] * 10,
        "provider_comparison": providers,
        "recommended_provider": (decision.selected_provider or "").upper(),
        "decision_support": decision,
        "analysis_confidence": decision.confidence,
    }
    if selected is None:
        update["savings_summary"] = response.savings_summary.model_copy(
            update={
                "total_monthly_savings_usd": 0.0,
                "percentage_reduction": 0.0,
                "payback_period_months": None,
            }
        )
    if decision.selected_provider:
        update["recommendation"] = (
            f"Avaliar {decision.selected_provider.upper()}. {decision.selection_reason}"
        )
    else:
        update["recommendation"] = decision.selection_reason
    # A ordem de migração também é do backend quando há inventário (PLANO_INVENTARIO_API RI14).
    return impor_topologia(response.model_copy(update=update), payload)
