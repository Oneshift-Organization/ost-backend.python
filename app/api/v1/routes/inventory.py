"""Inventário — `/v1/inventories` (PLANO_INVENTARIO_API, I5).

O cliente exporta o inventário da conta dele com o comando oficial do provedor
(AWS Config, Cloud Asset Inventory, Resource Graph) ou no formato canônico, e
sobe aqui. **Nenhuma credencial de nuvem entra**: a API do provedor é chamada
pelo cliente, não por nós (decisão D1).

Atrás de `INVENTORY_ENABLED` (default `false`): desligada, as rotas respondem 404
como se não existissem, igual à geração de Terraform.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.entities.billing_report import JobStatus
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.entities.topologia import TipoDeRecurso
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.inventory_ingestion.processar import (
    ResultadoDaNormalizacao,
    normalizar_arquivos,
)
from app.infrastructure.repositories.pg_inventory_repo import PgInventoryRepository
from app.schemas.inventory import (
    DeclaracoesIn,
    InventoryDetail,
    InventorySummary,
    InventoryUploadResponse,
    ResourceItem,
    ResourcesPage,
    TopologyResponse,
)
from app.use_cases.inventory.ciclo import (
    ProcessarInventarioInput,
    ProcessarInventarioUseCase,
    RegistrarInventarioInput,
    RegistrarInventarioUseCase,
)
from app.use_cases.inventory.consultas import (
    AnalisarTopologiaUseCase,
    ApagarInventarioUseCase,
    DeclararUseCase,
    InventarioNaoProcessadoError,
    ListarRecursosUseCase,
    obter_do_usuario,
)
from app.use_cases.inventory.serializacao import serializar_topologia

router = APIRouter(prefix="/inventories", tags=["inventories"])
_log = get_logger(__name__)

# A detecção é pelo conteúdo; a extensão só barra o que certamente não é
# inventário (planilha, PDF).
_EXTENSOES = frozenset({".json", ".ndjson", ".jsonl", ".gz", ".zip"})
_BLOCO = 1 << 20
_NOME_SEGURO = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_POR_PAGINA = 500


def _erro(
    status_code: int, codigo: str, mensagem: str, *, retryable: bool = False
) -> HTTPException:
    """Mesmo envelope das rotas de Terraform e de agente."""
    return HTTPException(
        status_code=status_code,
        detail={"codigo": codigo, "mensagem": mensagem, "retryable": retryable},
    )


def _exigir_flag() -> None:
    if not get_settings().inventory_enabled:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "INVENTARIO_DESLIGADO",
            "o inventário não está habilitado neste ambiente",
        )


def _usuario(user: CurrentUser) -> UUID:
    try:
        return UUID(user.sub)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "id de usuário inválido") from e


def _traduzir(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return _erro(status.HTTP_404_NOT_FOUND, "INVENTARIO_NAO_ENCONTRADO", str(exc))
    if isinstance(exc, InventarioNaoProcessadoError):
        return _erro(
            status.HTTP_409_CONFLICT, "INVENTARIO_NAO_PROCESSADO", str(exc), retryable=True
        )
    if isinstance(exc, ValidationError):
        return _erro(status.HTTP_422_UNPROCESSABLE_ENTITY, "DECLARACAO_INVALIDA", str(exc))
    raise exc


# ── envio ───────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=InventoryUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Envia arquivos de inventário (processados em segundo plano)",
)
async def enviar_inventario(
    user: CurrentUser,
    session: DbSession,
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File(...)],
) -> InventoryUploadResponse:
    _exigir_flag()
    settings = get_settings()
    user_id = _usuario(user)
    if len(files) > settings.inventory_max_files:
        raise _erro(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ARQUIVOS_DEMAIS",
            f"no máximo {settings.inventory_max_files} arquivos por envio",
        )
    limite = settings.max_upload_size_mb * 1024 * 1024
    pasta = Path(tempfile.mkdtemp(prefix="inventario-"))
    arquivos: list[tuple[str, Path]] = []
    try:
        for i, arquivo in enumerate(files):
            nome = Path(arquivo.filename or f"arquivo-{i}").name
            if Path(nome).suffix.lower() not in _EXTENSOES:
                raise _erro(
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    "EXTENSAO_NAO_ACEITA",
                    f"{nome}: aceitos {', '.join(sorted(_EXTENSOES))}",
                )
            destino = pasta / f"{i:02d}-{_NOME_SEGURO.sub('_', nome)[-100:]}"
            await _gravar_em_blocos(arquivo, destino, limite, nome)
            arquivos.append((nome, destino))
    except HTTPException:
        shutil.rmtree(pasta, ignore_errors=True)
        raise

    inventario = await RegistrarInventarioUseCase(PgInventoryRepository(session)).execute(
        RegistrarInventarioInput(user_id=user_id, arquivos=[n for n, _ in arquivos])
    )
    await session.commit()
    background.add_task(_processar_isolado, user_id, inventario.id, arquivos, pasta)
    return InventoryUploadResponse(
        inventory_id=inventario.id,
        status=inventario.status.value,
        source_files=inventario.arquivos,
        created_at=inventario.created_at,
    )


async def _gravar_em_blocos(arquivo: UploadFile, destino: Path, limite: int, nome: str) -> None:
    """Grava em blocos de 1 MB: 20 arquivos de 50 MB não passam pela memória."""
    tamanho = 0
    with destino.open("wb") as saida:
        while bloco := await arquivo.read(_BLOCO):
            tamanho += len(bloco)
            if tamanho > limite:
                raise _erro(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "ARQUIVO_GRANDE_DEMAIS",
                    f"{nome}: passa de {limite // (1024 * 1024)} MB",
                )
            saida.write(bloco)


def _normalizar(arquivos: list[tuple[str, Path]]) -> ResultadoDaNormalizacao:
    """Com os limites padrão (RNF4)."""
    return normalizar_arquivos(arquivos)


async def _processar_isolado(
    user_id: UUID, inventario_id: UUID, arquivos: list[tuple[str, Path]], pasta: Path
) -> None:
    """BackgroundTask com sessão própria (a da requisição já fechou). A falha é
    gravada numa sessão limpa, como no billing."""
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            uc = ProcessarInventarioUseCase(PgInventoryRepository(session), _normalizar)
            try:
                await uc.execute(
                    ProcessarInventarioInput(
                        user_id=user_id, inventario_id=inventario_id, arquivos=arquivos
                    )
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                _log.exception("inventario_falhou", inventory_id=str(inventario_id))
                await _gravar_falha(sessionmaker, user_id, inventario_id, exc)
    finally:
        await asyncio.to_thread(shutil.rmtree, pasta, True)


async def _gravar_falha(sessionmaker, user_id: UUID, inventario_id: UUID, exc: Exception) -> None:  # type: ignore[no-untyped-def]
    try:
        async with sessionmaker() as session:
            repo = PgInventoryRepository(session)
            inventario = await repo.obter(inventario_id, user_id)
            if inventario is None:
                return
            # A mensagem da ingestão já é para o cliente; o resto sai com o tipo.
            texto = (
                str(exc)
                if exc.__class__.__name__.startswith(("Inventario", "Limite"))
                else (f"{type(exc).__name__}: {exc}")
            )
            inventario.marcar_falha(texto)
            await repo.atualizar(inventario)
            await session.commit()
    except Exception:
        _log.exception("falha_ao_gravar_falha", inventory_id=str(inventario_id))


# ── consulta ────────────────────────────────────────────────────────────


def _resumo(i: InventarioEnviado) -> InventorySummary:
    return InventorySummary(
        inventory_id=i.id,
        status=i.status.value,
        provider=i.provedor,
        source_files=i.arquivos,
        resource_count=i.recursos,
        captured_at=i.capturado_em,
        created_at=i.created_at,
    )


def _detalhe(i: InventarioEnviado, por_tipo: dict[str, int]) -> InventoryDetail:
    return InventoryDetail(
        **_resumo(i).model_dump(),
        detected_formats=i.formatos,
        captured_at_estimated=i.capturado_em_estimado,
        relation_count=i.relacoes,
        rule_count=i.regras,
        counts_by_kind=por_tipo,
        counts=i.contagens,
        fingerprint=i.fingerprint,
        declarations=i.declaracoes,
        declarations_updated_at=i.declaracoes_atualizadas_em,
        error=i.erro,
    )


@router.get("", response_model=list[InventorySummary], summary="Inventários do usuário")
async def listar_inventarios(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InventorySummary]:
    _exigir_flag()
    inventarios = await PgInventoryRepository(session).listar(
        _usuario(user), limite=limit, deslocamento=offset
    )
    return [_resumo(i) for i in inventarios]


@router.get("/{inventory_id}", response_model=InventoryDetail, summary="Estado e contagens")
async def obter_inventario(
    inventory_id: UUID, user: CurrentUser, session: DbSession
) -> InventoryDetail:
    _exigir_flag()
    repo = PgInventoryRepository(session)
    try:
        inventario = await obter_do_usuario(repo, inventory_id, _usuario(user))
    except NotFoundError as e:
        raise _traduzir(e) from e
    por_tipo = (
        await repo.contar_por_tipo(inventory_id, inventario.user_id)
        if inventario.status is JobStatus.DONE
        else {}
    )
    return _detalhe(inventario, por_tipo)


@router.get(
    "/{inventory_id}/resources",
    response_model=ResourcesPage,
    summary="Recursos normalizados, paginados pela chave",
)
async def listar_recursos(
    inventory_id: UUID,
    user: CurrentUser,
    session: DbSession,
    *,
    kind: TipoDeRecurso | None = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_POR_PAGINA)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResourcesPage:
    _exigir_flag()
    try:
        pagina = await ListarRecursosUseCase(PgInventoryRepository(session)).execute(
            inventory_id, _usuario(user), tipo=kind, limite=limit, deslocamento=offset
        )
    except (NotFoundError, InventarioNaoProcessadoError) as e:
        raise _traduzir(e) from e
    return ResourcesPage(
        items=[
            ResourceItem(
                key=r.chave,
                kind=r.tipo.value,
                native_type=r.tipo_nativo,
                name=r.nome,
                account=r.conta,
                region=r.regiao,
                zone=r.zona,
                state=r.estado.value,
                attributes=dict(r.atributos),
                tags=dict(r.tags),
            )
            for r in pagina.itens
        ],
        total=pagina.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{inventory_id}/topology",
    response_model=TopologyResponse,
    summary="Unidades, dependências, ondas, criticidade, riscos e ociosos",
)
async def obter_topologia(
    inventory_id: UUID, user: CurrentUser, session: DbSession
) -> TopologyResponse:
    _exigir_flag()
    try:
        calculada = await AnalisarTopologiaUseCase(PgInventoryRepository(session)).execute(
            inventory_id, _usuario(user)
        )
    except (NotFoundError, InventarioNaoProcessadoError) as e:
        raise _traduzir(e) from e
    return TopologyResponse(
        inventory_id=inventory_id,
        provider=calculada.inventario.provedor,
        captured_at=calculada.inventario.capturado_em,
        **serializar_topologia(calculada.analise),
    )


@router.put(
    "/{inventory_id}/declarations",
    response_model=InventoryDetail,
    summary="Substitui as declarações (criticidade, chaves de tag, dependências, ferramentas)",
)
async def declarar(
    inventory_id: UUID, declaracoes: DeclaracoesIn, user: CurrentUser, session: DbSession
) -> InventoryDetail:
    _exigir_flag()
    repo = PgInventoryRepository(session)
    try:
        inventario = await DeclararUseCase(repo).execute(
            inventory_id, _usuario(user), declaracoes.model_dump(mode="json")
        )
    except (NotFoundError, InventarioNaoProcessadoError, ValidationError) as e:
        raise _traduzir(e) from e
    await session.commit()
    return _detalhe(inventario, await repo.contar_por_tipo(inventory_id, inventario.user_id))


@router.delete(
    "/{inventory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Apaga o inventário e tudo que deriva dele",
)
async def apagar(inventory_id: UUID, user: CurrentUser, session: DbSession) -> Response:
    _exigir_flag()
    try:
        await ApagarInventarioUseCase(PgInventoryRepository(session)).execute(
            inventory_id, _usuario(user)
        )
    except NotFoundError as e:
        raise _traduzir(e) from e
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
