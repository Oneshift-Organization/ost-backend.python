# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projeto

Backend Python do **Cloud Intelligence Platform** (Challenge Quave + OCI + FIAP 2026) — plataforma que extrai billing de AWS/GCP/Azure/OCI, calcula cotações cross-cloud e score de portabilidade, e gera recomendações de migração para CFOs/controllers.

Stack: Python 3.12 · FastAPI 0.111 · SQLAlchemy 2.0 async · asyncpg · Pydantic v2 · Alembic · Pandas 2.2 + Pandera · Supabase Auth (JWT RS256) · structlog. PostgreSQL 16 fica em OCI compartilhado (não rodar local).

> **Decisão MVP (2026-05-18):** Celery + RabbitMQ + `cloud_clients/*` **diferidos**. Async no caminho crítico via **FastAPI BackgroundTasks** (parsing CSV típico < 30s). Isolamento por **`user_id` apenas** — `tenant_id` adiado. Ver `ost-docs.backend.plan/workflow_master.md` para o estado vivo.

## Comandos

Setup (venv usa Python 3.12, **não** 3.14 — `pyvenv.cfg` aponta para `/usr/bin/python3.12`):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env.dev   # config.py lê .env.dev por default
```

Rodar API (dev):

```bash
uvicorn app.main:app --reload --reload-dir app
```

Worker Celery (DIFERIDO no MVP — manter para retomar pós-MVP):

```bash
# celery -A app.infrastructure.messaging.celery_app.celery_app worker -Q billing.ingest -l info
```

Migrations (Alembic já lê `DATABASE_URL` via `get_settings()` — `sqlalchemy.url` no `alembic.ini` fica vazio de propósito):

```bash
alembic revision --autogenerate -m "<verbo_snake_case>"
alembic upgrade head
alembic downgrade -1
```

Qualidade:

```bash
ruff check . && ruff format --check .
mypy app                                   # strict mode
pytest                                     # roda com -ra -q --cov=app
pytest app/tests/unit/domain/test_money.py::test_<nome>   # teste único
pytest -k "billing and not integration"    # filtro
```

## Arquitetura

**Clean Architecture com regra de dependência unidirecional:**
`api/` + `infrastructure/` → `use_cases/` → `domain/`

- `domain/` é **puro**: zero imports de FastAPI, SQLAlchemy ou Pydantic (exceto VOs). Define entidades, value objects (`Money`, `CloudProvider`) e interfaces (`IBillingRepository`, etc.).
- `use_cases/` orquestra regras de negócio; recebe dependências via DI no `__init__`, nunca instancia ORM/SDK direto. Input em dataclass frozen (`UploadBillingFileInput`).
- `infrastructure/repositories/` implementa as interfaces `domain/repositories/I*`. Pattern `Pg<Entity>Repository(session)`.
- `infrastructure/file_ingestion/parsers/` implementa um parser por provedor (`aws_parser.py`, `azure_parser.py`, `gcp_parser.py`, `oci_parser.py`) que herda de `BaseParser`. Detecção é por **conteúdo** (não extensão) via `detector.detect_parser`. Adicionar nova cloud = novo parser + entrada em `detector.PARSERS`.
- `infrastructure/cloud_clients/` (DIFERIDO no MVP) implementaria `IBillingClient` (Protocol) por provedor — reativar pós-MVP junto com Celery + cloud-credentials Fernet.
- `schemas/` (Pydantic) é a borda HTTP — **nunca** cruza para `domain/`; converter no use case ou na rota.
- `api/deps.py` monta os use cases via `Annotated[..., Depends(...)]` e expõe `DbSession`, `UploadBillingFileUC`, etc. Rotas declaram `use_case: UploadBillingFileUC` direto.

**Fluxo Fase 1 implementado (billing — MVP):**
1. `POST /v1/billing-files` (autenticado, multipart): grava `BillingReport(QUEUED)` e dispara `IngestBillingFileUseCase` em **BackgroundTask**. Retorna 202 + `job_id`.
2. Pipeline async: `detect_and_parse` (4 parsers, decisão por conteúdo) → `deduplicate(line_hash)` → `validate_normalized_df` (Pandera) → `PgBillingItemRepository.bulk_insert` em chunks de 1000 (idempotente: `delete_by_report` + INSERT). Atualiza status `PROCESSING → DONE | FAILED`.
3. `GET /v1/billing-files/{job_id}` faz polling do status (filtrado por `user_id` — usuário A nunca vê job do usuário B).
4. Próximas fases: F1G dashboard + F2 cotação cross-cloud + F3 score portabilidade + F5 IA (regras calculam, LLM narra).

**DB / sessão:**
- `get_db_session()` (`infrastructure/database/session.py`) é a única forma autorizada de obter `AsyncSession`. Faz `commit` no sucesso, `rollback` na exceção, `close` no finally. `pool_size=10`, `expire_on_commit=False`.
- Modelos SQLAlchemy herdam de `Base` (`infrastructure/database/base.py`). Para Alembic detectar mudanças, todo novo modelo precisa ser importado em `infrastructure/database/models/__init__.py`.
- Tipos obrigatórios: `UUID` PK, `JSONB` para payloads brutos, **`NUMERIC(18,6)`** para todo valor monetário (decisão MVP — uniformizado em `billing_items`) — **nunca `FLOAT`**.

**Mensageria (DIFERIDA no MVP):**
- MVP usa `BackgroundTasks` do FastAPI. A task cria sua própria `AsyncSession` via `get_sessionmaker()` (a request session já fechou).
- Pós-MVP: reativar Celery + RabbitMQ AMQP. DLX nativa por queue: `billing.ingest` → `billing.ingest.dlq`. Tasks idempotentes, `acks_late=True`, retry com backoff exponencial.

**Auth:**
- Supabase emite JWT RS256; backend só **valida** via `app/core/security.py` (`PyJWKClient` com cache JWKS). Audience: `authenticated`. Backend não emite token nem armazena senha.

## Regras Inegociáveis (de `ost-docs.backend.plan/architecture_rules.md`)

- Valores monetários: `Decimal(str(valor))` em Python, `NUMERIC` no PG. Nunca `float`.
- IA narra resultados já calculados; **nunca** invocar LLM para fazer matemática.
- `domain/` zero framework imports.
- `AsyncSession` só via `get_db_session()` — nunca instanciar em use case.
- `allow_origins=["*"]` proibido em qualquer ambiente.
- REST sem verbo na URL; rotas em `kebab-case` plural (`/v1/billing-files`, `/v1/cloud-credentials`).
- Nunca commitar direto em `main`/`develop` (pre-commit `no-commit-to-branch` bloqueia). Branches: `feat/<card>`, `fix/<card>`, `hotfix/<card>`.
- Nunca bypassar pre-commit com `--no-verify` (inclui `detect-secrets`).
- Toda nova cloud vai como parser isolado em `infrastructure/file_ingestion/parsers/<provider>_parser.py` herdando de `BaseParser`, registrado em `detector.PARSERS`. (Pós-MVP: também um subpacote em `infrastructure/cloud_clients/<provider>/` implementando `IBillingClient`.)
- Não excluir arquivos em `ost-docs.backend.plan/` sem autorização explícita.

## Convenções

- `snake_case` para arquivos/módulos/funções; `PascalCase` para classes; `I`-prefix para interfaces (`IBillingClient`).
- Use cases: `<Verbo>UseCase` com método `execute()` assíncrono recebendo dataclass `<Verbo>Input`.
- Schemas: `<Acao>Request` / `<Recurso>Response`.
- Migrations: `<verbo>_<contexto>` em snake_case (`add_billing_reports_table`). `post_write_hooks` formata com ruff automaticamente.
- Cobertura ≥80% em `domain/` e `use_cases/` é gate de merge. TDD obrigatório para cálculos de billing, equivalências cross-cloud, regras de recomendação e normalização.

## Documentação interna

Plano arquitetural vivo e contexto de produto: `ost-docs.backend.plan/` (`architecture_rules.md`, `fluxo-BETA.md`, `workflow_master.md`, `error_ledger.md`). Consultar antes de mudanças estruturais.
