"""Read-only dashboard projection: no raw inputs, evidence IDs or private actions."""

from typing import Any


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _flags(value: Any) -> list[dict[str, str]]:
    themes = {"custo", "banco", "computacao", "seguranca", "precisao", "portabilidade", "operacao"}
    if not isinstance(value, list):
        return []
    flags = []
    for item in value:
        row = item if isinstance(item, dict) else {"message": _text(item)}
        if not _text(row.get("message")):
            continue
        flag = {key: _text(row.get(key)) for key in ("code", "message", "title", "impact_area")}
        theme = _text(row.get("theme"))
        flag["theme"] = theme if theme in themes else "outro"
        flags.append(flag)
    return flags


def _agents(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {}
    specifications = (
        ("finops", "financial_flags", "key_findings"),
        ("cloud_engineering", "technical_flags", "technical_recommendations"),
        ("risk_migration", "risk_flags", "blocked_or_uncertain_items"),
    )
    for name, flags, findings in specifications:
        raw = value.get(name)
        row = raw if isinstance(raw, dict) else {}
        result[name] = {
            "summary": _text(row.get("summary")),
            "confidence_level": _text(row.get("confidence_level")),
            flags: _flags(row.get(flags)),
            findings: _strings(row.get(findings)),
        }
        if name == "cloud_engineering":
            result[name]["migration_complexity"] = _text(row.get("migration_complexity"))
        if name == "risk_migration":
            result[name]["recommended_migration_sequence"] = [
                {key: _text(step.get(key)) for key in ("name", "duration", "detail")}
                for step in _rows(row.get("recommended_migration_sequence"))
                if _text(step.get("name"))
            ]
    return result


def _strategies(value: Any) -> list[dict[str, Any]]:
    strategies = []
    valid = {"retain", "retire", "rehost", "relocate", "repurchase", "replatform", "refactor"}
    for index, row in enumerate(_rows(value)):
        if not _text(row.get("workload")) or not _text(row.get("provider")):
            continue
        strategy = _text(row.get("strategy"))
        status = _text(row.get("status"))
        strategies.append(
            {
                "workload_id": f"shared-{index}",
                "workload": row["workload"],
                "provider": row["provider"],
                "target_service": _text(row.get("target_service")) or None,
                "alternative_targets": _strings(row.get("alternative_targets")),
                "strategy": strategy if strategy in valid else None,
                "status": status if status in {"candidate", "declared"} else "undetermined",
                "rationale": _text(row.get("rationale")),
                "missing_information": _strings(row.get("missing_information")),
            }
        )
    return strategies


def dashboard_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    decision = result.get("decision_support")
    return {
        "agent_views": _agents(result.get("agent_views")),
        "workload_strategies": _strategies(
            decision.get("workload_strategies") if isinstance(decision, dict) else None
        ),
    }
