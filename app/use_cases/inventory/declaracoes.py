"""Declarações: o JSON guardado no inventário ↔ ``Declaracoes`` do domínio.

A borda HTTP valida o formato (``schemas/inventory.py``); aqui só se traduz.
O JSON guardado é exatamente o que o cliente enviou depois de validado, para
que o `GET` devolva o que foi declarado, não uma reinterpretação.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.services.topologia import Declaracoes, DependenciaDeclarada
from app.domain.services.topologia.declaracoes import PREFIXO_APLICACAO
from app.domain.services.topologia.modelo import Acoplamento, Nivel


def para_dominio(bruto: Mapping[str, Any] | None) -> Declaracoes:
    if not bruto:
        return Declaracoes()
    criticidade = bruto.get("criticidade") or {}
    return Declaracoes(
        chave_aplicacao=bruto.get("chave_aplicacao"),
        chave_ambiente=bruto.get("chave_ambiente"),
        criticidade_recursos={k: Nivel(v) for k, v in (criticidade.get("recursos") or {}).items()},
        criticidade_aplicacoes={
            k: Nivel(v) for k, v in (criticidade.get("aplicacoes") or {}).items()
        },
        dependencias=tuple(
            DependenciaDeclarada(
                de=d["de"],
                para=d["para"],
                acoplamento=Acoplamento(d.get("acoplamento") or "fraco"),
                motivo=d.get("motivo"),
            )
            for d in bruto.get("dependencias") or []
        ),
        ferramentas=tuple(bruto.get("ferramentas") or []),
    )


def chaves_de_recurso(bruto: Mapping[str, Any]) -> set[str]:
    """As referências a recurso (não a aplicação) que precisam existir."""
    refs: set[str] = set((bruto.get("criticidade") or {}).get("recursos") or {})
    for d in bruto.get("dependencias") or []:
        refs |= {d["de"], d["para"]}
    refs |= set(bruto.get("ferramentas") or [])
    return {r for r in refs if not r.startswith(PREFIXO_APLICACAO)}
