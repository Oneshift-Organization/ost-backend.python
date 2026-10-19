"""Geração de Terraform — `/v1/terraform-generations`.

Superfície própria, atrás de `TERRAFORM_GENERATION_ENABLED` (default `false`).
Com a flag desligada as rotas respondem 404, como se não existissem — mesmo
padrão do `AGENT_FLOW_ENABLED`, e pela mesma razão: o código pode estar
deployado e continuar invisível até alguém decidir virar o interruptor.

**Esta API não provisiona nada.** Não recebe, não guarda e não usa credencial de
nuvem; não roda `plan` nem `apply`. Ela escreve arquivos.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, escopo_de_migracao_uc
from app.api.v1.routes.migration_scope import ERROS_DO_ESCOPO, traduzir_erro_do_escopo
from app.core.config import get_settings
from app.domain.entities.analysis_job import AnalysisStatus, agora_utc
from app.domain.services.terraform_topologia.escopo import recortar_ao_escopo
from app.domain.services.topologia.escopo import Escolha
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.terraform_generation_job import (
    TerraformGenerationJobModel,
)
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.database.sinal_de_vida import sinal_de_vida
from app.infrastructure.terraform.archive import montar_zip
from app.infrastructure.terraform.renderers import arquivos_esperados
from app.schemas.terraform import (
    EscopoIn,
    ResumoValidacao,
    StatusGeracao,
    TerraformGenerationIn,
    TerraformGenerationOut,
)
from app.use_cases.terraform.build_blueprint import validate_migration_strategy
from app.use_cases.terraform.generate_terraform import gerar_pacote

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terraform-generations", tags=["terraform"])


def _erro(
    status_code: int, codigo: str, mensagem: str, *, retryable: bool, **extra
) -> HTTPException:
    """Mesmo envelope das outras superfícies: `codigo`, `mensagem`, `retryable`.

    Em português e com `mensagem` — não `message`. É o contrato que já está em
    produção nas rotas de agente, e divergir aqui faria o consumidor tratar dois
    formatos.
    """
    corpo: dict = {"codigo": codigo, "mensagem": mensagem, "retryable": retryable}
    corpo.update(extra)
    return HTTPException(status_code=status_code, detail=corpo)


def _exigir_flag() -> None:
    if not get_settings().terraform_generation_enabled:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "GERACAO_TERRAFORM_DESLIGADA",
            "a geração de Terraform não está habilitada neste ambiente",
            retryable=False,
        )


@router.post(
    "",
    response_model=TerraformGenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Inicia a geração do pacote Terraform a partir de uma análise",
)
async def iniciar_geracao(
    entrada: TerraformGenerationIn,
    user: CurrentUser,
    session: DbSession,
    background: BackgroundTasks,
) -> TerraformGenerationOut:
    _exigir_flag()
    user_uuid = UUID(user.sub)

    analise = (
        await session.execute(
            select(AnalysisJobModel).where(
                AnalysisJobModel.job_id == entrada.analysis_job_id,
                AnalysisJobModel.user_id == user_uuid,
            )
        )
    ).scalars().first()

    # 404 e não 403: revelar que a análise existe mas é de outro usuário já é
    # vazamento. Mesma política do resto da API.
    if analise is None:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "ANALISE_NAO_ENCONTRADA",
            f"análise {entrada.analysis_job_id} não existe para este usuário",
            retryable=False,
        )

    if analise.status != AnalysisStatus.COMPLETED:
        raise _erro(
            status.HTTP_409_CONFLICT,
            "ANALISE_NAO_CONCLUIDA",
            f"a análise está em {analise.status}; aguarde COMPLETED",
            retryable=True,
            status_analise=analise.status,
        )

    if not analise.llm_input_payload:
        raise _erro(
            status.HTTP_409_CONFLICT,
            "PAYLOAD_AUSENTE",
            "a análise não tem payload determinístico gravado; refaça a análise",
            retryable=False,
        )

    payload_analise = dict(analise.llm_input_payload)
    if entrada.escopo is not None:
        payload_analise = await _recortar(
            user_uuid, entrada.analysis_job_id, entrada.escopo, payload_analise, session
        )
    try:
        validate_migration_strategy(payload_analise, entrada.target_provider.value)
    except ValueError as error:
        raise _erro(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ESTRATEGIA_REQUER_REVISAO",
            str(error),
            retryable=False,
        ) from error

    job = TerraformGenerationJobModel(
        generation_id=uuid4(),
        user_id=user_uuid,
        analysis_job_id=entrada.analysis_job_id,
        report_id=analise.report_id,
        status=StatusGeracao.PENDING,
        target_provider=entrada.target_provider.value,
        region=entrada.region,
        project_name=entrada.project_name,
        request_payload=entrada.model_dump(mode="json"),
        created_at=agora_utc(),
    )
    session.add(job)
    await session.commit()

    background.add_task(
        _executar_geracao,
        job.generation_id,
        payload_analise,
        entrada,
        get_sessionmaker(),
    )

    return _para_saida(job)


@router.get(
    "/{generation_id}",
    response_model=TerraformGenerationOut,
    status_code=status.HTTP_200_OK,
    summary="Estado da geração, com premissas e o que ficou de fora",
)
async def obter_geracao(
    generation_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> TerraformGenerationOut:
    _exigir_flag()
    job = await _buscar(generation_id, UUID(user.sub), session)
    return _para_saida(job)


@router.get(
    "/{generation_id}/download",
    status_code=status.HTTP_200_OK,
    summary="Baixa o pacote Terraform em ZIP",
    response_class=Response,
)
async def baixar_pacote(
    generation_id: UUID,
    user: CurrentUser,
    session: DbSession,
) -> Response:
    _exigir_flag()
    job = await _buscar(generation_id, UUID(user.sub), session)

    if job.status != StatusGeracao.COMPLETED or not job.generated_files:
        raise _erro(
            status.HTTP_409_CONFLICT,
            "GERACAO_NAO_CONCLUIDA",
            f"a geração está em {job.status}",
            retryable=job.status
            in (StatusGeracao.PENDING, StatusGeracao.PROCESSING),
        )

    conteudo = montar_zip(
        dict(job.generated_files), raiz=f"{job.project_name}-terraform"
    )
    return Response(
        content=conteudo,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="oneshift-terraform-{generation_id}.zip"'
            )
        },
    )


# ── execução ────────────────────────────────────────────────────────────


async def _executar_geracao(
    generation_id: UUID,
    payload_analise: dict,
    entrada: TerraformGenerationIn,
    sessionmaker,
) -> None:
    """Roda em BackgroundTask, com sessão própria.

    A sessão da requisição já foi fechada quando isto executa — usar a mesma
    daria erro de conexão fechada, e a falha ficaria invisível porque ninguém
    espera por esta função.
    """
    sinal = sinal_de_vida(
        sessionmaker,
        TerraformGenerationJobModel,
        TerraformGenerationJobModel.generation_id,
        generation_id,
        [StatusGeracao.PENDING.value, StatusGeracao.PROCESSING.value],
        intervalo_segundos=get_settings().job_heartbeat_seconds,
        agora=agora_utc,
    )
    async with sinal, sessionmaker() as session:
        job = (
            await session.execute(
                select(TerraformGenerationJobModel).where(
                    TerraformGenerationJobModel.generation_id == generation_id
                )
            )
        ).scalars().first()
        if job is None:
            logger.error("terraform_job_sumiu generation_id=%s", generation_id)
            return

        job.status = StatusGeracao.PROCESSING
        job.started_at = agora_utc()
        await session.commit()

        try:
            # Em thread: `gerar_pacote` chama `terraform fmt` por subprocess, e
            # `subprocess.run` bloqueia o event loop. BackgroundTasks roda no
            # mesmo processo que atende requisição — segurar o loop por até 30s
            # travaria TODA a API, não só esta geração.
            resultado = await asyncio.to_thread(
                gerar_pacote, payload_analise, entrada
            )
            job.blueprint = resultado.blueprint.model_dump(mode="json")
            job.generated_files = resultado.arquivos
            job.validation_result = resultado.validacao.model_dump()
            job.status = StatusGeracao.COMPLETED
            logger.info(
                "terraform_gerado generation_id=%s recursos=%d premissas=%d",
                generation_id,
                len(resultado.blueprint.resources),
                len(resultado.blueprint.assumptions),
            )
        except Exception as erro:
            job.status = StatusGeracao.FAILED
            # Mensagem curta e sem stack: o texto vai para a tela do cliente.
            job.error = f"{type(erro).__name__}: {erro}"[:500]
            logger.exception("terraform_falhou generation_id=%s", generation_id)

        job.updated_at = agora_utc()
        await session.commit()


async def _recortar(
    user_id: UUID,
    job_id: UUID,
    escopo: EscopoIn,
    payload: dict[str, Any],
    session: DbSession,
) -> dict[str, Any]:
    """O payload só com o escopo do plano de migração (T5). Os erros são os do plano."""
    if not get_settings().inventory_enabled:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "INVENTARIO_DESLIGADO",
            "o Terraform do escopo usa o inventário, que não está habilitado neste ambiente",
            retryable=False,
        )
    escolha = Escolha(
        aplicacoes=frozenset(escopo.aplicacoes),
        servicos=frozenset(escopo.servicos),
        ambiente=escopo.ambiente or None,
        incluir_grupos=frozenset(escopo.incluir_grupos),
        somente_os_servicos=escopo.somente_os_servicos,
    )
    try:
        _, recorte = await escopo_de_migracao_uc(session).recorte(user_id, job_id, escolha)
    except ERROS_DO_ESCOPO as e:
        raise traduzir_erro_do_escopo(e) from e
    return recortar_ao_escopo(payload, recorte)


# ── utilidades ──────────────────────────────────────────────────────────


async def _buscar(
    generation_id: UUID, user_id: UUID, session
) -> TerraformGenerationJobModel:
    job = (
        await session.execute(
            select(TerraformGenerationJobModel).where(
                TerraformGenerationJobModel.generation_id == generation_id,
                TerraformGenerationJobModel.user_id == user_id,
            )
        )
    ).scalars().first()
    if job is None:
        raise _erro(
            status.HTTP_404_NOT_FOUND,
            "GERACAO_NAO_ENCONTRADA",
            f"geração {generation_id} não existe para este usuário",
            retryable=False,
        )
    return job


def _para_saida(job: TerraformGenerationJobModel) -> TerraformGenerationOut:
    blueprint = job.blueprint or {}
    validacao = job.validation_result
    return TerraformGenerationOut(
        generation_id=job.generation_id,
        analysis_job_id=job.analysis_job_id,
        report_id=job.report_id,
        status=StatusGeracao(job.status),
        target_provider=job.target_provider,
        region=job.region,
        project_name=job.project_name,
        validation=ResumoValidacao(**validacao) if validacao else None,
        assumptions=blueprint.get("assumptions") or [],
        unsupported_items=blueprint.get("unsupported_items") or [],
        warnings=blueprint.get("warnings") or [],
        available_files=(
            sorted(job.generated_files) if job.generated_files else list(arquivos_esperados())
        )
        if job.status == StatusGeracao.COMPLETED
        else [],
        error=job.error,
        created_at=job.created_at,
    )
