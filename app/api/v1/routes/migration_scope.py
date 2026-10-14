"""Aplicações da análise e plano de migração de um escopo (PLANO_ESCOPO_DE_MIGRACAO).

- `GET /v1/analyses/{job_id}/applications`: cada aplicação com os serviços que usa,
  cada serviço com as aplicações que o usam e os serviços de que depende;
- `POST /v1/analyses/{job_id}/migration-plans`: a escolha fechada pelas regras da
  topologia (o que vai junto, o que vai antes, quem fica para trás), cotada no
  destino. `?formato=xlsx` devolve o mesmo plano em planilha.

Só leitura: não grava, não chama LLM, não muda score nem preço. Atrás de
`INVENTORY_ENABLED`, como as rotas de inventário. Contrato:
docs/endpoints/migration-plans.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession, escopo_de_migracao_uc
from app.api.v1.envelope import erro
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.domain.services.topologia.escopo import Escolha, EscolhaInvalidaError
from app.infrastructure.exports.plano_xlsx import montar_xlsx
from app.schemas.migration_scope import (
    ApplicationsResponse,
    MigrationPlanRequest,
    MigrationPlanResponse,
)
from app.use_cases.analysis.consultar_acoplamento import (
    AnaliseFalhouError,
    AnaliseNaoConcluidaError,
)
from app.use_cases.analysis.escopo_de_migracao import (
    InventarioAusenteError,
    InventarioNaoCorrespondeError,
    TopologiaDivergenteError,
)
from app.use_cases.inventory.consultas import InventarioNaoProcessadoError

router = APIRouter(prefix="/analyses", tags=["analyses"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _exigir_flag() -> None:
    if not get_settings().inventory_enabled:
        raise erro(
            status.HTTP_404_NOT_FOUND,
            "INVENTARIO_DESLIGADO",
            "o inventário não está habilitado neste ambiente",
            retryable=False,
        )


# Erro do caso de uso → (status, código, repetir resolve?)
_TRADUCAO: dict[type[Exception], tuple[int, str, bool]] = {
    NotFoundError: (status.HTTP_404_NOT_FOUND, "ANALISE_NAO_ENCONTRADA", False),
    AnaliseNaoConcluidaError: (status.HTTP_409_CONFLICT, "ANALISE_NAO_CONCLUIDA", True),
    AnaliseFalhouError: (status.HTTP_409_CONFLICT, "ANALISE_FALHOU", False),
    InventarioAusenteError: (status.HTTP_409_CONFLICT, "INVENTARIO_AUSENTE", False),
    InventarioNaoProcessadoError: (status.HTTP_409_CONFLICT, "INVENTARIO_NAO_PROCESSADO", True),
    TopologiaDivergenteError: (status.HTTP_409_CONFLICT, "TOPOLOGIA_DIVERGENTE", False),
    InventarioNaoCorrespondeError: (status.HTTP_409_CONFLICT, "INVENTARIO_NAO_CORRESPONDE", False),
    EscolhaInvalidaError: (status.HTTP_422_UNPROCESSABLE_ENTITY, "ESCOLHA_INVALIDA", False),
}
_ERROS = tuple(_TRADUCAO)


def _traduzir(e: Exception) -> HTTPException:
    # Inventário de outra conta: as contas dos dois lados vão junto, para a tela dizer qual enviar
    extra = (
        {
            "contas_da_fatura": list(e.correspondencia.contas_da_fatura),
            "contas_do_inventario": list(e.correspondencia.contas_do_inventario),
        }
        if isinstance(e, InventarioNaoCorrespondeError)
        else {}
    )
    for tipo, (codigo_http, codigo, retryable) in _TRADUCAO.items():
        if isinstance(e, tipo):
            return erro(codigo_http, codigo, str(e), retryable=retryable, **extra)
    raise e


@router.get(
    "/{job_id}/applications",
    response_model=ApplicationsResponse,
    summary="Aplicações da conta, os serviços que cada uma usa e as dependências entre serviços",
)
async def get_applications(
    job_id: UUID, user: CurrentUser, session: DbSession
) -> ApplicationsResponse:
    """`409 INVENTARIO_AUSENTE` quando a análise foi feita sem inventário: a tela
    mostra o bloqueio e o upload."""
    _exigir_flag()
    try:
        ctx = await escopo_de_migracao_uc(session).aplicacoes(UUID(user.sub), job_id)
    except _ERROS as e:
        raise _traduzir(e) from e
    return ApplicationsResponse.de(ctx)


@router.post(
    "/{job_id}/migration-plans",
    response_model=MigrationPlanResponse,
    responses={200: {"content": {_XLSX: {}}, "description": "o plano, em JSON ou planilha"}},
    summary="Plano de migração de aplicações ou serviços, com o que vai junto e o custo no destino",
)
async def post_migration_plan(
    job_id: UUID,
    corpo: MigrationPlanRequest,
    user: CurrentUser,
    session: DbSession,
    formato: Literal["json", "xlsx"] = "json",
) -> MigrationPlanResponse | Response:
    """Escolher um serviço leva as aplicações inteiras que o usam; o plano diz quais
    serviços vieram junto e por quê. `422 ESCOLHA_INVALIDA` para escolha vazia ou
    com nome que a análise não tem."""
    _exigir_flag()
    escolha = Escolha(
        aplicacoes=frozenset(corpo.aplicacoes),
        servicos=frozenset(corpo.servicos),
        ambiente=corpo.ambiente or None,
        incluir_grupos=frozenset(corpo.incluir_grupos),
    )
    try:
        cotado = await escopo_de_migracao_uc(session).plano(UUID(user.sub), job_id, escolha)
    except _ERROS as e:
        raise _traduzir(e) from e
    plano = MigrationPlanResponse.de(cotado)
    if formato == "json":
        return plano
    agora = datetime.now(UTC)
    return Response(
        content=montar_xlsx(plano, job_id=str(job_id), gerado_em=agora),
        media_type=_XLSX,
        headers={
            "Content-Disposition": (
                f'attachment; filename="plano-de-migracao-{agora:%Y%m%d-%H%M}.xlsx"'
            )
        },
    )
