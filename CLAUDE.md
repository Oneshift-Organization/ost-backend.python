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
| Capacity Pool Model / FOCUS / WorkloadPool / aggregator | [`docs/capacity-pool-model.md`](docs/capacity-pool-model.md) |
| Exports detalhados (CUR 2.0, BigQuery, Azure, OCI) / base amortizada / fatos medidos / mocks | [`PLANO_CUR_BIGQUERY.md`](PLANO_CUR_BIGQUERY.md) |
| Inventário (AWS Config, Asset Inventory, Resource Graph) / topologia / dependências / ondas / Terraform com rede | [`PLANO_INVENTARIO_API.md`](PLANO_INVENTARIO_API.md) |
| Acoplamento (lock-in) por serviço / endpoint `service-couplings` / inventário como evidência | [`docs/endpoints/service-couplings.md`](docs/endpoints/service-couplings.md) · [`PLANO_LOCKIN_INVENTARIO.md`](PLANO_LOCKIN_INVENTARIO.md) |
| Convenções de rota / paginação / serialização | [`docs/api-conventions.md`](docs/api-conventions.md) |
| Catálogo `/v1/billing-files` | [`docs/endpoints/billing.md`](docs/endpoints/billing.md) |
| Catálogo `/v1/inventories` / export de cada nuvem / efeito na análise e no Terraform | [`docs/endpoints/inventories.md`](docs/endpoints/inventories.md) |
| Preço por região / zonas / fontes oficiais / família (arquitetura, burstable) / payback | [`PLANO_PRECO_REGIONAL.md`](PLANO_PRECO_REGIONAL.md) · [`docs/precision-engine.md`](docs/precision-engine.md) |
| Caminho do agente (MCP): snapshot de leitura, envio idempotente, versões | [`docs/endpoints/agent-snapshots.md`](docs/endpoints/agent-snapshots.md) · [`PLANO_SNAPSHOT_AGENTE.md`](PLANO_SNAPSHOT_AGENTE.md) |
| `decision_support` compacto para o agente (`agent-snapshot-v2`, rota de detalhe) | [`PLANO_DECISION_SUPPORT_COMPACTO.md`](PLANO_DECISION_SUPPORT_COMPACTO.md) |
| Cotação de compute pela capacidade-hora / Savings Plan e reserva de compute na comparação / antes e depois nos seis relatórios | [`PLANO_EMPACOTAMENTO.md`](PLANO_EMPACOTAMENTO.md) |
| Lote da autoridade (entregue como v4: contestação verificável, provedor não avaliado, classificação, pool coberto por compromisso, confiança pelo custo atual) e fila de precisão (custos por zona, `perf_coef`) | [`PLANO_AUTORIDADE_V3_E_PRECISAO.md`](PLANO_AUTORIDADE_V3_E_PRECISAO.md) |
| Migrar uma aplicação ou um serviço com tudo que vai junto / rotas `applications` e `migration-plans` / custo do escopo / exportação / Terraform do escopo | [`PLANO_ESCOPO_DE_MIGRACAO.md`](PLANO_ESCOPO_DE_MIGRACAO.md) · [`docs/endpoints/migration-plans.md`](docs/endpoints/migration-plans.md) |
| Estratégia da sprint: fechamento do 13/10, decisões do usuário, pendências do MCP, sprint de Terraform em blocos (T0–T7) | [`PLANO_ESTRATEGIA_SPRINT_TERRAFORM.md`](PLANO_ESTRATEGIA_SPRINT_TERRAFORM.md) |
| Fila de prioridades do que está aberto (faça agora / assim que possível / fora do MVP) e o que já foi concluído | [`PROXIMAS_EXECUCOES.md`](PROXIMAS_EXECUCOES.md) |
| Lock-in pelo inventário e pela classe no score (V4.4 e V4.5): regra `lock_in_efetivo`, metadata curada pelo nome do DE-PARA, tarefas do `score-7rs-v5` para o autor do #32, curadoria pendente | [`PLANO_SCORE_V5_LOCKIN.md`](PLANO_SCORE_V5_LOCKIN.md) |
| Ciclo de vida do preço (VIVO → ADUBO, duração de cada estado, agente não revive preço, revitalização por billing, quórum) — P3.0–P3.2 feitos | [`PLANO_CICLO_DE_VIDA_DO_PRECO.md`](PLANO_CICLO_DE_VIDA_DO_PRECO.md) |
| Rede por tipo (balanceador, NAT, IP, VPN, tráfego entre zonas, CDN) cotada pela quantidade medida / destino grátis / taxas oficiais / plano de controle do Kubernetes, segredos e chaves (R4) / bastion, Defender e contêiner serverless (Container Apps, Cloud Run) cotados pela quantidade (R5) | [`PLANO_PRECO_NAO_COMPUTE.md`](PLANO_PRECO_NAO_COMPUTE.md) |
| Tabela de equivalentes: tipo, portabilidade e risco / logs, DNS, WAF, registro de imagens e métricas cotados pela quantidade / DE-PARA (Functions da Azure, PostgreSQL da OCI) e curadoria | [`PLANO_EQUIVALENTES_E_TARIFAS.md`](PLANO_EQUIVALENTES_E_TARIFAS.md) |
| Cenários de entrada (Cost Explorer, fatura sem ID, CUR, com ou sem inventário) / custo estimado pelo tipo / diagnóstico de entrada da análise | [`PLANO_CENARIOS_DE_ENTRADA.md`](PLANO_CENARIOS_DE_ENTRADA.md) |
| Curadoria de nomes de serviço das faturas (fila de sugestões, aprovação pela OneShift, apelido do DE-PARA) | [`docs/endpoints/curadoria.md`](docs/endpoints/curadoria.md) |
| Conferência de serviços nas quatro nuvens (o que cada serviço vira na análise), reconhecidos sem cotação, prefixo de marca no DE-PARA, scripts de exportação da fatura | [`PLANO_CONFERENCIA_DE_SERVICOS.md`](PLANO_CONFERENCIA_DE_SERVICOS.md) · [`scripts/exportar_fatura/`](scripts/exportar_fatura/README.md) |
| Mudanças de tela decididas, para o front (premissa 1 vCPU = 1 vCPU, estado da reanálise na lista) | [`PLANO_ALTERACOES_DE_TELA.md`](PLANO_ALTERACOES_DE_TELA.md) |
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
  cloud). Gate de acurácia: `sku_coverage_ratio ≥ 95%` (orphan ≤ 5%). Detalhes em
  [`docs/sku-matching.md`](docs/sku-matching.md).
- **Fase atual — PRECISÃO (Precision Engine v3):** documentado em
  [`docs/precision-engine.md`](docs/precision-engine.md).
  Já construído: FX antes de qualquer `delta_custo` (nunca comparar BRL×USD),
  preços de API oficial em `cloud_price_points` (Azure/OCI), shape de instância
  (instance-hours → vCPU/RAM), packing por custo total, categorias não-compute
  por razão de taxa, consolidação por provedor com recomendação determinística,
  ledger `premissas`, **camada de reconciliação** (a análise auditando a si
  mesma) e **cotação por região** (cada pool na zona dele, preços oficiais dos
  4 provedores, arquitetura e burstable no packer, payback do agente derivado
  da premissa — [`PLANO_PRECO_REGIONAL.md`](PLANO_PRECO_REGIONAL.md)).
  Compute cotado pela capacidade-hora medida, com o compromisso de compute
  saindo da origem junto com os pools ([`PLANO_EMPACOTAMENTO.md`](PLANO_EMPACOTAMENTO.md)).
  Custos auxiliares por zona e burstable na origem feitos em 16/10. Pendente: `perf_coef`
  (espera a fonte, decisão 2.7) e payback na tela (falta a fonte do custo da migração).
- **O backend manda no número; a IA narra.** `use_cases/analysis/backend_authority.py`
  sobrescreve custo, economia e provedor recomendado da resposta do LLM com o
  cálculo determinístico de `resumo_executivo`. O qualitativo do modelo é
  preservado. Sem isso a resposta da API poderia contradizer a reconciliação.
- **Seeds têm ordem.** `seed_from_csv` (864 linhas, DE-PARA autoritativo) roda
  **por último** — ver [`docs/dev-setup.md`](docs/dev-setup.md#41-seeds-de-referência--a-ordem-importa).
  Na ordem errada o ambiente novo fica com `service_type` grosseiro.
- **Gates de integração:** `app/tests/integration/` roda a pipeline inteira sobre
  os 2 CSVs reais versionados e trava os invariantes de cada onda. Pula sem
  Postgres; no CI um skip é falha. `pytest -m "not integration"` para o loop rápido.

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
