"""Overlay determinístico pós-LLM (relatorio.md A3).

O LLM produz texto qualitativo; TODOS os agregados financeiros exibidos ao
usuário são recalculados aqui a partir do mesmo payload persistido em
``llm_input_payload``. Divergências viram flags — nunca passam silenciosas.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.analysis_v2 import AnalysisV2Response

_TOLERANCE_PCT = Decimal("0.01")


def apply_financial_overlay(
    response: AnalysisV2Response, payload: dict[str, Any]
) -> AnalysisV2Response:
    savings_by, cost_by = _aggregate_by_provider(payload)
    total_spend = _dec(payload.get("gasto_total", payload.get("total_spend", 0)))
    divergences: list[str] = []

    # 2. provider_comparison
    for pc in response.provider_comparison:
        prov = pc.provider.upper()
        if prov in cost_by:
            det_cost = cost_by[prov]
            det_sav = -savings_by[prov]
            _check(divergences, prov, "estimated_monthly_cost", pc.estimated_monthly_cost, det_cost)
            pc.estimated_monthly_cost = float(round(det_cost, 2))
            pc.estimated_monthly_savings = float(round(det_sav, 2))

        # 4. clamp de sub-scores por provedor (mesma regra do OneshiftScores)
        scope = f"provider_comparison[{pc.provider}]"
        pc.financial_score = _clamp(divergences, f"{scope}.financial_score", pc.financial_score)
        pc.technical_score = _clamp(divergences, f"{scope}.technical_score", pc.technical_score)
        pc.enterprise_score = _clamp(divergences, f"{scope}.enterprise_score", pc.enterprise_score)
        pc.overall_score = _clamp(divergences, f"{scope}.overall_score", pc.overall_score)

    # 5. recommended_provider deve existir no payload
    rec = response.recommended_provider.upper()
    if rec and rec not in savings_by and rec != "INCONCLUSIVE":
        divergences.append(
            f"recommended_provider '{response.recommended_provider}' ausente do payload"
        )
        response.recommended_provider = "inconclusive"
        rec = ""

    # 1. savings_summary — do provider recomendado, senão o melhor
    if rec and rec in savings_by:
        total_savings = -savings_by[rec]
    elif savings_by:
        _, best_delta = min(savings_by.items(), key=lambda kv: kv[1])
        total_savings = -best_delta
    else:
        total_savings = Decimal("0")
    _check(
        divergences,
        "savings_summary",
        "total_monthly_savings_usd",
        response.savings_summary.total_monthly_savings_usd,
        total_savings,
    )
    response.savings_summary.total_monthly_savings_usd = float(round(total_savings, 2))
    response.savings_summary.percentage_reduction = (
        float(round(total_savings / total_spend * 100, 1)) if total_spend > 0 else 0.0
    )
    response.savings_summary.payback_period_months = None  # 3. sem input p/ calcular

    # 4. clamp de scores
    response.viability_score = _clamp(divergences, "viability_score", response.viability_score)
    s = response.oneshift_scores
    s.financial_score = _clamp(divergences, "financial_score", s.financial_score)
    s.technical_score = _clamp(divergences, "technical_score", s.technical_score)
    s.enterprise_score = _clamp(divergences, "enterprise_score", s.enterprise_score)
    s.overall_score = _clamp(divergences, "overall_score", s.overall_score)

    # 6. divergências viram flags
    if divergences:
        response.flags.setdefault("numeric_validation", []).extend(
            {"code": "LLM_NUMERIC_DIVERGENCE", "message": m, "impact_area": "financials"}
            for m in divergences
        )
    return response


def _aggregate_by_provider(
    payload: dict[str, Any],
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    savings: dict[str, Decimal] = {}
    costs: dict[str, Decimal] = {}

    def _consume(equivs: list[dict[str, Any]]) -> None:
        for eq in equivs:
            prov = str(eq.get("provedor", eq.get("provider", ""))).upper()
            fin = eq.get("financeiro", eq.get("financials")) or {}
            if not prov:
                continue
            savings[prov] = savings.get(prov, Decimal("0")) + _dec(
                fin.get("delta_custo", fin.get("cost_delta"))
            )
            costs[prov] = costs.get(prov, Decimal("0")) + _dec(
                fin.get("custo_mensal_estimado", fin.get("estimated_monthly_cost"))
            )

    for pool in payload.get("pools_computacao", []):
        _consume(pool.get("equivalentes", []))
    aux = payload.get("custos_auxiliares", {})
    _consume(aux.get("equivalentes_banco_dados", []))
    for wl in payload.get("workloads", []):  # formato legacy (/direct)
        _consume(wl.get("equivalents", []))
    return savings, costs


def _check(out: list[str], scope: str, field: str, llm_value: float, det_value: Decimal) -> None:
    tolerance = max(Decimal("1"), abs(det_value) * _TOLERANCE_PCT)
    if abs(_dec(llm_value) - det_value) > tolerance:
        out.append(f"{scope}.{field}: LLM={llm_value} determinístico={det_value:.2f}")


def _clamp(out: list[str], field: str, value: int) -> int:
    clamped = min(100, max(0, int(value)))
    if clamped != value:
        out.append(f"{field}: {value} fora de 0-100, ajustado para {clamped}")
    return clamped


def _dec(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal("0")
