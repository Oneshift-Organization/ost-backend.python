"""Tipo de equivalência, portabilidade e risco de cada destino (PLANO_EQUIVALENTES_E_TARIFAS §2).

A tabela de equivalentes mostrava "desconhecido" nos três campos em todas as linhas:
nenhum seed os preenchia, e o front marcava toda linha como "atenção". Aqui eles saem da
evidência que a linha já mostra (refatoração e lock-in do catálogo, pela pior restrição
dos dois lados). O que falta não é inventado: sem lock-in conhecido, risco e portabilidade
ficam desconhecidos; refatoração não confirmada nunca dá risco baixo.

Os campos vão na `metadata` do destino na preparação do payload; a projeção da tabela já
lê `metadata.equivalence_type`, `portability` e `migration_risk`. Módulo puro, sem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

HARDWARE = "hardware_match"
FUNCIONAL = "functional_match"
DESCONHECIDO = "unknown"

# Máquina virtual vai para máquina virtual: o mesmo lado de origem que a projeção dá aos
# pools (`equivalence_projection._sources`)
ORIGEM_DO_POOL: Mapping[str, object] = {
    "requires_refactoring": False,
    "vendor_lock_in_level": "low",
}

_PORTABILIDADE = {"low": "portable", "medium": "semi_portable", "high": "non_portable"}


def risco(refatora: bool | None, lock_in: str | None) -> str:
    """Pior restrição conhecida. Lock-in baixo só vira risco baixo com a refatoração
    dispensada; não confirmada, fica médio (o limite conservador, não um fato)."""
    if refatora is True or lock_in == "high":
        return "high"
    if lock_in == "medium":
        return "medium"
    if lock_in == "low":
        return "low" if refatora is False else "medium"
    return DESCONHECIDO


def portabilidade(lock_in: str | None) -> str:
    return _PORTABILIDADE.get(lock_in or "", DESCONHECIDO)


def classificar(refatora: bool | None, lock_in: str | None, *, tipo: str) -> dict[str, str]:
    """Os três campos da linha, a partir da refatoração e do lock-in já avaliados."""
    return {
        "equivalence_type": tipo,
        "portability": portabilidade(lock_in),
        "migration_risk": risco(refatora, lock_in),
    }
