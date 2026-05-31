# Fase 3 — Pipeline de Análise com Background + Polling

> **Propósito:** transformar a análise de migração (BuildMigrationContext) em
> uma pipeline assíncrona com status tracking, background execution e polling.
> Adicionar sanitização robusta nos parsers para eliminar `unknown` e
> double-counting de linhas financeiras.
>
> **Duração estimada:** ~2h (4 blocos).
>
> **Dependências:** Fase 2 concluída (EquivalenceMatcher, enrichment, mock
> catalog seed).

---

## Bloco 1 — Higienização de Entrada (15 min)

**Problema:** Linhas de resumo financeiro (StatementTotal, InvoiceTotal) e
impostos (Tax) estouravam o `total_spend` — ex: $208k em vez de $104k.

**O que fazer:** Adicionar `if line.get("RecordType") in (...) continue` no
início do loop principal de **todos os 7 parsers**, mesmo que o parser não
tenha essas colunas (`.get()` retorna vazio, no-op).

**Parsers que já têm o filtro:**
- `aws_invoice_parser.py` — filtra `RecordType` em (`StatementTotal`, `InvoiceTotal`)
- `gcp_cost_table_parser.py` — filtra `Cost type == "tax"`

**Parsers que receberão o filtro (belt-and-suspenders):**
- `aws_parser.py` (CUR)
- `aws_cost_explorer_parser.py`
- `azure_parser.py`
- `gcp_parser.py` (BigQuery)
- `oci_parser.py`

**Camada extra de segurança:** O `BuildBillingEnrichedUseCase` já filtra no
pós-parse via `raw_payload["RecordType"]` e `raw_payload["Cost type"]`.

---

## Bloco 2 — Orquestrador Background (45 min)

**Problema:** `POST /v1/analyses/migration-context` roda síncrono na request.
Análises complexas (>1000 workloads) podem timeout.

**O que fazer:** Criar `POST /v1/analyses/start/{job_id}` que dispara uma
`BackgroundTask` e retorna 202 imediatamente.

### Artefatos novos

| Camada | Arquivo | O que faz |
|---|---|---|
| Entity | `domain/entities/analysis_job.py` | Dataclass `AnalysisJob` com `job_id`, `user_id`, `report_id`, `status`, `created_at`, `llm_output`, `error` |
| Interface | `domain/repositories/i_analysis_job_repo.py` | `IAnalysisJobRepository` — `create`, `update_status`, `get_by_id` |
| SQLAlchemy model | `infra/database/models/analysis_job.py` | `AnalysisJobModel` (tabela `analysis_jobs`) |
| Repository impl | `infra/repositories/pg_analysis_job_repo.py` | `PgAnalysisJobRepository` |
| Use case | `use_cases/analysis/run_analysis_pipeline.py` | `RunAnalysisPipelineUseCase` — 1. atualiza status PROCESSING, 2. executa BuildMigrationContextUseCase, 3. simula LLM, 4. persiste COMPLETED/FAILED |
| Route | `api/v1/routes/analysis.py` | `POST /v1/analyses/start/{job_id}` |

### Fluxo

```
POST /v1/analyses/start/{report_id}
  → cria AnalysisJob(PENDING)
  → BackgroundTask: run_analysis_pipeline(job_id)
      → status = PROCESSING
      → BuildMigrationContextUseCase.execute()
      → LLM simulation
      → status = COMPLETED (with llm_output)
      → on error: status = FAILED (with error)
  → return 202 {"job_id": ..., "status": "PENDING"}
```

### Simulação de LLM

Por enquanto, o output da IA é uma string template:

```python
llm_output = (
    f"Análise de migração concluída para o provedor {context.source_provider}. "
    f"Período: {context.billing_period}. "
    f"Gasto total: {context.total_spend} {context.currency}. "
    f"Workloads analisados: {len(context.workloads)}. "
    f"Workloads com equivalência encontrada: {with_match}."
)
```

Futuramente (Fase 5), substituir por chamada real a LLM com o contexto
serializado como prompt.

---

## Bloco 3 — Endpoint de Poll (30 min)

**Problema:** Front-end precisa saber quando a IA terminou.

**O que fazer:**

```
GET /v1/analyses/{job_id}/result
  → busca AnalysisJob por job_id + user_id
  → 404 se não encontrado ou user mismatch
  → retorna {job_id, status, llm_output, error, created_at}
```

**Status possíveis:** `PENDING` → `PROCESSING` → `COMPLETED` | `FAILED`

### Schema de resposta

```json
{
  "job_id": "uuid",
  "report_id": "uuid",
  "status": "COMPLETED",
  "llm_output": "Análise de migração...",
  "error": null,
  "created_at": "2026-05-25T12:00:00Z"
}
```

---

## Bloco 4 — Payload da IA (30 min)

**Problema:** O JSON de contexto de migração pode ser muito grande para a
janela de contexto do LLM (>1000 workloads).

**O que fazer:** Garantir que o `MigrationContextResponse` serializa os campos
críticos:

- `workloads[].technical_match` (vcpus, ram_gb)
- `workloads[].financials` (estimated_monthly_cost, cost_delta, pricing_confidence)
- `total_spend` — sem double-counting (graças ao Bloco 1)

**Filtragem futura (recomendação):** Antes de enviar ao LLM real, limitar a
top-5 workloads por custo total, ou agrupar por `canonical_service` para
reduzir cardinalidade.

---

## Entregas

- [x] Bloco 1: Parser filters funcionais (2 parsers já filtram; 5 recebem filtro)
- [ ] Bloco 2: `POST /v1/analyses/start/{report_id}` → 202 + BackgroundTask
- [ ] Bloco 2: `AnalysisJob` entity + model + repository
- [ ] Bloco 2: `RunAnalysisPipelineUseCase` com simulação de LLM
- [ ] Bloco 3: `GET /v1/analyses/{job_id}/result` → status + output
- [ ] Bloco 4: Payload schema já OK (`MigrationContextResponse`)
- [ ] Testes: pelo menos 1 teste de unidade para o use case
- [ ] Migration: `analysis_jobs` table

## Referências

- Código existente: `app/api/v1/routes/analysis.py`
- Schema existente: `app/schemas/analysis.py`
- BackgroundTask pattern: `app/api/v1/routes/billing.py` (`_run_ingest_isolated`)
- Session isolation pattern: `app/api/v1/routes/billing.py` (`_run_ingest_isolated`)
- Mock catalog seed: `app/infrastructure/database/seed_mock_catalog.py`
