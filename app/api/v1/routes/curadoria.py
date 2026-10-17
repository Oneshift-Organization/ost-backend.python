"""Curadoria de nomes de serviço pela OneShift (fatura como fonte, etapa A, decisão 2.11).

As faturas trazem nomes que o DE-PARA não reconhece. A coleta junta esses nomes numa
fila, com uma linha do DE-PARA sugerida; a curadoria aprova (escolhendo a linha) ou
recusa. Aprovado vira apelido, e as análises novas passam a reconhecer o nome.

O DE-PARA é de todos os clientes: só os curadores da OneShift (`CURADORES`, e-mails)
decidem. A tela do Swagger (`/docs`) serve de console.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import _DEV_USER, CurrentUser, DbSession
from app.api.v1.envelope import erro
from app.core.config import get_settings
from app.domain.services.service_identity import service_key
from app.infrastructure.repositories.pg_service_name_suggestion_repo import (
    PgServiceNameSuggestionRepository,
)
from app.infrastructure.repositories.service_repositories import PGServiceComparisonRepository
from app.schemas.auth import UserClaims
from app.schemas.curadoria import ColetaOut, DecisaoIn, SugestaoDeNomeOut
from app.use_cases.curadoria.coletar_sugestoes_de_nome import ColetarSugestoesDeNomeUseCase

router = APIRouter(prefix="/admin", tags=["curadoria"])

_DEV_SUB = _DEV_USER.sub


def _lista(valor: str) -> list[str]:
    return [item.strip() for item in valor.split(",") if item.strip()]


def _curador(user: CurrentUser) -> UserClaims:
    """Só e-mail da lista de curadores. Em dev sem autenticação, o usuário local."""
    settings = get_settings()
    curadores = {email.lower() for email in _lista(settings.curadores)}
    sem_auth_local = not settings.auth_enabled and user.sub == _DEV_SUB
    if sem_auth_local or (user.email and user.email.lower() in curadores):
        return user
    raise erro(
        status.HTTP_403_FORBIDDEN,
        "ACESSO_DE_CURADORIA",
        "só a curadoria da OneShift decide os nomes do DE-PARA",
        retryable=False,
    )


Curador = Annotated[UserClaims, Depends(_curador)]


@router.get("/service-name-suggestions", response_model=list[SugestaoDeNomeOut])
async def listar_sugestoes(
    _: Curador,
    session: DbSession,
    status_da_sugestao: Literal["pendente", "aprovada", "recusada"] | None = "pendente",
    provider: str | None = None,
) -> list[SugestaoDeNomeOut]:
    """A fila, dos nomes que mais clientes trazem para os que menos trazem."""
    linhas = await PgServiceNameSuggestionRepository(session).listar(status_da_sugestao, provider)
    return [SugestaoDeNomeOut.model_validate(linha) for linha in linhas]


@router.post(
    "/service-name-suggestion-collections",
    response_model=ColetaOut,
    status_code=status.HTTP_201_CREATED,
)
async def coletar(_: Curador, session: DbSession) -> ColetaOut:
    """Lê as faturas (fora as de teste) e atualiza a fila."""
    excluidos = [UUID(u) for u in _lista(get_settings().curadoria_usuarios_excluidos)]
    resultado = await ColetarSugestoesDeNomeUseCase(
        PgServiceNameSuggestionRepository(session), PGServiceComparisonRepository(session)
    ).execute(excluidos)
    await session.commit()
    return ColetaOut(
        lidos=resultado.lidos, na_fila=resultado.na_fila, com_sugestao=resultado.com_sugestao
    )


@router.patch("/service-name-suggestions/{sugestao_id}", response_model=SugestaoDeNomeOut)
async def decidir(
    sugestao_id: UUID, corpo: DecisaoIn, curador: Curador, session: DbSession
) -> SugestaoDeNomeOut:
    """Aprovar (com a linha do DE-PARA), recusar ou devolver à fila."""
    repo = PgServiceNameSuggestionRepository(session)
    sugestao = await repo.obter(sugestao_id)
    if sugestao is None:
        raise erro(
            status.HTTP_404_NOT_FOUND,
            "SUGESTAO_NAO_ENCONTRADA",
            "sugestão não existe",
            retryable=False,
        )
    alvo = None
    if corpo.status == "aprovada":
        linhas = await PGServiceComparisonRepository(session).list_by_provider(sugestao.provider)
        por_chave = {
            service_key(sugestao.provider, linha.service_name): linha.service_name
            for linha in linhas
        }
        alvo = por_chave.get(service_key(sugestao.provider, corpo.alvo or ""))
        if alvo is None:
            raise erro(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "ALVO_FORA_DO_DE_PARA",
                f"aprovar exige uma linha do DE-PARA de {sugestao.provider} em `alvo`",
                retryable=False,
            )
    decidida = await repo.decidir(
        sugestao,
        status=corpo.status,
        alvo=alvo,
        por=curador.email or curador.sub,
        observacao=corpo.observacao,
    )
    await session.commit()
    return SugestaoDeNomeOut.model_validate(decidida)
