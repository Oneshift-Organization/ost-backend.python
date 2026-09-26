"""Consultar, declarar, analisar e apagar um inventário (RF4 a RF6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.entities.billing_report import JobStatus
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.entities.topologia import Inventario, Recurso, TipoDeRecurso
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.topologia import analisar_topologia
from app.domain.services.topologia.modelo import AnaliseDeTopologia
from app.use_cases.inventory.declaracoes import chaves_de_recurso, para_dominio


class InventarioNaoProcessadoError(ConflictError):
    """O inventário existe, mas ainda não está DONE."""


async def obter_do_usuario(
    repo: IInventoryRepository, inventario_id: UUID, user_id: UUID
) -> InventarioEnviado:
    inventario = await repo.obter(inventario_id, user_id)
    if inventario is None:
        raise NotFoundError(f"inventário {inventario_id} não existe para este usuário")
    return inventario


def exigir_processado(inventario: InventarioEnviado) -> None:
    if inventario.status is not JobStatus.DONE:
        raise InventarioNaoProcessadoError(
            f"o inventário está em {inventario.status.value}; aguarde done"
        )


@dataclass(frozen=True, slots=True)
class PaginaDeRecursos:
    itens: list[Recurso]
    total: int


class ListarRecursosUseCase:
    def __init__(self, repo: IInventoryRepository) -> None:
        self._repo = repo

    async def execute(
        self,
        inventario_id: UUID,
        user_id: UUID,
        *,
        tipo: TipoDeRecurso | None,
        limite: int,
        deslocamento: int,
    ) -> PaginaDeRecursos:
        inventario = await obter_do_usuario(self._repo, inventario_id, user_id)
        exigir_processado(inventario)
        itens, total = await self._repo.listar_recursos(
            inventario_id, user_id, tipo=tipo, limite=limite, deslocamento=deslocamento
        )
        return PaginaDeRecursos(itens, total)


@dataclass(frozen=True, slots=True)
class TopologiaCalculada:
    inventario: InventarioEnviado
    conteudo: Inventario
    analise: AnaliseDeTopologia


class AnalisarTopologiaUseCase:
    """Topologia com as declarações vigentes, calculada na hora (RF5).

    Não guarda o resultado: é função pura do conteúdo (que não muda) e das
    declarações (que mudam). Quem precisa congelar, como a análise, grava o que
    usou.
    """

    def __init__(self, repo: IInventoryRepository) -> None:
        self._repo = repo

    async def execute(self, inventario_id: UUID, user_id: UUID) -> TopologiaCalculada:
        inventario = await obter_do_usuario(self._repo, inventario_id, user_id)
        exigir_processado(inventario)
        conteudo = await self._repo.carregar_conteudo(inventario)
        analise = analisar_topologia(conteudo, para_dominio(inventario.declaracoes))
        return TopologiaCalculada(inventario, conteudo, analise)


class DeclararUseCase:
    """Substitui as declarações (PUT). Referência a recurso que não existe é
    recusada com a lista: um erro de digitação viraria declaração ignorada em
    silêncio."""

    def __init__(self, repo: IInventoryRepository) -> None:
        self._repo = repo

    async def execute(
        self, inventario_id: UUID, user_id: UUID, declaracoes: dict[str, Any]
    ) -> InventarioEnviado:
        inventario = await obter_do_usuario(self._repo, inventario_id, user_id)
        exigir_processado(inventario)
        referidas = chaves_de_recurso(declaracoes)
        existentes = await self._repo.chaves_existentes(inventario_id, user_id, referidas)
        faltando = sorted(referidas - existentes)
        if faltando:
            raise ValidationError(
                "recursos que não existem neste inventário: " + ", ".join(faltando[:20])
            )
        inventario.declarar(declaracoes)
        await self._repo.atualizar(inventario)
        return inventario


class ApagarInventarioUseCase:
    def __init__(self, repo: IInventoryRepository) -> None:
        self._repo = repo

    async def execute(self, inventario_id: UUID, user_id: UUID) -> None:
        if not await self._repo.apagar(inventario_id, user_id):
            raise NotFoundError(f"inventário {inventario_id} não existe para este usuário")
