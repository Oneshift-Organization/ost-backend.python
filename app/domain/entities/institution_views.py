"""O recorte de uma análise compartilhada pelas visões de quem a abre.

O corte é aqui, não na tela: o que a visão não mostra não sai da API.
- Sem a financeira: nenhum valor em dinheiro, nem nos números nem nos textos.
- Sem a técnica: sem recursos, equivalentes, estratégias por recurso e o parecer do arquiteto.
- Sem a estratégica: sem racional, notas, plano de ação e o parecer do estrategista.
"""

import re
from copy import deepcopy
from typing import Any

from app.domain.entities.institution import InstitutionView

FINANCIAL = InstitutionView.FINANCIAL
TECHNICAL = InstitutionView.TECHNICAL
STRATEGIC = InstitutionView.STRATEGIC

# Campo cujo número é dinheiro (ou cuja subárvore é dinheiro: `economia_por_fonte`, `destino`)
_MONEY_KEY = re.compile(
    r"cost|custo|saving|economia|price|preco|spend|gasto|usd|amount|valor|payback"
    r"|reduction|sem_aplicacao|sem_cotacao|soma_linhas|fora_do_escopo|diferenca|^destino$",
    re.IGNORECASE,
)
# Valor em dinheiro escrito pelo modelo: "US$ 1.234,56", "R$ 300 mil", "1200 USD"
_MONEY_TEXT = re.compile(
    r"(?:US\$|R\$|USD|BRL|EUR|€|\$)\s?[-+]?\d[\d.,]*(?:\s?(?:mil|milh(?:ão|ões)|bilh(?:ão|ões)|k|mi|bi)\b)?"
    r"|\b\d[\d.,]*\s?(?:USD|BRL|dólares|reais)\b",
    re.IGNORECASE,
)
RESTRICTED = "[valor restrito]"

# Parecer de cada agente → a visão que o mostra
_AGENT_VIEW = {
    "finops": FINANCIAL,
    "cloud_engineering": TECHNICAL,
    "risk_migration": STRATEGIC,
}
# Só dinheiro: saem inteiros sem a visão financeira
_FINANCIAL_ONLY = ("billing_baseline", "savings_by_source", "pricing_reconciliation")
# De `resumo_executivo.por_provedor`, o que a tela lê da economia de cada destino
_BY_PROVIDER = ("provedor", "economia_por_fonte", "itens_gratis")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _no_numbers(value: Any) -> Any:
    if _is_number(value):
        return None
    if isinstance(value, dict):
        return {key: _no_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_no_numbers(item) for item in value]
    return without_money(value)


def without_money(value: Any) -> Any:
    """Cópia sem dinheiro: número de campo de custo vira null; valor citado em texto, restrito."""
    if isinstance(value, dict):
        return {
            key: _no_numbers(item) if _MONEY_KEY.search(str(key)) else without_money(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [without_money(item) for item in value]
    if isinstance(value, str):
        return _MONEY_TEXT.sub(RESTRICTED, value)
    return value


def shared_result(result: dict[str, Any], views: frozenset[InstitutionView]) -> dict[str, Any]:
    """O resultado da análise (o mesmo da tela do dono), sem o que as visões não mostram."""
    out = deepcopy(result)
    decision = out.get("decision_support")
    decision = decision if isinstance(decision, dict) else {}
    agents = out.get("agent_views")
    if isinstance(agents, dict):
        for name, view in _AGENT_VIEW.items():
            if view not in views:
                agents.pop(name, None)
    if TECHNICAL not in views:
        out["cross_cloud_equivalents"] = []
        if decision:
            decision["workload_strategies"] = []
    if STRATEGIC not in views:
        out.update(strategic_notes=[], action_plan=[], recommendation_rationale="")
        if decision:
            decision["selection_reason"] = ""
    if FINANCIAL in views:
        return out
    for key in _FINANCIAL_ONLY:
        out.pop(key, None)
    if decision:
        decision["comparison_basis"] = []
        decision["corporate_benefits"] = None
    for row in out.get("cross_cloud_equivalents") or []:
        if isinstance(row, dict):
            row.pop("pricing_evidence", None)
    cleaned: dict[str, Any] = without_money(out)
    return cleaned


def shared_payload(payload: Any, views: frozenset[InstitutionView]) -> dict[str, Any]:
    """Do payload congelado, só o que a tela lê: a nuvem de origem e, com a visão financeira,
    o gasto de hoje, a economia por fonte e o câmbio. Prompts e linhas da fatura não saem."""
    data = payload if isinstance(payload, dict) else {}
    summary = data.get("resumo_executivo")
    summary = summary if isinstance(summary, dict) else {}
    out: dict[str, Any] = {
        "moeda": data.get("moeda"),
        "resumo_executivo": {"provedor_atual": summary.get("provedor_atual")},
    }
    if FINANCIAL not in views:
        return out
    rows = summary.get("por_provedor")
    out["resumo_executivo"].update(
        custo_mensal_atual=summary.get("custo_mensal_atual"),
        por_provedor=[
            {key: deepcopy(row.get(key)) for key in _BY_PROVIDER}
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        ],
    )
    premises = data.get("premissas")
    if isinstance(premises, dict):
        out["premissas"] = {key: deepcopy(premises.get(key)) for key in ("moeda_origem", "fx")}
    return out


def shared_detail(data: Any, views: frozenset[InstitutionView]) -> Any:
    """Acoplamento, aplicações e o diagnóstico da entrada: iguais aos do dono, sem dinheiro
    quando falta a visão financeira."""
    return deepcopy(data) if FINANCIAL in views else without_money(data)


def parse_views(raw: str | None) -> frozenset[InstitutionView] | None:
    """`?views=financial,technical`: ver com menos visões que as próprias (o "Ver como")."""
    if not raw:
        return None
    try:
        return frozenset(InstitutionView(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError:
        return None
