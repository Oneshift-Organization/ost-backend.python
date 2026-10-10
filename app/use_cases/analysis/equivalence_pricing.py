"""Attach existing scenario prices without duplicating category budgets per service."""

from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal

from app.domain.services.decision_support import number
from app.domain.services.sku_normalization import normalize_provider

_CENT = Decimal("0.01")
_CATEGORIES = {
    "armazenamento_ssd": ("Discos SSD", {"block_storage"}),
    "armazenamento_hdd": ("Discos HDD", {"block_storage"}),
    "armazenamento_objetos": ("Armazenamento de objetos", {"object_storage"}),
    "egresso_rede": ("Tráfego de saída", {"network_egress"}),
    "banco_dados": ("Bancos de dados", {"relational_database", "managed_relational_database"}),
}


def _provider(target: dict) -> str:
    return normalize_provider(str(target.get("provider") or target.get("provedor") or "")).lower()


def _eligible(sources: list[dict], kinds: set[str], provider: str) -> list[dict]:
    return [s for s in sources if (
        s.get("service_type") in kinds
        and s.get("type_source") == "curado"
        and (number(s.get("cost")) or Decimal(0)) > 0
        and any(_provider(t) == provider for t in s.get("targets", []))
        and not any(_provider(t) == provider and t.get("financials")
                    for t in s.get("targets", []))
    )]


def _financials(quote: dict, base: Decimal, price: Decimal) -> dict:
    return {
        "custo_mensal_estimado": float(price),
        "delta_custo": float(price - base),
        "confianca_preco": quote.get("confianca_preco", "low"),
        "pricing_method": "category_rate_ratio",
        "pricing_evidence": {
            **quote, "source_cost": float(base),
            "assumption": "Razão de tarifas de referência; não é cotação de SKU nem uso medido.",
        },
    }


def _allocate(members: list[dict], provider: str, base: Decimal, quote: dict) -> None:
    """Stable cent allocation; alternatives share a budget and are never additive."""
    total = number(quote["custo_mensal_estimado"])
    remainder = total
    for index, member in enumerate(members):
        source_cost = number(member["cost"])
        price = (total * source_cost / base).quantize(_CENT, rounding=ROUND_HALF_UP)
        if index == len(members) - 1:
            price = remainder
        remainder -= price
        for target in member["targets"]:
            if _provider(target) == provider:
                target["financials"] = _financials(quote, source_cost, price)


def attach_category_prices(sources: list[dict], payload: dict) -> list[dict]:
    """Allocate only when the *whole* source category reconciles; otherwise expose its scope."""
    sources = deepcopy(sources)
    auxiliary = payload.get("custos_auxiliares") or {}
    for key, (label, kinds) in _CATEGORIES.items():
        base = number(auxiliary.get(key))
        if base is None or base <= 0:
            continue
        for quote in auxiliary.get(f"equivalentes_{key}", []):
            price = number(quote.get("custo_mensal_estimado"))
            if price is None or price < 0:
                continue
            provider = _provider(quote)
            members = sorted(_eligible(sources, kinds, provider), key=lambda s: s["identity"])
            if key in {"armazenamento_ssd", "armazenamento_hdd"} and all(
                (number(auxiliary.get(k)) or Decimal(0)) > 0
                for k in ("armazenamento_ssd", "armazenamento_hdd")
            ):
                members = []  # mixed storage media need dimension-specific consumption
            member_total = sum((number(m["cost"]) for m in members), Decimal(0))
            # Exact cents only; a service can contain several billing dimensions.
            if members and member_total == base:
                _allocate(members, provider, base, quote)
                continue
            sources.append({
                "identity": f"category/{key}/{provider}", "name": f"{label} (agregado)",
                "source_service": label, "cost": float(base), "basis": "category_budget",
                "targets": [{
                    "provider": provider, "service_name": f"{label} — cenário {provider.upper()}",
                    "financials": _financials(quote, base, price),
                }],
                "scoped_provider": provider,
            })
    return sources


def reconcile_pricing(rows: list, payload: dict) -> list[dict]:
    """Explain graph = selected scoped prices + unchanged source balance, at cents precision."""
    summary = payload.get("resumo_executivo") or {}
    current = number(summary.get("custo_mensal_atual"))
    if current is None:
        return []
    result = []
    for scenario in summary.get("por_provedor", []):
        provider = _provider(scenario)
        selected = [r for r in rows if r.target_provider.lower() == provider
                    and r.included_in_estimate]
        covered = sum((number(r.source_monthly_cost) or Decimal(0) for r in selected), Decimal(0))
        priced = sum((number(r.estimated_monthly_cost) or Decimal(0) for r in selected), Decimal(0))
        expected = number(scenario.get("custo_estimado_total"))
        residual = current - covered
        total = residual + priced
        # Pool components and totals are independently rounded on serialization.
        tolerance = _CENT * max(1, len(selected))
        reconciled = expected is not None and residual >= 0 and abs(total - expected) <= tolerance
        result.append({
            "provider": provider, "currency": payload.get("moeda", "USD"),
            "status": "reconciled" if reconciled else "review_required",
            "source_covered": float(covered), "priced_target": float(priced),
            "unchanged_source_balance": float(residual),
            "estimated_total": float(total),
            "published_total": float(expected) if expected is not None else None,
            "priced_components": len(selected),
            "note": (
                "Saldo sem cotação mantido ao custo atual; não é preço validado no destino."
                if reconciled else "Escopos não reconciliados; revisar antes de usar a economia."
            ),
        })
    return result
