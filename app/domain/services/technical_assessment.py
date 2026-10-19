"""Pure evidence rules for the score; hypotheses never become catalog facts.

Does not select a price/SKU or change the 7 Rs. Alternative targets are usable
only when they agree on capability; scores retain the least favorable evidence.
"""

from decimal import Decimal, InvalidOperation

from app.domain.services.lock_in_efetivo import LockInEfetivo, lock_in_da_origem
from app.domain.services.migration_strategy import normalized, technical_evidence

CURATED = {"curado", "catalog_alias"}
LOCK_POINTS = {"low": 100, "medium": 50, "high": 0}
CLASS_POINTS = {"rehost": 80, "replatform": 60, "refactor": 30}


def fraction(value: object) -> Decimal:
    if isinstance(value, bool):
        return Decimal(0)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)
    return result if result.is_finite() and 0 <= result <= 1 else Decimal(0)


def inventory_services(payload: dict) -> dict:
    """Only the immutable, backend-built topology; not narrative/LLM output."""
    topology = payload.get("topologia") or {}
    coverage = (topology.get("resumo") or {}).get("cobertura") or {}
    try:
        cost = Decimal(str(coverage.get("custo_estimado") or 0))
        estimated = cost.is_finite() and cost > 0
    except InvalidOperation:
        estimated = True  # malformed provenance must not certify measured costs
    services = (topology.get("detalhe") or {}).get("servicos") or {}
    return {name: {**entry, "cost_estimated": estimated} for name, entry in services.items()}


def assess(workload: dict, provider: str, inventory: dict, *, effective_lock: bool = True) -> dict:
    targets = [
        t for t in workload.get("targets") or [] if normalized(t.get("provider")) == provider
    ]
    same_type = bool(workload.get("service_type")) and all(
        t.get("service_type") == workload["service_type"] for t in targets
    )
    curated = workload.get("type_source") in CURATED
    # A direct caller can still supply one explicit metadata pair. Alternatives
    # need a curated, same-capability match, never merely the same provider.
    usable = bool(targets) and (len(targets) == 1 or (same_type and curated))
    evidence = [technical_evidence(workload, t) for t in targets] if usable else []
    refactors = [r for r, _ in evidence]
    locks = [LOCK_POINTS.get(lock) for _, lock in evidence]
    dependency = inventory.get(str(workload.get("name") or "")) or {}
    origin = lock_in_da_origem(workload, dependency)
    provisional_lock = False
    if effective_lock:
        locks, provisional_lock = _effective_locks(workload, targets if usable else [], origin)
    refactor = None
    if any(r is True for r in refactors):
        refactor = True
    elif refactors and all(r is not None for r in refactors):
        refactor = any(refactors)
    lock = min(locks) if locks and all(v is not None for v in locks) else None
    raw_lock = next((k for k, v in LOCK_POINTS.items() if v == lock), None)
    adaptation = (30 if refactor else 100) if refactor is not None else None
    hypothesis = False
    if adaptation is None and usable and same_type and curated:
        migration_class = (workload.get("migration") or {}).get("classe_migracao")
        adaptation = CLASS_POINTS.get(migration_class)
        hypothesis = adaptation is not None

    coverage = fraction(dependency.get("cobertura"))
    dependency_value = LOCK_POINTS.get(dependency.get("nivel")) if coverage else None
    # Exclusivity affects source lock-in, with provenance; dependency retains
    # its separate 10% factor. Neither overwrites catalog metadata or the 7 Rs.
    return {
        "compatibility": (100 if usable and same_type and curated else None, 1, False),
        "refactoring": (adaptation, 1, hypothesis),
        "lock_in": (lock, 1, provisional_lock),
        "dependencies": (dependency_value, coverage, dependency.get("cost_estimated", False)),
        "evidence": {
            "workload": workload.get("name"),
            "source": workload.get("evidence_source", "catalog_snapshot"),
            "requires_refactoring": refactor,
            "lock_in": raw_lock,
            "lock_in_fonte": origin.fonte if effective_lock else None,
            "lock_in_motivo": origin.motivo if effective_lock else None,
            "lock_in_origem": origin.nivel if effective_lock else None,
            "type_source": workload.get("type_source"),
            "service_type": workload.get("service_type"),
            "alternatives": sorted(str(t.get("service_name") or "") for t in targets),
            "dependency_level": dependency.get("nivel") if dependency_value is not None else None,
            "dependency_coverage": str(coverage),
            "dependency_cost_estimated": dependency.get("cost_estimated", False),
            "dependency_reason": dependency.get("motivo") if dependency_value is not None else None,
            "migration_class": (workload.get("migration") or {}).get("classe_migracao"),
        },
    }


def _effective_locks(
    workload: dict, targets: list[dict], origin: LockInEfetivo
) -> tuple[list[int | None], bool]:
    if workload.get("type_source") == "inferido":
        return [], False
    source = LOCK_POINTS.get(origin.nivel)
    locks = []
    provisional_values = []
    proven_values = []
    for target in targets:
        destination = LOCK_POINTS.get(
            normalized((target.get("metadata") or {}).get("vendor_lock_in_level"))
        )
        known = [v for v in (source, destination) if v is not None]
        value = min(known) if known else None
        locks.append(value)
        # A known equally/stronger restrictive target proves this value even
        # when the origin is hypothetical. Lower points mean more lock-in.
        if (
            origin.provisorio
            and source is not None
            and (destination is None or source < destination)
        ):
            provisional_values.append(value)
        elif value is not None:
            proven_values.append(value)
    known_locks = [v for v in locks if v is not None]
    decisive = min(known_locks) if known_locks else None
    provisional = (
        None not in locks
        and decisive is not None
        and decisive in provisional_values
        and decisive not in proven_values
    )
    return locks, provisional
