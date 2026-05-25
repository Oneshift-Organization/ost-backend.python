# Architecture

Regras técnicas inegociáveis e estrutura de pastas do backend.

> **Audiência:** dev que vai escrever código no repo, agente de IA que vai
> propor mudanças estruturais. Leia antes de qualquer mudança que cruze
> fronteira de camada.
>
> **Origem:** este documento é a forma versionada e pública de
> `ost-docs.backend.plan/architecture_rules.md` (gitignored, brain local-only
> do arquiteto). Quando houver divergência, **este arquivo é a fonte de verdade
> publicada**; o brain pode estar mais à frente em discussões em andamento.

## Stack (versões mínimas)

| Camada | Tecnologia | Versão |
|---|---|---|
| Linguagem | Python | **3.12** (compatível com 3.14; venv atual roda 3.14.4) |
| Framework HTTP | FastAPI | 0.111 |
| ASGI server (dev) | Uvicorn | 0.30 |
| ASGI server (prod) | Gunicorn + UvicornWorker | 22+ |
| ORM | SQLAlchemy async | 2.0 |
| Migrations | Alembic | 1.13 |
| Driver PG | asyncpg | 0.29 |
| Validação HTTP | Pydantic v2 | 2.7+ |
| Validação DataFrame | Pandera | 0.20 |
| DataFrame engine | Pandas | 2.2 |
| Auth | PyJWT[crypto] (Supabase JWKS) | 2.8 |
| Logs | structlog | 24.1 |
| Mensageria (pós-MVP) | Celery + RabbitMQ AMQP | 5.4 / 3.13 |
| Database | PostgreSQL | **16** (instância dev em OCI compartilhada; **não rodar local**) |
| Test runner | pytest + pytest-asyncio | 8 / 0.23 |

> A versão "alvo" do `pyproject.toml` é `python_requires = ">=3.12"`; `mypy`
> roda em `python_version = "3.12"` (strict). O venv atual usa 3.14.4 — ver
> [`dev-setup.md`](dev-setup.md) para recriar.

## Clean Architecture — regra de dependência

**Sentido único:**

```
api/  +  infrastructure/   →   use_cases/   →   domain/
```

- [`domain/`](../app/domain/) é **puro**: zero imports de FastAPI, SQLAlchemy
  ou Pydantic (exceto Value Objects internos). Define entidades, VOs (`Money`,
  `CloudProvider`) e interfaces (`I*Repository`).
- [`use_cases/`](../app/use_cases/) orquestra regras de negócio. Recebe deps
  via DI no `__init__`, **nunca instancia ORM/SDK direto**. Input em dataclass
  frozen (`UploadBillingFileInput`).
- [`infrastructure/repositories/`](../app/infrastructure/repositories/)
  implementa as interfaces `I*Repository`. Pattern `Pg<Entity>Repository(session)`.
- [`infrastructure/file_ingestion/parsers/`](../app/infrastructure/file_ingestion/parsers/)
  implementa um parser por provedor herdando de `BaseParser`. Detecção é
  **por conteúdo**, não extensão. Adicionar nova cloud = novo parser + entrada
  em `detector.PARSERS`. Ver [`parsers.md`](parsers.md).
- [`infrastructure/cloud_clients/`](../app/infrastructure/) (DIFERIDO no MVP)
  implementará `IBillingClient` por provedor — reativar pós-MVP junto com
  Celery + cloud-credentials Fernet.
- [`schemas/`](../app/schemas/) (Pydantic) é a **borda HTTP** — **nunca** cruza
  para `domain/`; conversão acontece no use case ou na rota.
- [`api/deps.py`](../app/api/deps.py) monta os use cases via
  `Annotated[..., Depends(...)]` e expõe `DbSession`, `UploadBillingFileUC`,
  etc. Rotas declaram `use_case: UploadBillingFileUC` direto.

## Estrutura de pastas (canônica)

```
app/
├── api/
│   ├── deps.py             # DI: DbSession, CurrentUser, *UC types
│   └── v1/
│       ├── router.py       # /v1 prefix; inclui billing + health
│       └── routes/
│           ├── billing.py  # /v1/billing-files*
│           └── health.py   # /v1/health
├── core/
│   ├── config.py           # pydantic-settings (lê .env.dev)
│   ├── security.py         # verify_jwt (Supabase JWKS, RS256)
│   ├── exceptions.py       # DomainError + subclasses
│   └── logging.py          # structlog JSON
├── domain/                 # PURO — zero framework imports
│   ├── entities/           # BillingReport, BillingItem
│   ├── value_objects/      # Money, CloudProvider, BillingRecord
│   └── repositories/       # IBillingReportRepository, IBillingItemRepository
├── use_cases/billing/
│   ├── upload_billing_file.py
│   ├── ingest_billing_file.py
│   ├── get_billing_job_status.py
│   ├── list_billing_reports.py
│   └── list_billing_items.py
├── infrastructure/
│   ├── database/
│   │   ├── base.py         # DeclarativeBase
│   │   ├── session.py      # get_db_session, get_sessionmaker
│   │   ├── models/         # ORM (billing_reports, billing_items)
│   │   └── migrations/     # Alembic
│   ├── repositories/       # Pg* implementations
│   └── file_ingestion/
│       ├── base_parser.py
│       ├── detector.py     # detect_parser por conteúdo
│       ├── normalizer.py   # df → items, deduplicate
│       ├── validator.py    # Pandera BillingNormalizedSchema
│       ├── loaders.py      # read_tabular, is_xlsx
│       └── parsers/        # aws_parser, aws_cost_explorer_parser, azure, gcp, oci
├── schemas/                # Pydantic — borda HTTP
│   ├── auth.py             # UserClaims
│   └── billing.py          # *Request / *Response
├── main.py                 # create_app() + CORS + lifespan
└── tests/                  # unit/ + integration/ + fixtures/
```

Pós-MVP, ressuscitar `infrastructure/cloud_clients/<provider>/` e
`infrastructure/messaging/` (Celery tasks + DLX).

## Banco de dados

- `get_db_session()` em [`infrastructure/database/session.py`](../app/infrastructure/database/session.py)
  é a **única forma autorizada** de obter `AsyncSession`. Commit no sucesso,
  rollback na exceção, close no finally. `pool_size=10`, `expire_on_commit=False`.
- Background tasks usam `get_sessionmaker()` (a session do request já fechou
  quando a task roda).
- **Tipos obrigatórios:** `UUID` PK, `JSONB` para payloads brutos,
  **`NUMERIC(18,6)`** para todo valor monetário. **Nunca `FLOAT`** em
  monetários.
- Modelos SQLAlchemy herdam de `Base` ([`infrastructure/database/base.py`](../app/infrastructure/database/base.py)).
  Para Alembic detectar mudanças, todo modelo novo precisa ser importado em
  [`infrastructure/database/models/__init__.py`](../app/infrastructure/database/models/__init__.py).
- DDL detalhado: [`data-model.md`](data-model.md).

## Autenticação

- Supabase emite JWT; backend só **valida** via [`app/core/security.py`](../app/core/security.py)
  (`PyJWKClient` com cache JWKS).
- Algoritmo `RS256`, audience `authenticated`.
- Claims usadas: `sub` (= `user_id` UUID), `email`, `role`.
- Backend **nunca emite token**, **nunca armazena senha**, **nunca toca refresh
  token**.
- **Dev bypass:** `AUTH_ENABLED=false` no `.env.dev` aceita qualquer request
  como usuário sintético `00000000-0000-0000-0000-000000000001`. Hard-blocked
  em produção (`get_current_user` retorna 500 se `is_production` e
  `auth_enabled=False`).

## CORS

- `allow_origins=["*"]` **proibido em qualquer ambiente**. `create_app()`
  levanta `RuntimeError` se detectar `*`.
- `allow_credentials=True` obrigatório (cookies SSR do Supabase).
- Headers permitidos: `Authorization`, `Content-Type`, `X-Request-ID`.
- Headers expostos: `X-Request-ID`.

## API REST

- Versionamento por path: `/v1/...`. Próxima quebra → `/v2`.
- Coleções em **kebab-case plural**: `/v1/billing-files`, `/v1/cloud-credentials`.
- Verbo HTTP expressa ação — **nunca verbo na URL** (`/createBillingFile`
  proibido).
- Resposta sempre tipada via `response_model=<Schema>Response`.
- Contratos detalhados: [`api-conventions.md`](api-conventions.md) e
  [`endpoints/billing.md`](endpoints/billing.md).

## Mensageria

- **MVP:** FastAPI `BackgroundTasks`. A task cria sua própria `AsyncSession`
  via `get_sessionmaker()`.
- **Pós-MVP:** Celery + RabbitMQ AMQP. DLX nativa por queue
  (`billing.ingest` → `billing.ingest.dlq`). Tasks idempotentes,
  `acks_late=True`, retry com backoff exponencial.
- **POO + interface**: cada provedor herda `IBillingClient` e implementa o SDK
  específico (ver [`product.md`](product.md#módulo-1--extração-de-billing-fase-1-mvp)).
- **Evolução futura:** Kafka **quando** auditoria com replay/fan-out for
  requisito real — afeta só `infrastructure/messaging/`.

## Convenções de nomenclatura

| Item | Convenção |
|---|---|
| Arquivos / módulos / funções / vars | `snake_case` |
| Classes | `PascalCase` |
| Interfaces (Protocol/ABC) | `PascalCase` com prefixo `I` (`IBillingRepository`) |
| Constantes, env vars | `SCREAMING_SNAKE_CASE` |
| Use Cases | `<Verbo>UseCase` (`UploadBillingFileUseCase`) |
| Schemas | `<Acao>Request` / `<Recurso>Response` |
| Value Objects | `PascalCase` sem sufixo (`Money`, `CloudProvider`) |
| Migrations | verbo descritivo em `snake_case` (`add_billing_reports_table`) |
| Testes | `test_<comportamento_esperado_em_linguagem_clara>` |

## Qualidade

- **Ruff** (lint + format) — config em [`pyproject.toml`](../pyproject.toml).
- **mypy strict** em `app/` (exceto migrations).
- **Cobertura ≥ 80%** em `domain/` e `use_cases/` é gate de merge.
- **TDD obrigatório** para: cálculo de billing, equivalências cross-cloud,
  regras de recomendação, normalização de payloads.
- **Test-after** aceitável para: CRUD, configs, wrappers de SDK (mockados).

## Segurança / Secrets

- `detect-secrets` no pre-commit bloqueia hardcoded credentials — **nunca**
  bypassar com `--no-verify`.
- `.env*` no `.gitignore` raiz; nunca commitar.
- Produção: HashiCorp Vault injeta env vars no container.
- `SUPABASE_SERVICE_ROLE_KEY` apenas backend; `SUPABASE_ANON_KEY` pode ser
  pública.
- `APP_ENV=production` desabilita `/docs`, `/redoc` e `/openapi.json`.
- Logs JSON via structlog com `X-Request-ID` para correlação.

## Docker (pós-MVP)

- Multi-stage: `base` → `development` → `builder` → `production`.
- Base: `python:3.12-slim`. **Nunca `alpine`** — `musl libc` quebra `asyncpg`.
- Layer cache: `COPY requirements.txt` antes de `COPY . .`.
- Usuário não-root `appuser` UID/GID **1001**.
- `HEALTHCHECK` aponta para `/v1/health` (que valida DB).
- Prod: `gunicorn --worker-class uvicorn.workers.UvicornWorker --workers 2 --timeout 120 --graceful-timeout 30`.
- PostgreSQL **não** é container local em dev — usa OCI compartilhado.

## CI/CD (pós-MVP)

- CI: ruff → mypy → pytest cov ≥ 80% → SonarQube → Semgrep → docker build → Trivy.
- CD: `develop` → `api-dev.oneshift.com.br`; `main` → `api.oneshift.com.br`.
- Estratégia: **Blue/Green** com smoke test antes de cutover.
- Branch protection: `develop` exige 1 aprovação; `main` exige 2.

## Proibições absolutas

Estas levantam exceção no review automaticamente. Sem discussão.

- ❌ Acessar ORM/SDK em `domain/`.
- ❌ Importar Pydantic schema em `domain/`.
- ❌ Instanciar `AsyncSession` fora de `get_db_session()`.
- ❌ `allow_origins=["*"]` no CORS.
- ❌ Verbo na URL REST.
- ❌ Commit em `main` / `develop` direto (pre-commit `no-commit-to-branch`
  bloqueia).
- ❌ Hardcoded secret no código.
- ❌ Container rodando como root.
- ❌ Imagem base `alpine` para Python.
- ❌ Bypass de pre-commit (`--no-verify`).
- ❌ Excluir arquivos em `ost-docs.backend.plan/` sem autorização explícita.
- ❌ `float` em qualquer cálculo monetário.
- ❌ LLM calculando — só narrando.

## Por que estas regras existem (não-óbvio)

Pensado para o leitor que quer ignorar uma regra "porque a vida é curta":

| Regra | Consequência de violar |
|---|---|
| Sem `float` em monetário | 1369.5165 vira `1369.5164999999999` após N somas → relatório de CFO com US$ a menos. |
| Sem `AsyncSession` fora do DI | Vazamento de conexão no pool + race conditions no event loop → API trava em produção sob carga. |
| Sem Pydantic em `domain/` | Acopla regra de negócio ao DTO HTTP → trocar response model quebra cálculo de score. |
| Sem `allow_origins=["*"]` | Permite qualquer site fazer request com cookies do usuário → CSRF + exfiltração de billing. |
| Sem `alpine` Python | `musl libc` quebra `asyncpg` em runtime no container, com erro críptico que custa 4h pra debugar. |
| LLM não calcula | LLM mente com confiança em aritmética; CFO toma decisão de US$ 100k baseada em soma errada. |

## Documentação relacionada

- [`product.md`](product.md) — visão de produto, 5 módulos, glossário.
- [`data-model.md`](data-model.md) — DDL, schema canônico, raw_payload conventions.
- [`parsers.md`](parsers.md) — guia por provider + como adicionar nova cloud.
- [`api-conventions.md`](api-conventions.md) — contratos transversais.
- [`endpoints/billing.md`](endpoints/billing.md) — catálogo F1.
- [`dev-setup.md`](dev-setup.md) — rodar local.
- `ost-docs.backend.plan/architecture_rules.md` (gitignored) — brain
  local-only do arquiteto, pode estar à frente deste arquivo em decisões
  em discussão.
