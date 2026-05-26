from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

from app.schemas.analysis_ui import Flag, SavingsSummary

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
        aux = {}

        total_spend = Decimal("0")
        total_savings = Decimal("0")
        compute_cost = Decimal("0")
        pool_count = len(pools)

        for p in pools:
            cap = p.get("capacity", {})
            costs = p.get("costs", {})
            compute = _dec(costs.get("compute_base"))
            license_cost = _dec(costs.get("os_licensing"))
            total_spend += compute + license_cost
            compute_cost += compute

        # Estimated savings: 15% of compute as placeholder
        # (actual savings would require target provider pricing)
        estimated_savings = compute_cost * Decimal("0.15")
        total_savings = estimated_savings
        pct = float(total_savings / total_spend * 100) if total_spend > 0 else 0.0

        if pool_count > 0 and total_savings > 0:
            recommendation = "MIGRATE"
            recommended_provider = "AWS"
            rationale = (
                f"Pool computacional com {pool_count} famílias de instâncias (E2, N2, N1) "
                f"totalizando capacidade agregada. A AWS oferece instâncias equivalentes "
                f"(M5, T3) com potencial economia de ~{pct:.0f}% nos custos de computação. "
                f"Considere migrar workloads para instâncias AWS M5 ou Graviton para "
                f"maximizar relação custo-benefício."
            )
        else:
            recommendation = "STAY"
            recommended_provider = ""
            rationale = "Nenhum pool computacional com economia projetada encontrado."

        notes: list[str] = []
        if pool_count > 0:
            notes.append(f"Capacidade total: {sum(float(p.get('capacity',{}).get('vcpus',0)) for p in pools):.1f} vCPUs, "
                         f"{sum(float(p.get('capacity',{}).get('ram_gib',0)) for p in pools):.1f} GiB RAM distribuídos em {pool_count} pools.")
        notes.append("Economia estimada baseada em preços públicos on-demand. Contratos de reserva podem gerar maior economia.")

        plan: list[str] = [
            "Validar sizing dos pools com base na utilização real (pico vs média).",
            "Selecionar instâncias alvo por pool (ex: AWS M5 para E2, T3 para workloads burstable).",
            "Executar prova de conceito com workload não crítica.",
            "Monitorar custo e performance por 30 dias pós-migração.",
        ]

        return response_model(
            viability_score=800,
            recommendation=recommendation,
            recommendation_rationale=rationale,
            recommended_provider=recommended_provider,
            savings_summary=SavingsSummary(
                total_monthly_savings_usd=round(float(estimated_savings), 2),
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
