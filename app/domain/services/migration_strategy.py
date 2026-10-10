"""Evidence-based 7 Rs. Pure rules, never inferred from an LLM response."""

from __future__ import annotations

import hashlib
from typing import Any

from app.schemas.decision_support import WorkloadStrategy

LOCK_RANK = {"low": 1, "medium": 2, "high": 3}
_SPECIAL_RULES = (
    ("retain", ("retain_required",), "Permanência exigida pelo responsável."),
    (
        "retire",
        ("unused_confirmed", "retirement_approved", "dependencies_reviewed"),
        "Desuso, aprovação de desativação e revisão das dependências declarados.",
    ),
    (
        "repurchase",
        ("saas_replacement_approved", "functional_fit_confirmed", "data_migration_planned"),
        "Substituição por SaaS aprovada, com aderência funcional e plano de dados declarados.",
    ),
    (
        "relocate",
        ("relocation_supported", "licenses_transferable", "unchanged_platform_confirmed"),
        "Movimentação da plataforma e transferência de licenças declaradas como suportadas.",
    ),
)


def normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


_PLATFORM_TYPES = frozenset({
    "relational_database", "nosql_database", "in_memory_database", "cache",
    "object_storage", "block_storage", "archive_storage", "file_storage",
    "container_registry", "kubernetes", "container_orchestration", "cdn",
    "load_balancer", "load_balancing", "managed_kubernetes", "message_broker",
    "managed_relational_database", "managed_mysql", "managed_postgresql",
    "managed_redis", "in_memory_cache", "archive_cold_storage", "managed_airflow",
    "container_instances", "serverless_containers", "managed_kafka", "messaging",
})


def _preliminary(result: WorkloadStrategy, workload: dict, target: dict) -> WorkloadStrategy:
    """A same-capability pair can support a hypothesis, not confirmed compatibility."""
    kind = workload.get("service_type")
    if not kind or kind != target.get("service_type"):
        return result
    if workload.get("type_source") not in {"curado", "catalog_alias"}:
        return result
    if kind == "virtual_server":
        strategy = "rehost"
        missing = ["Confirmar arquitetura CPU, sistema operacional, licenças e dependências."]
    elif kind in _PLATFORM_TYPES:
        strategy = "replatform"
        missing = ["Confirmar engine/versão ou API, formato dos dados e procedimento de migração."]
    elif kind in {"translation", "natural_language_processing", "conversational_ai"}:
        strategy = "refactor"
        missing = ["Revisar SDKs/APIs, qualidade dos modelos, dados e integrações dependentes."]
    elif (workload.get("migration") or {}).get("classe_migracao") in {
        "rehost", "replatform", "refactor",
    }:
        strategy = workload["migration"]["classe_migracao"]
        missing = ["Confirmar a aplicabilidade do padrão de migração desta classe de serviço."]
    else:
        return result
    return result.model_copy(update={
        "strategy": strategy, "status": "candidate", "rule": f"7r.{strategy}.capability",
        "rationale": "Origem e destino têm a mesma capacidade no catálogo; "
                     "recomendação preliminar, ainda sem validação da aplicação.",
        "assumptions": ["O equivalente selecionado atende aos requisitos funcionais da aplicação."],
        "missing_information": [
            *missing, "Validar dependências, disponibilidade e janela de mudança.",
        ],
    })


def technical_evidence(workload: dict, target: dict | None) -> tuple[bool | None, str | None]:
    """Worst known catalog constraint; absence is not False or low lock-in."""
    source = workload.get("migration") or {}
    if "source_metadata" in workload:
        raw = workload.get("source_metadata") or {}
        source = {
            "requer_refatoracao": raw.get("requires_refactoring"),
            "lock_in": raw.get("vendor_lock_in_level"),
        }
    metadata = (target or {}).get("metadata") or {}
    refactors = [source.get("requer_refatoracao"), metadata.get("requires_refactoring")]
    known = [v for v in refactors if type(v) is bool]
    # One positive constraint is sufficient; a negative needs evidence on both sides.
    refactor = True if True in known else (False if len(known) == len(refactors) else None)
    if workload.get("type_source") == "inferido":
        return None, None  # an inferred mapping cannot confirm technical compatibility
    locks = [normalized(source.get("lock_in")), normalized(metadata.get("vendor_lock_in_level"))]
    known_locks = [v for v in locks if v in LOCK_RANK]
    lock = max(known_locks, key=LOCK_RANK.__getitem__) if known_locks else None
    return refactor, lock


def classify_strategy(
    workload: dict,
    provider: str,
    target: dict | None,
    declared: dict[str, Any] | None = None,
) -> WorkloadStrategy:
    declared = declared or {}
    refactor, lock = technical_evidence(workload, target)
    identity = f"{normalized(workload.get('name'))}|{normalized(workload.get('category'))}"
    result = WorkloadStrategy(
        workload_id=hashlib.sha256(identity.encode()).hexdigest()[:20],
        workload=str(workload.get("name") or "Componente sem identificação"),
        provider=provider,
        target_service=(target or {}).get("service_name"),
        status="undetermined",
        rule="7r.insufficient_evidence",
        rationale="A fatura e o catálogo não comprovam a estratégia de migração.",
        requires_refactoring=refactor,
        vendor_lock_in_level=lock,
        evidence=[
            {
                "source": workload.get("evidence_source", "catalog_snapshot"),
                "migration": workload.get("migration") or {},
                "source_metadata": workload.get("source_metadata"),
                "type_source": workload.get("type_source"),
                "target_metadata": (target or {}).get("metadata") or {},
            }
        ],
        missing_information=[
            "Inventário da aplicação, dependências e validação de compatibilidade."
        ],
    )
    if declared:
        result.evidence.append({"source": "user_declared", **declared})

    matches = [r for r in _SPECIAL_RULES if all(declared.get(k) is True for k in r[1])]
    if len(matches) > 1 and matches[0][0] != "retain":
        result.rule = "7r.conflicting_declarations"
        result.rationale = "Há estratégias declaradas incompatíveis; confirmar uma alternativa."
        return result
    if matches:
        strategy, _, rationale = matches[0]
        return result.model_copy(
            update={
                "strategy": strategy,
                "status": "declared",
                "rule": f"7r.{strategy}.explicit_evidence",
                "rationale": rationale,
                "missing_information": [
                    "Declaração do responsável; não constitui validação independente "
                    "ou autorização de execução."
                ],
            }
        )
    if not target:
        result.missing_information.append("Equivalente suportado no provedor de destino.")
        return result
    if refactor is True:
        strategy = "refactor"
        rationale = "O catálogo registra necessidade de refatoração em origem ou destino."
    elif workload.get("service_type") in _PLATFORM_TYPES:
        return _preliminary(result, workload, target)
    elif refactor is False and lock in {"medium", "high"}:
        strategy = "replatform"
        rationale = "Sem refatoração declarada no catálogo, mas com acoplamento de plataforma."
    elif refactor is False and lock == "low":
        strategy = "rehost"
        rationale = (
            "Catálogo sem refatoração e com baixo lock-in; confirmar runtime e dependências."
        )
    else:
        return _preliminary(result, workload, target)
    return result.model_copy(
        update={
            "strategy": strategy,
            "status": "candidate",
            "rule": f"7r.{strategy}.catalog",
            "rationale": rationale,
            "confidence": "medium",
        }
    )


def classify_workload_strategy(
    workload: dict, provider: str, declared: dict[str, Any] | None = None,
) -> WorkloadStrategy:
    """Alternative destinations must agree; never silently pick one to decide a 7 R."""
    targets = [t for t in workload.get("targets", []) if normalized(t.get("provider")) == provider]
    targets = sorted(targets, key=lambda t: str(t.get("service_name")))
    if not targets:
        return classify_strategy(workload, provider, None, declared)
    decisions = [classify_strategy(workload, provider, t, declared) for t in targets]
    result = decisions[0]
    if len(decisions) == 1:
        return result
    alternatives = sorted({str(t["service_name"]) for t in targets if t.get("service_name")})
    if len({d.strategy for d in decisions}) > 1:
        result = classify_strategy(workload, provider, None, declared)
        result.rule = "7r.alternatives_disagree"
        result.rationale = "As alternativas exigem estratégias diferentes; selecionar o destino."
    result.target_service = None
    result.alternative_targets = alternatives
    result.confidence = "low"
    result.evidence = [e for d in decisions for e in d.evidence]
    result.missing_information = sorted({
        info for d in decisions for info in d.missing_information
    } | {"Selecionar e validar uma das alternativas de destino."})
    return result
