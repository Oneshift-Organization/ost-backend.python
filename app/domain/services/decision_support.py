"""Deterministic score ledger. Missing evidence forms bounds, never an invented grade.

Policy v4.2: financial 35%, technical 50%, evidence coverage 15%.
Governance declarations only block a
destination; they are not a score axis. Bounds are arithmetic
limits from unknown factors, NOT statistical confidence intervals. No I/O/LLM.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.domain.services.corporate_benefits import build_corporate_benefits
from app.domain.services.migration_strategy import (
    classify_workload_strategy,
    normalized,
)
from app.domain.services.service_identity import is_financial_component
from app.domain.services.technical_assessment import CURATED, assess, fraction, inventory_services
from app.schemas.decision_support import (
    DECISION_POLICY_VERSION,
    DecisionComparison,
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
_MIN_TECHNICAL_FACTORS = 2
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
    """Legacy per-destination mapping; ambiguous targets cannot select a service."""
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
    known = sum((w * fraction(e.get("coverage", 1)) for w, v, e in samples if v is not None), _ZERO)
    coverage = known / total if total > 0 else _ZERO
    lower = sum(
        (w * v * fraction(e.get("coverage", 1)) for w, v, e in samples if v is not None), _ZERO
    )
    lower = lower / total if total > 0 else _ZERO
    upper = lower + _HUNDRED * (_ONE - coverage)
    return ScoreFactor(
        id=factor_id,
        label=label,
        weight=float(weight),
        value=rounded(lower / coverage) if coverage else None,
        coverage=float(coverage),
        lower_bound=rounded(lower),
        upper_bound=rounded(upper),
        contribution=rounded(lower * Decimal(weight)),
        rule=rule,
        evidence=[entry for _, _, entry in samples],
        provisional=any(e.get("hypothesis") for _, v, e in samples if v is not None),
    )


def _axis(axis_id: str, label: str, weight: str, factors: list[ScoreFactor]) -> ScoreAxis:
    lower = sum((Decimal(str(f.lower_bound)) * Decimal(str(f.weight)) for f in factors), _ZERO)
    upper = sum((Decimal(str(f.upper_bound)) * Decimal(str(f.weight)) for f in factors), _ZERO)
    coverage = sum((Decimal(str(f.coverage)) * Decimal(str(f.weight)) for f in factors), _ZERO)
    observed = sum(
        (
            Decimal(str(f.value or 0)) * Decimal(str(f.weight)) * Decimal(str(f.coverage))
            for f in factors
        ),
        _ZERO,
    )
    value = observed / coverage if coverage else None
    for factor in factors:
        factor.contribution = (
            rounded(
                Decimal(str(factor.value or 0))
                * Decimal(str(factor.weight))
                * Decimal(str(factor.coverage))
                / coverage
            )
            if coverage
            else 0
        )
    return ScoreAxis(
        id=axis_id,
        label=label,
        weight=float(weight),
        value=rounded(value) if value is not None else None,
        coverage=float(coverage),
        lower_bound=rounded(lower),
        upper_bound=rounded(upper),
        contribution=rounded((value or _ZERO) * Decimal(weight)),
        factors=factors,
    )


def _financial(row: dict, current: Decimal | None) -> ScoreAxis:
    saving = number(row.get("economia_mensal"))
    delta = None
    if current is not None and current > 0 and saving is not None:
        delta = max(_ZERO, min(_HUNDRED, Decimal(50) + _HUNDRED * saving / current))
    return _axis(
        "financial",
        "Financeiro",
        "0.35",
        [
            _factor(
                "savings",
                "Economia recorrente",
                "1",
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
        ],
    )


def _technical(workloads: list[dict], provider: str, inventory: dict) -> ScoreAxis:
    samples: dict[str, list] = {
        k: [] for k in ("compatibility", "refactoring", "lock_in", "dependencies")
    }
    for workload in workloads:
        assessment = assess(workload, provider, inventory)
        cost = number(workload.get("cost")) or _ZERO
        for key, entries in samples.items():
            value, coverage, hypothesis = assessment[key]
            entries.append(
                (
                    cost,
                    number(value),
                    {
                        **assessment["evidence"],
                        "cost_weight": str(cost),
                        "coverage": str(coverage),
                        "hypothesis": hypothesis,
                        "assessed_value": value,
                    },
                )
            )
    definitions = (
        (
            "compatibility",
            "Compatibilidade funcional",
            "0.4",
            "mesma capacidade curada: 100; não comprova compatibilidade da aplicação",
        ),
        (
            "refactoring",
            "Esforço de adaptação",
            "0.3",
            "metadado: refatorar 30, dispensado 100; "
            "hipótese curada: rehost 80, replatform 60, refactor 30",
        ),
        (
            "lock_in",
            "Portabilidade",
            "0.2",
            "lock-in baixo: 100; médio: 50; alto: 0; pior alternativa conhecida",
        ),
        (
            "dependencies",
            "Dependências do inventário",
            "0.1",
            "acoplamento baixo: 100; médio: 50; alto: 0, somente na fração correlacionada",
        ),
    )
    return _axis(
        "technical",
        "Técnico",
        "0.5",
        [
            _factor(key, label, weight, rule, samples[key])
            for key, label, weight, rule in definitions
        ],
    )


def _identified_coverage(payload: dict) -> Decimal:
    scope = [
        w
        for w in all_workloads(payload)
        if not is_financial_component(str(w.get("name") or ""))
        and normalized(w.get("category")) not in (_NON_WORKLOAD - UNCLASSIFIED)
        and (number(w.get("cost")) or _ZERO) > 0
    ]
    total = sum((number(w.get("cost")) or _ZERO for w in scope), _ZERO)
    identified = sum(
        (
            number(w.get("cost")) or _ZERO
            for w in scope
            if w.get("service_type") and w.get("type_source") in CURATED
        ),
        _ZERO,
    )
    return identified / total if total else _ZERO


def _coverage_axis(row: dict, identified: Decimal, technical: ScoreAxis) -> ScoreAxis:
    values = (
        ("identification", "Identificação dos serviços", identified),
        ("price_coverage", "Cobertura de preços", fraction(row.get("cobertura"))),
        ("technical_coverage", "Evidência técnica", Decimal(str(technical.coverage))),
    )
    return _axis(
        "coverage",
        "Cobertura da análise",
        "0.15",
        [
            _factor(
                key,
                label,
                weight,
                "100 * fração coberta; ausência de evidência: 0%",
                [(_ONE, value * _HUNDRED, {"source": key, "value": str(value)})],
            )
            for (key, label, value), weight in zip(values, ("0.3", "0.4", "0.3"), strict=True)
        ],
    )


def _scenario(
    row: dict,
    current: Decimal | None,
    workloads: list[dict],
    evidence: dict,
    *,
    inventory: dict,
    identified: Decimal,
) -> ScenarioScore:
    provider = normalized(row.get("provedor"))
    technical = _technical(workloads, provider, inventory)
    axes = [_financial(row, current), technical, _coverage_axis(row, identified, technical)]
    lower = sum((Decimal(str(a.lower_bound)) * Decimal(str(a.weight)) for a in axes), _ZERO)
    upper = sum((Decimal(str(a.upper_bound)) * Decimal(str(a.weight)) for a in axes), _ZERO)
    coverage = Decimal(str(axes[2].value or 0)) / _HUNDRED
    enough = (
        identified >= _MIN_COVERAGE
        and technical.coverage >= float(_MIN_COVERAGE)
        and sum(f.coverage >= float(_MIN_COVERAGE) for f in technical.factors)
        >= _MIN_TECHNICAL_FACTORS
        and fraction(row.get("cobertura")) >= _MIN_COVERAGE
        and axes[0].value is not None
    )
    complete = (
        enough
        and identified == 1
        and fraction(row.get("cobertura")) == 1
        and technical.coverage == 1
        and not any(f.provisional for f in technical.factors)
    )
    # A small sample must not be extrapolated to the entire estate. Still publish
    # its conservative, evidenced minimum when an economic comparison exists.
    comparable = axes[0].value is not None
    if not enough and comparable:
        for axis in axes:
            axis.contribution = rounded(Decimal(str(axis.lower_bound)) * Decimal(str(axis.weight)))
            for factor in axis.factors:
                factor.contribution = rounded(
                    Decimal(str(factor.lower_bound)) * Decimal(str(factor.weight))
                )
    value = (
        (sum((Decimal(str(a.contribution)) for a in axes), _ZERO) if enough else lower)
        if comparable
        else None
    )
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
        status="complete" if complete else ("partial" if comparable else "not_scored"),
        value=rounded(value) if value is not None else None,
        lower_bound=rounded(lower),
        upper_bound=rounded(upper),
        coverage=float(coverage),
        axes=axes,
        assessment_method="evidence_normalized" if enough else "conservative_bound",
        missing_information=[]
        if enough
        else [
            "Nota parcial conservadora: soma apenas contribuições comprovadas, sem "
            "extrapolar a amostra. Para nota normalizada faltam ≥50% de identificação, "
            "preços e evidência técnica e dois critérios técnicos com cobertura ≥50%."
        ],
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
    inventory = inventory_services(payload)
    identified = _identified_coverage(payload)
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
        score = _scenario(
            row,
            current,
            workloads,
            governance.get(provider, {}),
            inventory=inventory,
            identified=identified,
        )
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
    comparisons = [
        DecisionComparison(
            provider=normalized(row.get("provedor")),
            current_monthly_cost=rounded(current) if current is not None else None,
            target_monthly_cost=rounded(cost)
            if (cost := number(row.get("custo_estimado_total"))) is not None
            else None,
            cost_delta=rounded(cost - current)
            if cost is not None and current is not None
            else None,
            price_coverage=float(fraction(row.get("cobertura"))),
        )
        for row in rows
    ]
    no_cheaper_target = (
        bool(rows)
        and current is not None
        and current > 0
        and all(
            (cost := number(row.get("custo_estimado_total"))) is not None
            and cost >= current
            and fraction(row.get("cobertura")) > 0
            for row in rows
        )
    )
    outcome = "migrate" if selected else "retain" if no_cheaper_target else "inconclusive"
    # Display the closest economic alternative without turning it into a recommendation.
    reference = selected or next(
        iter(
            sorted(
                scores,
                key=lambda s: (
                    -(number(by_provider[s.provider].get("economia_mensal")) or _ZERO),
                    s.provider,
                ),
            )
        ),
        None,
    )
    reason = (
        (
            "Maior score completo entre cenários elegíveis; desempate por economia e provedor."
            if all_complete
            else "Comparação econômica preliminar entre destinos elegíveis; "
            "faltam dados para score completo."
        )
        if selected
        else (
            "Permanecer na cloud atual: os destinos cotados não reduzem o gasto mensal. "
            "O score mede a atratividade da migração para a alternativa econômica mais próxima, "
            "não a qualidade da cloud atual. Conclusão limitada ao escopo e preços avaliados; "
            "revise a cobertura, as pendências e os custos de transição antes de decidir."
            if no_cheaper_target
            else "Comparação inconclusiva: não há destino elegível. Ausência de cotação ou "
            "restrições não comprovam que permanecer seja economicamente melhor."
        )
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
        "policy": DECISION_POLICY_VERSION,
        "workloads": workloads,
        "rows": rows,
        "current": str(current),
        "context": context.model_dump(exclude_none=True),
        "source": normalized(payload.get("provedor_origem") or payload.get("source_provider")),
        "currency": payload.get("moeda") or payload.get("currency"),
        "confidence": confidence,
        "inventory_services": inventory,
        "identified_coverage": str(identified),
    }
    digest = hashlib.sha256(
        json.dumps(
            _canonical(snapshot), sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()
    return DecisionSupport(
        corporate_benefits=build_corporate_benefits(),
        input_fingerprint=digest,
        selected_provider=selected.provider if selected else None,
        outcome=outcome,
        comparison_basis=comparisons,
        selection_reason=reason,
        score=reference,
        provider_scores=scores,
        workload_strategies=strategies,
        strategy_summary=summary,
        confidence=confidence,
        limitations=[
            "Política de regras v4.2 (35/50/15), não modelo treinado nem probabilidade de sucesso.",
            "Financeiro 35%, técnico 50%, cobertura da análise 15%; benefícios não pontuam.",
            "Nota técnica normalizada pela evidência disponível; hipóteses são preliminares. "
            "Sem cobertura mínima, a nota é o mínimo comprovado, não autorização para migrar.",
            "Limites representam fatores desconhecidos, não intervalo estatístico.",
            "Billing não comprova dependências, topologia, compliance ou autorização para migrar.",
            "Economia recorrente não inclui todo o investimento de migração "
            "ou créditos não comprovados.",
            "7 Rs do catálogo são candidatos por componente; declarações são do responsável.",
        ],
        legacy_score_semantics="normalized_value_when_available; zero_when_not_scored",
    )
