"""Expectativa de migração por serviço (Onda 8 / E3).

A metadata curada guarda dois sinais por serviço: se a migração exige reescrita
(``requires_refactoring``) e o grau de aprisionamento ao fornecedor
(``vendor_lock_in_level``). Deles derivamos, **de forma determinística no
backend**, a classe de migração no vocabulário 6R — o mesmo eixo que CAST
Highlight usa para pontuar "CloudReady":

    rehost      — sobe como está (lift-and-shift)
    replatform  — ajustes de plataforma, sem reescrever a aplicação
    refactor    — exige reescrita/reengenharia

Sem isto a IA inventaria a dificuldade da migração, que é exatamente o que a
regra "IA narra resultados já calculados" proíbe. Aqui ela recebe a classe
pronta e só a explica.

Função pura, sem I/O e sem citar nome de nuvem ou de serviço.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_ALTO_LOCK_IN = frozenset({"medium", "high"})

# Esforço relativo por classe — ordena o plano de migração sem inventar prazo.
_ESFORCO = {"rehost": "baixo", "replatform": "medio", "refactor": "alto"}


@dataclass(frozen=True, slots=True)
class MigrationExpectation:
    classe: str          # rehost | replatform | refactor
    esforco: str         # baixo | medio | alto
    lock_in: str | None  # low | medium | high
    requer_refatoracao: bool

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "classe_migracao": self.classe,
            "esforco": self.esforco,
            "requer_refatoracao": self.requer_refatoracao,
        }
        if self.lock_in:
            d["lock_in"] = self.lock_in
        return d


def derive_expectation(metadata: dict[str, Any] | None) -> MigrationExpectation | None:
    """Deriva a expectativa a partir da metadata curada, ou None se não houver.

    None é honesto: melhor a IA saber que não temos o dado do que receber um
    palpite disfarçado de fato.
    """
    if not metadata:
        return None

    tem_refactor = "requires_refactoring" in metadata
    lock_in_raw = metadata.get("vendor_lock_in_level")
    lock_in = str(lock_in_raw).strip().lower() if lock_in_raw else None
    if not tem_refactor and not lock_in:
        return None

    requer = bool(metadata.get("requires_refactoring"))
    if requer:
        classe = "refactor"
    elif lock_in in _ALTO_LOCK_IN:
        classe = "replatform"
    else:
        classe = "rehost"

    return MigrationExpectation(
        classe=classe,
        esforco=_ESFORCO[classe],
        lock_in=lock_in,
        requer_refatoracao=requer,
    )
