"""Análise de topologia do inventário (PLANO_INVENTARIO_API, RI5 a RI12)."""

from app.domain.services.topologia.analise import analisar_topologia, fingerprint
from app.domain.services.topologia.declaracoes import (
    SEM_DECLARACOES,
    Declaracoes,
    DependenciaDeclarada,
)

__all__ = [
    "SEM_DECLARACOES",
    "Declaracoes",
    "DependenciaDeclarada",
    "analisar_topologia",
    "fingerprint",
]
