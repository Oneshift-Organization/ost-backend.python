"""O que o cliente declara sobre o inventário (RF6, RI6, RI7, RI10).

A configuração não diz criticidade, nem que uma aplicação chama outra por DNS,
nem que aquela VM é o monitoramento. O cliente diz, e a declaração tem
precedência sobre o que a análise deduz — sempre com a origem registrada.

Referência a unidade: a chave de um recurso, ou ``aplicacao:<valor da tag>``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.services.topologia.modelo import Acoplamento, Nivel

PREFIXO_APLICACAO = "aplicacao:"


@dataclass(frozen=True, slots=True)
class DependenciaDeclarada:
    de: str
    para: str
    acoplamento: Acoplamento = Acoplamento.FRACO
    motivo: str | None = None


@dataclass(frozen=True, slots=True)
class Declaracoes:
    # None = detectar; "" = desligar o agrupamento.
    chave_aplicacao: str | None = None
    chave_ambiente: str | None = None
    criticidade_recursos: Mapping[str, Nivel] = field(default_factory=dict)
    criticidade_aplicacoes: Mapping[str, Nivel] = field(default_factory=dict)
    dependencias: tuple[DependenciaDeclarada, ...] = ()
    ferramentas: tuple[str, ...] = ()


SEM_DECLARACOES = Declaracoes()
