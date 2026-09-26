"""Contrato dos normalizadores de inventário.

Um normalizador por formato. Cada envio cria instâncias novas: o normalizador
acumula os arquivos do seu formato (``consumir``) e só no fim resolve as
referências entre eles (``finalizar``), porque a VM pode citar uma subrede que
está noutro arquivo.

A coleta ativa (I10) vai produzir os mesmos documentos que o cliente exporta
hoje e reusar estes normalizadores sem mudança.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from app.domain.entities.topologia import Inventario
from app.infrastructure.inventory_ingestion.leitura import ArquivoLido


class Normalizador(Protocol):
    formato: ClassVar[str]

    @classmethod
    def reconhece(cls, objeto: dict[str, Any] | None, primeiro: dict[str, Any] | None) -> bool:
        """``objeto`` quando o arquivo é um objeto JSON; ``primeiro`` quando é
        uma sequência (o primeiro item). Olha o conteúdo, nunca o nome."""
        ...

    def consumir(self, arquivo: ArquivoLido) -> None: ...

    def finalizar(self) -> Inventario: ...
