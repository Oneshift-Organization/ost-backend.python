"""Project every trusted mapping to the UI, without asking an LLM to enumerate it.

Family prices belong to family pools. Category totals must NOT be copied onto
each service: named catalog mappings without scoped prices stay explicitly null.
The same snapshot always produces the same identities, ordering and row count.
"""

from __future__ import annotations

import hashlib
import json

from app.domain.services.decision_support import number
from app.domain.services.migration_strategy import normalized, technical_evidence
from app.domain.services.service_identity import is_financial_component
from app.domain.services.sku_normalization import normalize_provider
from app.schemas.analysis_v2 import CrossCloudEquivalent
from app.use_cases.analysis.equivalence_pricing import attach_category_prices

_PROVIDERS = {"aws", "azure", "gcp", "oci"}
_BILLING_ONLY = {"tax", "taxes", "imposto", "impostos", "commitment", "compromisso",
                 "discount", "credits", "support"}


def _provider(value: object) -> str:
    return normalize_provider(str(value or "")).lower()


def _target(raw: dict) -> dict:
    return {
        "provider": _provider(raw.get("provider") or raw.get("provedor")),
        "service_name": raw.get("service_name") or raw.get("nome_servico"),
        "metadata": raw.get("metadata") or raw.get("metadados") or {},
        "financials": raw.get("financials") or raw.get("financeiro") or {},
        "sku": raw.get("sku") or raw.get("sku_id"),
        "basis": raw.get("base_correspondencia"),
    }


def _sources(payload: dict) -> list[dict]:
    sources = []
    pools = payload.get("pools_computacao") or []
    for pool in pools:
        family = str(pool.get("familia") or "Família não identificada")
        region = str(pool.get("regiao") or "global")
        sources.append({
            "identity": f"pool/{family}/{region}",
            "name": f"{family} · {region}",
            "source_service": family,
            "targets": pool.get("equivalentes") or [],
            "basis": "family_pool",
            # Máquina virtual migra por rehost em qualquer nuvem: pouco acoplamento
            # e nenhuma reescrita do lado de origem. Sem isto o pool chegava sem
            # evidência e a API devolvia acoplamento vazio para todo o compute.
            # O que o destino exigir a mais continua valendo: `technical_evidence`
            # fica com a pior restrição dos dois lados.
            "source_metadata": {"requires_refactoring": False, "vendor_lock_in_level": "low"},
            "commitment": pool.get("custo_coberto_por_compromisso") is True,
            "cost": sum(float(pool.get(k) or (pool.get("custos") or {}).get(k) or 0)
                        for k in ("custo_computacao", "custo_licenca_so")),
        })
    if "decision_workloads" in payload:
        workloads = payload.get("decision_workloads") or []
    else:
        workloads = [{
            **w,
            "name": w.get("service_name") or w.get("canonical_service"),
            "targets": w.get("equivalents") or [],
            "basis": "provided_workload_data",
        } for w in payload.get("workloads") or []]
    # Older snapshots already contain service types in the compact service list.
    types = {s.get("nome"): s.get("tipo") for s in payload.get("servicos") or []}
    for workload in workloads:
        category = normalized(workload.get("category"))
        if category in _BILLING_ONLY or is_financial_component(
            str(workload.get("name") or ""), category,
        ):
            continue
        name = str(workload.get("name") or "Serviço não identificado")
        service_type = workload.get("service_type") or types.get(name)
        if pools and service_type == "virtual_server":
            continue  # compute is already represented at family/region granularity
        sources.append({
            **workload,
            "identity": f"service/{category}/{name}",
            "name": name,
            "source_service": name,
            "basis": workload.get("basis", "catalog_snapshot"),
        })
    return attach_category_prices(sources, payload)


def _money(financials: dict, en: str, pt: str) -> float | None:
    value = number(financials.get(en, financials.get(pt)))
    return float(value) if value is not None else None


def _row(source: dict, target: dict, provider: str) -> CrossCloudEquivalent:
    metadata = target["metadata"]
    name = target["service_name"]
    financials = target["financials"] if name else {}
    refactor, lock = technical_evidence(source, target)
    inferred = source.get("type_source") == "inferido"
    return CrossCloudEquivalent(
        workload_id=hashlib.sha256(f"{provider}/{source['identity']}".encode()).hexdigest()[:20],
        workload=source["name"],
        source_provider=provider.upper(),
        source_service=source["source_service"],
        target_provider=target["provider"].upper(),
        target_service=name,
        target_sku=target["sku"],
        source_monthly_cost=float(source["cost"]) if source.get("cost") is not None else None,
        pricing_method=financials.get("pricing_method") or (
            "capacity_quote" if financials and source["basis"] == "family_pool" else None
        ),
        pricing_reason=_pricing_reason(source, name, financials),
        pricing_evidence=financials.get("pricing_evidence") or financials,
        mapping_basis="inferred_catalog" if inferred else target["basis"] or source["basis"],
        equivalence_type=metadata.get("equivalence_type") or "unknown",
        estimated_monthly_cost=_money(
            financials, "estimated_monthly_cost", "custo_mensal_estimado"
        ),
        cost_delta=None if source.get("commitment") else _money(
            financials, "cost_delta", "delta_custo"
        ),
        pricing_confidence=financials.get("pricing_confidence", financials.get("confianca_preco")),
        portability="unknown" if inferred else metadata.get("portability") or "unknown",
        migration_risk="unknown" if inferred else metadata.get("migration_risk") or "unknown",
        requires_refactoring=refactor,
        vendor_lock_in_level=lock,
    )


def _pricing_reason(source: dict, name: str | None, financials: dict) -> str:
    if not name:
        return "Mapeamento ausente: validar serviço de destino antes de cotar."
    if financials.get("pricing_method") == "category_rate_ratio":
        return "Estimativa por razão de tarifas da categoria; validar consumo, região e descontos."
    if financials:
        return "Estimativa de capacidade; confirmar arquitetura, região e condições comerciais."
    if source.get("type_source") == "inferido":
        return "Classificação inferida: confirmar tipo de serviço e dimensionamento."
    return "Sem tarifa ou consumo compatível neste escopo. Consultar também os custos agregados."


def project_equivalences(payload: dict) -> list[CrossCloudEquivalent]:
    """Never import rows, cardinality, prices or technical facts from model output."""
    provider = _provider(payload.get("provedor_origem") or payload.get("source_provider"))
    sources = _sources(payload)
    providers = {
        _provider(p.get("provedor") or p.get("provider"))
        for p in (payload.get("resumo_executivo") or {}).get("por_provedor", [])
    }
    providers.update(_target(t)["provider"] for s in sources for t in s.get("targets") or [])
    providers = (providers & _PROVIDERS) - {provider}
    rows: dict[str, CrossCloudEquivalent] = {}
    for source in sources:
        targets = [_target(t) for t in source.get("targets") or []]
        targets = [t for t in targets if t["provider"] in providers]
        # A missing mapping stays visible; missing price is not a missing service.
        mapped = {t["provider"] for t in targets}
        if not source.get("scoped_provider"):
            targets.extend(_target({"provider": p}) for p in sorted(providers - mapped))
        for target in targets:
            row = _row(source, target, provider)
            key = json.dumps(row.model_dump(), sort_keys=True)
            rows[key] = row  # exact duplicates only; alternative SKUs are preserved
    ordered = sorted(rows.values(), key=lambda r: (
        r.workload.casefold(), r.workload_id or "", r.target_provider,
        r.target_service or "", r.target_sku or "", r.model_dump_json(),
    ))
    _select_scenario_rows(ordered)
    return ordered


def _select_scenario_rows(rows: list[CrossCloudEquivalent]) -> None:
    chosen: set[tuple[str, str]] = set()
    for row in sorted(rows, key=lambda r: (
        r.estimated_monthly_cost if r.estimated_monthly_cost is not None else float("inf"),
        r.target_sku or "", r.target_service or "",
    )):
        key = (row.workload_id, row.target_provider)
        if row.estimated_monthly_cost is not None and key not in chosen:
            row.included_in_estimate = True
            chosen.add(key)
