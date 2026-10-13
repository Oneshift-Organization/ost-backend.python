"""Deterministic score ledger. Missing evidence forms bounds, never an invented grade.

Policy v3: financial 50%, technical 50%. Governance declarations only block a
destination; they are not a score axis. Bounds are arithmetic
limits from unknown factors, NOT statistical confidence intervals. No I/O/LLM.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.domain.services.migration_strategy import (
    classify_workload_strategy,
    normalized,
    technical_evidence,
)
from app.domain.services.service_identity import is_financial_component
from app.schemas.decision_support import (
    DecisionContext,
    DecisionSupport,
    ScenarioScore,
    ScoreAxis,
    ScoreFactor,
)

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)
_MIN_COVERAGE = Decimal("0.5")
_NON_WORKLOAD = frozenset(
    {
        "tax",
        "taxes",
        "imposto",
        "impostos",
        "commitment",
        "discount",
        "credits",
        "compromisso",
        "support",
        "unclassified",
        "unknown",
        "nao_classificado",
    }
)
_LOCK_SCORE = {"low": 100, "medium": 50, "high": 0}


def number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(val) for key, val in sorted(value.items())}
    if isinstance(value, list):
        return sorted((_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


# Categorias sem classificação: fora do score, mas são gasto real (não financeiro).
UNCLASSIFIED = frozenset({"unclassified", "unknown", "nao_classificado"})


def all_workloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every service in the snapshot, before the score's filter."""
    if "decision_workloads" in payload:
        return list(payload["decision_workloads"])
    return [
        {
            "name": w.get("service_name") or w.get("canonical_service"),
            "category": w.get("category"),
            "cost": (w.get("source_usage") or {}).get("billed_cost"),
            "migration": {},
            "targets": w.get("equivalents") or [],
            "evidence_source": "provided_workload_data",
        }
        for w in payload.get("workloads") or []
    ]


def is_scored_workload(workload: dict[str, Any]) -> bool:
    """What the technical axis reads: priced, classified, not a financial line."""
    return (
        normalized(workload.get("category")) not in _NON_WORKLOAD
        and not is_financial_component(str(workload.get("name") or ""))
        and (number(workload.get("cost")) or _ZERO) > 0
    )


def decision_workloads(payload: dict) -> list[dict]:
    """Accept server snapshots and the existing direct-workload contract."""
    return _canonical([w for w in all_workloads(payload) if is_scored_workload(w)])


def unique_targets(workload: dict) -> dict[str, dict]:
    """The one mapping per provider the technical axis reads; ambiguous ones drop."""
    grouped: dict[str, list[dict]] = {}
    for target in workload.get("targets") or []:
        key = normalized(target.get("provider"))
        if key in {"aws", "azure", "gcp", "oci"}:
            grouped.setdefault(key, []).append(target)
    # Ambiguous matches are not resolved by input ordering or cheapest invented shape.
    return {key: values[0] for key, values in grouped.items() if len(values) == 1}


def _factor(
    factor_id: str,
    label: str,
    weight: str,
    rule: str,
    samples: list[tuple[Decimal, Decimal | None, dict]],
) -> ScoreFactor:
    total = sum((w for w, _, _ in samples), _ZERO)
    known = sum((w for w, v, _ in samples if v is not None), _ZERO)
    coverage = known / total if total > 0 else _ZERO
    lower = sum((w * v for w, v, _ in samples if v is not None), _ZERO)
    lower = lower / total if total > 0 else _ZERO
    upper = lower + _HUNDRED * (_ONE - coverage)
    return ScoreFactor(
        id=factor_id,
        label=label,
        weight=float(weight),
        value=rounded(lower) if coverage == 1 else None,
        coverage=float(coverage),
        lower_bound=rounded(lower),
        upper_bound=rounded(upper),
        contribution=rounded(lower * Decimal(weight)),
        rule=rule,
        evidence=[entry for _, _, entry in samples],
    )


def _axis(axis_id: str, label: str, weight: str, factors: list[ScoreFactor]) -> ScoreAxis:
    lower = sum((Decimal(str(f.lower_bound)) * Decimal(str(f.weight)) for f in factors), _ZERO)
    upper = sum((Decimal(str(f.upper_bound)) * Decimal(str(f.weight)) for f in factors), _ZERO)
    coverage = sum((Decimal(str(f.coverage)) * Decimal(str(f.weight)) for f in factors), _ZERO)
    complete = all(f.value is not None for f in factors)
    return ScoreAxis(
        id=axis_id,
        label=label,
        weight=float(weight),
        value=rounded(lower) if complete else None,
        coverage=float(coverage),
        lower_bound=rounded(lower),
        upper_bound=rounded(upper),
        contribution=rounded(lower * Decimal(weight)),
        factors=factors,
    )


def _financial(row: dict, current: Decimal | None) -> ScoreAxis:
    saving = number(row.get("economia_mensal"))
    delta = None
    if current is not None and current > 0 and saving is not None:
        delta = max(_ZERO, min(_HUNDRED, Decimal(50) + _HUNDRED * saving / current))
    coverage = number(row.get("cobertura"))
    coverage = coverage if coverage is not None and 0 <= coverage <= 1 else None
    return _axis(
        "financial",
        "Financeiro",
        "0.5",
        [
            _factor(
                "savings",
                "Economia recorrente",
                "0.7",
                "clamp(50 + 100 * economia/custo, 0, 100)",
                [
                    (
                        _ONE,
                        delta,
                        {
                            "source": "resumo_executivo",
                            "current_cost": str(current),
                            "monthly_savings": str(saving),
                        },
                    ),
                ],
            ),
            _factor(
                "price_coverage",
                "Cobertura da comparação de custos",
                "0.3",
                "100 * cobertura",
                [
                    (
                        _ONE,
                        coverage * _HUNDRED if coverage is not None else None,
                        {"source": "resumo_executivo.cobertura", "value": str(coverage)},
                    ),
                ],
            ),
        ],
    )


def _technical(workloads: list[dict], provider: str) -> ScoreAxis:
    refactors: list = []
    locks: list = []
    for workload in workloads:
        target = unique_targets(workload).get(provider)
        refactor, lock = technical_evidence(workload, target) if target else (None, None)
        cost = number(workload.get("cost")) or _ZERO
        evidence = {
            "workload": workload.get("name"),
            "cost_weight": str(cost),
            "source": workload.get("evidence_source", "catalog_snapshot"),
        }
        refactors.append(
            (
                cost,
                (Decimal(30) if refactor else _HUNDRED) if refactor is not None else None,
                {**evidence, "requires_refactoring": refactor},
            )
        )
        locks.append(
            (
                cost,
                Decimal(_LOCK_SCORE[lock]) if lock in _LOCK_SCORE else None,
                {**evidence, "lock_in": lock},
            )
        )
    return _axis(
        "technical",
        "Técnico",
        "0.5",
        [
            _factor(
                "refactoring",
                "Alteração da aplicação",
                "0.6",
                "refatoração: 30; sem: 100",
                refactors,
            ),
            _factor(
                "lock_in",
                "Portabilidade do catálogo",
                "0.4",
                "lock-in baixo: 100; médio: 50; alto: 0",
                locks,
            ),
        ],
    )


def _scenario(
    row: dict, current: Decimal | None, workloads: list[dict], evidence: dict
) -> ScenarioScore:
    provider = normalized(row.get("provedor"))
    axes = [_financial(row, current), _technical(workloads, provider)]
    lower = sum((Decimal(str(a.lower_bound)) * Decimal(str(a.weight)) for a in axes), _ZERO)
    upper = sum((Decimal(str(a.upper_bound)) * Decimal(str(a.weight)) for a in axes), _ZERO)
    coverage = sum((Decimal(str(a.coverage)) * Decimal(str(a.weight)) for a in axes), _ZERO)
    complete = all(a.value is not None for a in axes)
    blockers = [
        f"{key}: requisito obrigatório não atendido"
        for key in ("region_approved", "compliance_approved")
        if evidence.get(key) is False
    ]
    saving = number(row.get("economia_mensal"))
    cost_coverage = number(row.get("cobertura"))
    if saving is None or saving <= 0:
        blockers.append("Sem economia recorrente positiva comprovada.")
    if cost_coverage is None or cost_coverage < _MIN_COVERAGE:
        blockers.append("Cobertura de preços inferior a 50% ou desconhecida.")
    return ScenarioScore(
        provider=provider,
        status="complete" if complete else ("partial" if coverage else "not_scored"),
        value=rounded(lower) if complete else None,
        lower_bound=rounded(lower),
        upper_bound=rounded(upper),
        coverage=float(coverage),
        axes=axes,
        eligible=not blockers,
        blockers=blockers,
    )


def build_decision_support(payload: dict) -> DecisionSupport:
    """Compute from the saved input, ignoring any supplied decision_support/LLM output."""
    workloads = decision_workloads(payload)
    context = DecisionContext.model_validate(payload.get("decision_context") or {})
    resumo = payload.get("resumo_executivo") or {}
    rows = _canonical(resumo.get("por_provedor") or [])
    current = number(resumo.get("custo_mensal_atual"))
    governance = {g.provider: g.model_dump(exclude_none=True) for g in context.governance}
    declarations = {
        (e.provider, normalized(e.workload)): e.model_dump(exclude_none=True)
        for e in context.workloads
    }
    strategies = []
    scores = []
    known_names = {normalized(w.get("name")) for w in workloads}
    for row in rows:
        provider = normalized(row.get("provedor"))
        score = _scenario(row, current, workloads, governance.get(provider, {}))
        for declared_provider, declared_name in sorted(declarations):
            if declared_provider == provider and declared_name not in known_names:
                score.blockers.append(
                    f"Componente declarado não encontrado: {declared_name}; revisar identificação."
                )
        for workload in workloads:
            strategy = classify_workload_strategy(
                workload,
                provider,
                declarations.get((provider, normalized(workload.get("name")))),
            )
            strategies.append(strategy)
            if strategy.strategy in {"retain", "retire", "repurchase", "relocate"}:
                score.blockers.append(
                    f"{strategy.workload}: {strategy.strategy} exige cenário de custos específico."
                )
            if strategy.rule == "7r.conflicting_declarations":
                score.blockers.append(f"{strategy.workload}: declarações conflitantes.")
        score.eligible = not score.blockers
        scores.append(score)

    eligible = [s for s in scores if s.eligible]
    by_provider = {normalized(r.get("provedor")): r for r in rows}
    # Never rank partial scores against complete scores/other evidence masks.
    all_complete = bool(eligible) and all(s.status == "complete" for s in eligible)
    eligible.sort(
        key=lambda s: (
            -(s.value or 0) if all_complete else _ZERO,
            -(number(by_provider[s.provider].get("economia_mensal")) or _ZERO),
            s.provider,
        )
    )
    selected = eligible[0] if eligible else None
    reason = (
        (
            "Maior score completo entre cenários elegíveis; desempate por economia e provedor."
            if all_complete
            else "Comparação econômica preliminar entre destinos elegíveis; "
            "faltam dados para score completo."
        )
        if selected
        else "Nenhum destino elegível; manter provisoriamente e revisar as pendências."
    )
    selected_strategies = [s for s in strategies if selected and s.provider == selected.provider]
    rs = {s.strategy for s in selected_strategies if s.strategy}
    summary = (
        next(iter(rs))
        if len(rs) == 1 and all(s.strategy for s in selected_strategies)
        else ("mixed" if rs else "undetermined")
    )
    confidence = str(
        (payload.get("premissas") or {}).get("confianca_analise", {}).get("nivel", "low")
    )
    # Hash only decision inputs, not timestamps, UUIDs, narrative or logging metadata.
    snapshot = {
        "policy": "score-7rs-v3",
        "workloads": workloads,
        "rows": rows,
        "current": str(current),
        "context": context.model_dump(exclude_none=True),
        "source": normalized(payload.get("provedor_origem") or payload.get("source_provider")),
        "currency": payload.get("moeda") or payload.get("currency"),
        "confidence": confidence,
    }
    digest = hashlib.sha256(
        json.dumps(
            _canonical(snapshot), sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()
    return DecisionSupport(
        input_fingerprint=digest,
        selected_provider=selected.provider if selected else None,
        selection_reason=reason,
        score=selected,
        provider_scores=scores,
        workload_strategies=strategies,
        strategy_summary=summary,
        confidence=confidence,
        limitations=[
            "Política de regras v3 (50/50), não modelo treinado nem probabilidade de sucesso.",
            "Limites representam fatores desconhecidos, não intervalo estatístico.",
            "Billing não comprova dependências, topologia, compliance ou autorização para migrar.",
            "Economia recorrente não inclui todo o investimento de migração "
            "ou créditos não comprovados.",
            "7 Rs do catálogo são candidatos por componente; declarações são do responsável.",
        ],
    )
