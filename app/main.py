from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository


async def _expirar_jobs_travados() -> int:
    """Encerra jobs de análise presos em estado ativo.

    A análise roda em BackgroundTasks no próprio processo web: um deploy, um
    OOM ou um restart no meio da execução deixa o job ativo para sempre. Como o
    índice único parcial impede um segundo job ativo para o mesmo report, sem
    esta varredura o report ficaria bloqueado permanentemente — o reaper é a
    válvula que torna a idempotência segura.
    """
    limite = timedelta(minutes=get_settings().analysis_job_timeout_minutes)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        return await PgAnalysisJobRepository(session).expirar_travados(limite)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    log = get_logger(__name__)
    log.info("app_startup", env=get_settings().app_env)
    # Jobs órfãos do processo anterior: quem estava executando quando o processo
    # caiu não tem mais dono.
    try:
        expirados = await _expirar_jobs_travados()
        if expirados:
            log.warning("jobs_travados_expirados", quantidade=expirados)
    except Exception:  # startup nunca falha por causa do reaper
        log.exception("reaper_falhou_no_startup")
    yield


class ErroInternoMiddleware(BaseHTTPMiddleware):
    """Converte exceção não tratada em resposta HTTP normal.

    Duas coisas dependem disso. A primeira é o CORS: uma resposta 500 gerada pelo
    ServerErrorMiddleware nunca atravessa o CORSMiddleware, então o browser não vê
    `Access-Control-Allow-Origin` e culpa o CORS por um erro que é de servidor —
    o front fica caçando configuração de origem enquanto o defeito está no
    endpoint. A segunda é o contrato de erro: um agente precisa distinguir
    RE-TENTE de DESISTA, e 500 sem corpo estruturado não diz nada.

    O `X-Request-ID` do cliente é ecoado para casar o erro visto no browser com a
    linha de log do servidor.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception:
            request_id = request.headers.get("X-Request-ID", "")
            get_logger(__name__).exception(
                "erro_nao_tratado",
                path=request.url.path,
                method=request.method,
                request_id=request_id,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": {
                        "codigo": "ERRO_INTERNO",
                        "mensagem": "erro interno ao processar a requisição",
                        # Erro de servidor é transitório até prova em contrário:
                        # o cliente pode repetir. O que não pode é repetir em loop
                        # apertado — daí o campo existir em vez de ficar implícito.
                        "retryable": True,
                    }
                },
                headers={"X-Request-ID": request_id} if request_id else None,
            )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cloud Intelligence Platform — API",
        version="0.1.0",
        # Docs ligadas por padrão em TODOS os ambientes, produção inclusive: o
        # time do agente/MCP precisa do contrato da API para integrar, e um
        # OpenAPI publicado é a forma menos ambígua de entregar isso. Expõe rotas
        # e schemas, não dados — a auth continua valendo em cada endpoint.
        # `DOCS_ENABLED=false` desliga sem alterar código.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    if "*" in settings.cors_origins:
        raise RuntimeError("CORS allow_origins=['*'] is forbidden")

    # ORDEM IMPORTA. Starlette aplica middlewares na ordem inversa do registro:
    # o último adicionado é o mais EXTERNO. A captura precisa ficar por DENTRO do
    # CORS, senão o 500 não passa pelo CORSMiddleware e sai sem
    # `Access-Control-Allow-Origin` — o browser então reporta "blocked by CORS
    # policy" para o que na verdade é um erro interno, escondendo a causa real.
    # Um `@app.exception_handler(Exception)` NÃO resolve: o FastAPI o instala no
    # ServerErrorMiddleware, que continua sendo a camada mais externa de todas.
    app.add_middleware(ErroInternoMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(api_router)
    return app


app = create_app()
