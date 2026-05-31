# CLAUDE.md

Guia rápido para Claude Code neste repo. **Para qualquer tarefa de produto,
arquitetura, API ou parsers, consulte primeiro [`docs/`](docs/README.md)** —
fonte de verdade versionada, otimizada para leitura por IA.

## Projeto

Backend Python do **Cloud Intelligence Platform** (Challenge Quave + OCI +
FIAP 2026). Plataforma que extrai billing multi-cloud (AWS/GCP/Azure/OCI),
cota infra equivalente em cada provedor, calcula score de portabilidade e
gera recomendações de migração para CFOs/controllers.

Stack resumida: Python 3.12+ · FastAPI 0.111 · SQLAlchemy 2.0 async · asyncpg ·
Pydantic v2 · Alembic · Pandas 2.2 + Pandera · Supabase Auth (JWT RS256) ·
structlog. PostgreSQL 16 em OCI compartilhado (não rodar local).

> **Decisão MVP (2026-05-18):** Celery + RabbitMQ + `cloud_clients/*` **diferidos**.
> Async no caminho crítico via **FastAPI BackgroundTasks** (parsing CSV típico
> < 30s). Isolamento por **`user_id` apenas** — `tenant_id` adiado. Estado vivo
> em `ost-docs.backend.plan/workflow_master.md` (gitignored).

## Onde achar cada coisa

| Tarefa | Documento canônico |
|---|---|
| Entender produto / 5 módulos / cronograma | [`docs/product.md`](docs/product.md) |
| Regras de arquitetura / Clean Arch / proibições | [`docs/architecture.md`](docs/architecture.md) |
| Schema do banco / DDL / `raw_payload` conventions | [`docs/data-model.md`](docs/data-model.md) |
| Parsers de cloud / adicionar novo / política `global_zero` | [`docs/parsers.md`](docs/parsers.md) |
| Mock Engine MVP / matching hardware / troubleshooting | [`docs/refactor/mvp-mock-engine.md`](docs/refactor/mvp-mock-engine.md) |
| Capacity Pool Model / FOCUS alignment | [`docs/refactor/focus-alignment.md`](docs/refactor/focus-alignment.md) |
| WorkloadPool entities / aggregator / pool design | [`docs/refactor/focus-alignment.md`](docs/refactor/focus-alignment.md#2-the-solution-capacity-pool-model) |
| Convenções de rota / paginação / serialização | [`docs/api-conventions.md`](docs/api-conventions.md) |
| Catálogo `/v1/billing-files` | [`docs/endpoints/billing.md`](docs/endpoints/billing.md) |
| Rodar local / venv / podman / troubleshooting | [`docs/dev-setup.md`](docs/dev-setup.md) |
| Índice geral + fluxo de leitura | [`docs/README.md`](docs/README.md) |

**Foundations externas** (raiz do workspace, **não** editar):
[`cloud_billing_analyzer_prompt.md`](../cloud_billing_analyzer_prompt.md) (visão
completa do produto) + [`fase1_parser_prompt.md`](../fase1_parser_prompt.md)
(spec original do Módulo 1). Sintetizados nos docs versionados acima.

## Comandos

Setup completo em [`docs/dev-setup.md`](docs/dev-setup.md). Atalhos:

```bash
python3.14 -m venv .venv     # ou 3.12 — pyproject exige >=3.12
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

## Arquitetura (resumo)

**Clean Architecture** com regra de dependência unidirecional:

```
api/ + infrastructure/ → use_cases/ → domain/
```

- `domain/` é **puro** (zero framework).
- `use_cases/` orquestra; recebe deps via DI.
- `infrastructure/repositories/` implementa interfaces `I*`.
- `infrastructure/file_ingestion/parsers/` tem **7 parsers** (AWS CUR, AWS Cost
  Explorer, AWS Invoice, Azure, GCP BigQuery, GCP Cost Table, OCI), detecção
  por **conteúdo**.
- `domain/services/equivalence_matcher.py` — matching cross-cloud por hardware
  (vcpus/ram_gb), sem if/else em Python. Cobertura 100%.
- `schemas/` Pydantic na borda HTTP; **nunca** cruza para `domain/`.
- `api/deps.py` monta UCs via DI; rotas declaram `use_case: UploadBillingFileUC`.

**Detalhes completos:** [`docs/architecture.md`](docs/architecture.md) (folder
layout, stack versions, regras inegociáveis, proibições, "por que cada regra
existe").

## Fluxo Fase 1 (estado atual)

1. `POST /v1/billing-files` → grava `BillingReport(QUEUED)` + dispara
   `IngestBillingFileUseCase` em **BackgroundTask**. Retorna 202 + `job_id`.
2. Pipeline: `detect_and_parse` (7 parsers, por conteúdo) → `deduplicate(line_hash)`
   → `validate_normalized_df` (Pandera) → `bulk_insert` em chunks de 1000
   (idempotente: `delete_by_report` + INSERT) → `BuildBillingEnrichedUseCase`
   (filtra StatementTotal/InvoiceTotal/Tax, cruza com ontologia + catálogo de SKUs,
   persiste `billing_enriched` + `unmapped_billing_lines`).
   Status `PROCESSING → DONE | FAILED`. Falha grava em **session limpa**.
3. `GET /v1/billing-files` (lista) + `GET /v1/billing-files/{job_id}` (status) +
   `GET /v1/billing-files/{job_id}/items` (drill-down paginado, `cost DESC`).
4. Isolamento por `user_id` em **todas** queries → 404 esconde existência.
5. Próximas fases: F1G dashboard · F2 cotação cross-cloud · F3 score
   portabilidade · F5 IA (regras calculam, LLM narra).

**Detalhes por endpoint:** [`docs/endpoints/billing.md`](docs/endpoints/billing.md).
**Como cada parser funciona + política `global_zero` do Cost Explorer:**
[`docs/parsers.md`](docs/parsers.md).

## Pontos críticos (cheat-sheet)

- **DB / sessão:** [`docs/architecture.md#banco-de-dados`](docs/architecture.md#banco-de-dados).
  Resumo: `get_db_session()` é a única forma de obter `AsyncSession`;
  `NUMERIC(18,6)` em monetário; `JSONB` em raw payload; nunca `FLOAT`.
- **Auth:** Supabase emite JWT RS256; backend só valida via
  [`app/core/security.py`](app/core/security.py). Dev bypass via
  `AUTH_ENABLED=false`. Hard-blocked em produção.
- **Mensageria (diferida):** MVP usa `BackgroundTasks`; pós-MVP Celery+RabbitMQ.
  Ver [`docs/architecture.md#mensageria`](docs/architecture.md#mensageria).
- **Política `cost=0`** no parser Cost Explorer: ativos emitem zeros pontuais
  (drift); global-zeros viram 1 summary por mês com `raw_payload.scope =
  "global_zero"`. Spec completa em
  [`docs/parsers.md#política-de-emissão-de-cost0-cost-explorer`](docs/parsers.md#política-de-emissão-de-cost0-cost-explorer).
- **Exclusão de linhas financeiras:** `StatementTotal` / `InvoiceTotal` (AWS
  Invoice) e `Cost type = "tax"` (GCP Cost Table) são filtrados no parser e
  no enriquecimento. Não excluir duplica custos totais (ex: $208k → $104k).
- **Provider case-insensitive:** `cloud_sku_catalog` usa `'AWS'`, `'GCP'`;
  `report.provider.value` usa `'aws'`, `'gcp'`. Buscas usam `.ilike()` no
  `PgCloudSkuRepository` para evitar mismatch.
- **Assertividade do SKU matching:** classificação linha→SKU usa a **Matching
  Ladder** (L0–L5, data-driven via `sku_classification_rules`, sem `if/else` por
  cloud). Gate de acurácia: `sku_coverage_ratio ≥ 95%` (orphan ≤ 5%). Spec em
  [`docs/refactor/sku-accuracy-spec.md`](docs/refactor/sku-accuracy-spec.md).

## Regras Inegociáveis (resumo)

Lista completa + justificativa em [`docs/architecture.md`](docs/architecture.md#proibições-absolutas).
Cheat-sheet:

- Valores monetários: `Decimal(str(valor))` em Python, `NUMERIC(18,6)` no PG. **Nunca `float`**.
- IA narra resultados já calculados; **nunca** invocar LLM para fazer matemática.
- `domain/` zero framework imports.
- `AsyncSession` só via `get_db_session()` — nunca instanciar em use case.
- `allow_origins=["*"]` proibido em qualquer ambiente.
- REST sem verbo na URL; rotas em `kebab-case` plural.
- Nunca commitar direto em `main`/`develop`. Branches: `feat/<card>`, `fix/<card>`, `hotfix/<card>`.
- Nunca bypassar pre-commit com `--no-verify`.
- Toda nova cloud = novo parser herdando de `BaseParser`, registrado em
  `detector.PARSERS`. Checklist completo em
  [`docs/parsers.md#como-adicionar-um-parser-novo`](docs/parsers.md#como-adicionar-um-parser-novo).
- Não editar arquivos em `ost-docs.backend.plan/` (brain local-only do arquiteto)
  nem `ost-docs.mkdocs/` (repo separado, read-only).

## Convenções (resumo)

Detalhes em [`docs/architecture.md#convenções-de-nomenclatura`](docs/architecture.md#convenções-de-nomenclatura).

- `snake_case` para arquivos/módulos/funções; `PascalCase` para classes; `I`-prefix para interfaces.
- Use cases: `<Verbo>UseCase` com `execute()` async recebendo dataclass `<Verbo>Input`.
- Schemas: `<Acao>Request` / `<Recurso>Response`.
- Migrations: `<verbo>_<contexto>` em snake_case.
- Cobertura ≥80% em `domain/` e `use_cases/` é gate de merge.

## Camadas de documentação

| Camada | Localização | Editável daqui? |
|---|---|---|
| **Versionada (fonte de verdade pública)** | [`docs/`](docs/README.md) | ✅ sim — editar junto com o código |
| **Brain local-only do arquiteto** | `ost-docs.backend.plan/` (gitignored) | ❌ não — apenas consultar |
| **Docs corporativos (PRD/TRD)** | `ost-docs.mkdocs/` (repo separado) | ❌ não — apenas consultar |
| **Prompts foundation** | `../cloud_billing_analyzer_prompt.md`, `../fase1_parser_prompt.md` (raiz workspace) | ❌ não — apenas consultar |

Para qualquer dúvida sobre **o que** está implementado e **como** funciona,
começar por [`docs/README.md`](docs/README.md) (índice + fluxo de leitura).
