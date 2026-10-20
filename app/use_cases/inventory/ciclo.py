"""Registrar e processar um inventário (RF1 a RF3).

O processamento recebe a função de normalização por injeção: o caso de uso não
sabe de formato de arquivo, e a coleta ativa da I10 vai entrar pelo mesmo
caminho entregando documentos em vez de arquivos.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.entities.topologia import Inventario
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.topologia import SEM_DECLARACOES, fingerprint


class ResultadoDeNormalizacao(Protocol):
    @property
    def inventario(self) -> Inventario: ...

    @property
    def formatos(self) -> dict[str, str]: ...


Normalizar = Callable[[list[tuple[str, Path]]], ResultadoDeNormalizacao]


@dataclass(frozen=True, slots=True)
class RegistrarInventarioInput:
    user_id: UUID
    arquivos: list[str]


class RegistrarInventarioUseCase:
    def __init__(self, repo: IInventoryRepository) -> None:
        self._repo = repo

    async def execute(self, entrada: RegistrarInventarioInput) -> InventarioEnviado:
        inventario = InventarioEnviado(user_id=entrada.user_id, arquivos=list(entrada.arquivos))
        await self._repo.salvar(inventario)
        return inventario


@dataclass(frozen=True, slots=True)
class ProcessarInventarioInput:
    user_id: UUID
    inventario_id: UUID
    arquivos: list[tuple[str, Path]]


class ProcessarInventarioUseCase:
    """QUEUED → PROCESSING → DONE. A falha é gravada por quem chama, numa
    sessão limpa: a desta já está suja quando a exceção chega."""

    def __init__(self, repo: IInventoryRepository, normalizar: Normalizar) -> None:
        self._repo = repo
        self._normalizar = normalizar

    async def execute(self, entrada: ProcessarInventarioInput) -> InventarioEnviado:
        inventario = await self._repo.obter(entrada.inventario_id, entrada.user_id)
        if inventario is None:
            raise NotFoundError(f"inventário {entrada.inventario_id} não existe")
        inventario.marcar_processando()
        await self._repo.atualizar(inventario)

        # Em thread: ler e normalizar é CPU (json.loads de dezenas de MB), e a
        # BackgroundTask roda no mesmo processo que atende requisição.
        resultado = await asyncio.to_thread(self._normalizar, entrada.arquivos)
        conteudo = resultado.inventario
        await self._repo.gravar_conteudo(inventario.id, inventario.user_id, conteudo)
        inventario.marcar_concluido(
            provedor=conteudo.provedor,
            formatos=dict(resultado.formatos),
            capturado_em=conteudo.capturado_em,
            recursos=len(conteudo.recursos),
            relacoes=len(conteudo.relacoes),
            regras=len(conteudo.regras),
            contagens=dict(conteudo.contagens),
            # Sem declarações: o fingerprint do conteúdo não muda quando o
            # cliente declara algo depois.
            fingerprint=fingerprint(conteudo, SEM_DECLARACOES),
        )
        await self._repo.atualizar(inventario)
        return inventario
