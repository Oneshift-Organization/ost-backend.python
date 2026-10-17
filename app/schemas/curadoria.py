"""Curadoria de nomes de serviço (decisão 2.11): contrato das rotas de administração."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SugestaoDeNomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    nome_na_fatura: str
    alvo_sugerido: str | None = Field(None, description="linha do DE-PARA mais parecida")
    semelhanca: float | None = None
    faturas: int = Field(description="quantas faturas trazem o nome (fora as de teste)")
    clientes: int = Field(description="quantos clientes distintos")
    status: Literal["pendente", "aprovada", "recusada"]
    alvo: str | None = Field(None, description="linha do DE-PARA aprovada")
    decidido_por: str | None = None
    decidido_em: datetime | None = None
    observacao: str | None = None
    visto_em: datetime


class DecisaoIn(BaseModel):
    """Aprovar exige o alvo, uma linha do DE-PARA do mesmo provedor."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["aprovada", "recusada", "pendente"]
    alvo: str | None = Field(None, max_length=200)
    observacao: str | None = Field(None, max_length=500)


class ColetaOut(BaseModel):
    lidos: int = Field(description="nomes de serviço lidos nas faturas")
    na_fila: int = Field(description="nomes que o DE-PARA não reconhece")
    com_sugestao: int = Field(description="desses, com uma linha do DE-PARA parecida")
