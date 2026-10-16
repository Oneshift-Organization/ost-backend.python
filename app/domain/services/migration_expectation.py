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

from collections.abc import Iterable
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
    # "curada"  = metadata do próprio serviço
    # "classe"  = herdada de serviços curados do MESMO service_type
    fonte: str = "curada"
    # Classe cujos curados discordam do lock-in: sem nota, só os níveis vistos.
    lock_in_faixa: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "classe_migracao": self.classe,
            "esforco": self.esforco,
            "requer_refatoracao": self.requer_refatoracao,
        }
        if self.lock_in:
            d["lock_in"] = self.lock_in
        if self.lock_in_faixa:
            d["lock_in_faixa"] = list(self.lock_in_faixa)
        if self.fonte != "curada":
            d["fonte"] = self.fonte
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


_LOCK_RANK = {"low": 1, "medium": 2, "high": 3}


def learn_class_defaults(
    curated: Iterable[tuple[str, dict[str, Any] | None]],
) -> dict[str, MigrationExpectation]:
    """Aprende a expectativa TÍPICA de cada ``service_type`` a partir dos
    serviços que já têm metadata curada.

    A dificuldade de migração é propriedade da **classe** do serviço, não do
    produto: object storage é fácil em qualquer nuvem (API compatível), banco
    relacional gerenciado é difícil em qualquer nuvem (engine/versão/extensões).
    Em vez de enumerar 234 tipos à mão — o que seria outra esteira de manutenção
    — derivamos o padrão dos exemplos curados existentes, e ele melhora sozinho
    conforme a curadoria cresce.

    Agregação **conservadora**: se algum serviço curado daquele tipo exige
    reescrita, o tipo inteiro é tratado como `refactor`, e a classe sai do maior
    lock-in observado. Errar para o lado difícil não gera promessa de economia fácil.

    A **nota de lock-in** da classe só existe quando os curados do tipo concordam
    (lote v4, V4.4). Banco relacional tem RDS e Cloud SQL médios e Azure SQL alto: o
    maior dava "alto" ao Azure Database for PostgreSQL, motor aberto. Tipo misto sai
    sem `lock_in`, com `lock_in_faixa`, e a nota fica para a curadoria.

    Recebe pares ``(service_type, metadata)`` — função pura, sem I/O.
    """
    agregado: dict[str, dict[str, Any]] = {}
    for service_type, metadata in curated:
        if not service_type or not metadata:
            continue
        exp = derive_expectation(metadata)
        if exp is None:
            continue
        acc = agregado.setdefault(service_type, {"refac": False, "locks": set()})
        acc["refac"] = acc["refac"] or exp.requer_refatoracao
        if exp.lock_in in _LOCK_RANK:
            acc["locks"].add(exp.lock_in)

    defaults: dict[str, MigrationExpectation] = {}
    for service_type, acc in agregado.items():
        niveis = sorted(acc["locks"], key=_LOCK_RANK.__getitem__)
        base = derive_expectation(
            {
                "requires_refactoring": acc["refac"],
                "vendor_lock_in_level": niveis[-1] if niveis else None,
            }
        )
        if base is not None:
            unanime = len(niveis) <= 1
            defaults[service_type] = MigrationExpectation(
                classe=base.classe,
                esforco=base.esforco,
                lock_in=base.lock_in if unanime else None,
                requer_refatoracao=base.requer_refatoracao,
                fonte="classe",
                lock_in_faixa=() if unanime else tuple(niveis),
            )
    return defaults
