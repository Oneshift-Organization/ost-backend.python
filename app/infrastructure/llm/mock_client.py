from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

from app.schemas.analysis_ui import SavingsSummary

if TYPE_CHECKING:
    from pydantic import BaseModel


class MockLLMClient:
    """Mock LLM client that analyzes real context data to produce responses.

    No network calls. Parses the serialized MigrationContext from the prompt
    and derives viability_score, flags, recommendation, and savings from the
    actual workload data. This allows full pipeline testing without an API key.
    """

    async def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        pools = _parse_pools(prompt)
        pool_count = len(pools)

        total_spend = Decimal("0")
        # savings_by_provider: provider -> total (negative cost_delta = saving)
        savings_by_provider: dict[str, Decimal] = {}
        total_vcpus = Decimal("0")
        total_ram = Decimal("0")

        for p in pools:
            costs = p.get("costs", {})
            total_spend += _dec(costs.get("compute_base")) + _dec(costs.get("os_licensing"))
            total_vcpus += _dec(p.get("capacity", {}).get("vcpus"))
            total_ram += _dec(p.get("capacity", {}).get("ram_gib"))

            for eq in p.get("equivalents", []):
                prov = eq.get("provider", "")
                delta = _dec(eq.get("financials", {}).get("cost_delta"))
                savings_by_provider[prov] = savings_by_provider.get(prov, Decimal("0")) + delta

        # Best provider = min total cost_delta (most negative = most savings)
        best_provider, best_delta = "", Decimal("0")
        if savings_by_provider:
            best_provider, best_delta = min(
                savings_by_provider.items(), key=lambda kv: kv[1]
            )

        total_savings = -best_delta  # positive = saving
        pct = float(total_savings / total_spend * 100) if total_spend > 0 else 0.0

        if best_provider and total_savings > 0:
            recommendation = "MIGRATE"
            recommended_provider = best_provider.upper()
            savings_str = f"${float(total_savings):,.0f}/mês ({pct:.0f}%)"
            rationale = (
                f"{recommended_provider} apresenta a maior economia agregada ({savings_str}) "
                f"baseada em preços públicos on-demand dos equivalentes catalogados. "
                f"Workloads de computação ({pool_count} pools, {float(total_vcpus):.0f} vCPUs / "
                f"{float(total_ram):.0f} GiB) têm equivalentes técnicos confirmados no catálogo."
            )
        else:
            recommendation = "STAY"
            recommended_provider = ""
            rationale = "Nenhum equivalente com economia projetada encontrado no catálogo atual."

        notes: list[str] = []
        if pool_count > 0:
            notes.append(
                f"Capacidade total: {float(total_vcpus):.1f} vCPUs e "
                f"{float(total_ram):.1f} GiB RAM em {pool_count} pools "
                f"(análise por equivalentes do catálogo)."
            )
        if savings_by_provider:
            provider_summary = "; ".join(
                f"{p.upper()}: ${float(-d):,.0f}"
                for p, d in sorted(savings_by_provider.items(), key=lambda kv: kv[1])
                if d < 0
            )
            if provider_summary:
                notes.append(f"Economia estimada por provedor: {provider_summary}.")
        notes.append(
            "Preços baseados em catálogo público on-demand (pricing_confidence=low). "
            "Contratos de reserva ou negociação empresarial podem ampliar a economia."
        )

        plan: list[str] = [
            "Validar sizing dos pools com base na utilização real (pico vs média).",
            "Selecionar instâncias alvo por pool usando os equivalentes do catálogo.",
            "Executar prova de conceito com workload não crítica.",
            "Monitorar custo e performance por 30 dias pós-migração.",
        ]

        return response_model(
            viability_score=800,
            recommendation=recommendation,
            recommendation_rationale=rationale,
            recommended_provider=recommended_provider,
            savings_summary=SavingsSummary(
                total_monthly_savings_usd=round(float(total_savings), 2),
                percentage_reduction=round(pct, 1),
                payback_period_months=None,
                confidence_level="low",
            ),
            flags={},
            strategic_notes=notes,
            action_plan=plan,
        )


def _extract_json_array(prompt: str, key: str) -> list[dict]:
    """Extract a JSON array value for a given key from the prompt text."""
    start = prompt.find(f'"{key}"')
    if start == -1:
        return []
    colon = prompt.find(":", start)
    if colon == -1:
        return []
    bracket = prompt.find("[", colon)
    if bracket == -1:
        return []
    depth = 0
    end = bracket
    for i in range(bracket, len(prompt)):
        ch = prompt[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if depth != 0:
        return []
    try:
        blob = "{" + prompt[start:end] + "}"
        parsed = json.loads(blob)
        return parsed.get(key, [])
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_pools(prompt: str) -> list[dict]:
    """Extract compute_pools array from the new pool-based prompt payload."""
    pools = _extract_json_array(prompt, "compute_pools")
    if pools:
        return pools
    return _extract_json_array(prompt, "workloads")


def _dec(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal("0")


from decimal import InvalidOperation  # noqa: E402 — needed for _dec
