# Endpoints `/v1/billing-files`

Catálogo da Fase 1 (Módulo 1 — Extração de Billing).

> Convenções gerais: [`api-conventions.md`](../api-conventions.md). Formatos
> aceitos no upload e como o parsing funciona: [`parsers.md`](../parsers.md).
> Schema dos recursos retornados: [`data-model.md`](../data-model.md).

Toda rota aqui exige `Authorization: Bearer <jwt>` (bypass em dev via
`AUTH_ENABLED=false` — ver [`architecture.md`](../architecture.md#autenticação)).

## Fluxo típico

```
POST /v1/billing-files (arquivo)
    → 202 { job_id, status: "queued" }
         ↓ background task
GET /v1/billing-files/{job_id}              ← poll status
    → 200 { status: "done", row_count: 57, ... }
         ↓ quando done
GET /v1/billing-files/{job_id}/items        ← drill-down nos dados
    → 200 { total: 57, items: [...] }
```

---

## `POST /v1/billing-files`

Upload de arquivo de billing. Dispara parsing async; retorna **202** imediatamente.

### Request

- Content-Type: `multipart/form-data`
- Campo: `file` (obrigatório)
- Extensões aceitas: `.csv`, `.tsv`, `.txt`, `.gz`, `.zip`, `.json`, `.ndjson`, `.xlsx`, `.xlsm`
- Tamanho máximo: 50 MB (configurável via `MAX_UPLOAD_SIZE_MB`)

### Response — 202 Accepted

```json
{
  "job_id": "07531638-0c1f-4453-acdb-703ebb01c0f7",
  "status": "queued",
  "source_filename": "aws_cur2_sample.csv",
  "created_at": "2026-04-01T00:00:00Z"
}
```

### Erros

| Status | Quando |
|---|---|
| 400 | Sem filename no upload |
| 401 | Token ausente ou inválido |
| 413 | Arquivo > 50 MB |
| 415 | Extensão não suportada |
| 422 | `FORMATO_NAO_SUPORTADO`: o que a verificação (`POST /v1/billing-file-checks`) recusa — Cost Explorer por serviço (`AWS_CE_DAILY_BY_SERVICE`, sem tipo de uso), relatório de uso da OCI (`reports_usage`, sem custo) ou arquivo que não é fatura conhecida. `mensagem` explica e diz o export certo |

---

## `POST /v1/billing-file-checks`

Crítica do arquivo **antes** do upload: lê só a amostra, não grava nada. A tela de
importação usa para avisar antes de enviar (PLANO_CENARIOS_DE_ENTRADA §3.4).

Request igual ao upload (`multipart/form-data`, campo `file`). Response 200:

```json
{
  "aceita": false,
  "formato": "AWS_CE_DAILY_BY_SERVICE",
  "nome_do_formato": "Cost Explorer da AWS (total por serviço)",
  "provedor": "aws",
  "com_id_de_recurso": false,
  "problemas": [
    {"nivel": "critico", "codigo": "cost_explorer", "titulo": "Esse arquivo é um resumo do Cost Explorer", "texto": "…"}
  ],
  "como_exportar": "Na AWS, crie uma exportação CUR 2.0 …"
}
```

`aceita=false` quando o upload recusaria (Cost Explorer) ou o conteúdo não é fatura
conhecida (`formato_nao_reconhecido`). `nivel`: `critico` (não serve), `alerta` (falta
uma parte), `informativo` (limite do formato: fatura sem ID de recurso, que dá custo
por aplicação **estimado** quando há inventário).

O mesmo diagnóstico, com o que depende da análise (inventário ausente, de outra
nuvem ou parcial, custo estimado, gasto não classificado), vem em
`diagnostico_de_entrada` no `GET /v1/analyses` e no `GET /v1/analyses/{id}/result`:
`{ "nivel": "critico" | "alerta" | null, "problemas": [...] }`, ou `null` sem nada a dizer.

### O que acontece em background

1. Detecta provider pelo **conteúdo** (não pela extensão).
2. Parser específico do provider produz DataFrame normalizado.
3. `deduplicate(line_hash)` + Pandera validate.
4. `delete_by_report(report_id, user_id)` + `bulk_insert` em chunks de 1000.
5. Atualiza status para `done` ou `failed` (com `error_message` se falhar).

Detecção suporta: **AWS CUR 2.0 / CUR Legacy / fatura detalhada legada · Azure
PAYG / EA · GCP NDJSON / CSV / XLSX · OCI Cost Report**. Detalhes por provider
em [`parsers.md`](../parsers.md).

> **Nota sobre o Cost Explorer:** linhas com `cost=0` podem ser emitidas por
> design — para o agente de IA detectar "serviço zerou neste mês" (ativo com
> drift) ou "recurso observado, sem cobrança" (`raw_payload.scope = "global_zero"`).
> Ver [`parsers.md`](../parsers.md#política-de-emissão-de-cost0-cost-explorer)
> para a regra completa.

---

## `GET /v1/billing-files`

Lista de jobs do usuário, mais recente primeiro.

### Query params

| Param | Default | Range |
|---|---|---|
| `limit` | 50 | 1..500 |

(Sem `offset` por enquanto — adicione quando virar problema.)

### Response — 200 OK

**Array direto**, sem envelope:

```json
[
  {
    "job_id": "07531638-0c1f-4453-acdb-703ebb01c0f7",
    "status": "done",
    "provider": "aws",
    "schema_version": "AWS_CUR2.0",
    "source_filename": "aws_cur2_sample.csv",
    "alias": "Fatura AWS de março",
    "nome_exibicao": "Fatura AWS de março",
    "row_count": 4,
    "error_message": null,
    "created_at": "2026-04-01T00:00:00Z",
    "updated_at": "2026-04-01T00:00:01Z"
  },
  {
    "job_id": "6cdcd93c-bacd-4292-bd9a-d97c28fbf9f5",
    "status": "failed",
    "provider": null,
    "schema_version": null,
    "source_filename": "broken.xlsx",
    "alias": null,
    "nome_exibicao": "broken.xlsx",
    "row_count": 0,
    "error_message": "ValidationError: normalized DataFrame is empty",
    "created_at": "2026-04-01T00:00:00Z",
    "updated_at": "2026-04-01T00:00:00Z"
  }
]
```

### Erros

| Status | Quando |
|---|---|
| 422 | `limit` fora do range |

---

## `GET /v1/billing-files/{job_id}`

Status de um job. Usado para polling.

### Response — 200 OK

```json
{
  "job_id": "07531638-0c1f-4453-acdb-703ebb01c0f7",
  "status": "done",
  "provider": "aws",
  "schema_version": "AWS_CUR2.0",
  "row_count": 4,
  "error_message": null,
  "source_filename": "aws_cur2_sample.csv",
  "alias": null,
  "nome_exibicao": "aws_cur2_sample.csv",
  "created_at": "2026-04-01T00:00:00Z",
  "updated_at": "2026-04-01T00:00:01Z"
}
```

Estados possíveis em `status`: `queued`, `processing`, `done`, `failed`.

### Erros

| Status | Quando |
|---|---|
| 404 | Job não existe **OU** pertence a outro usuário |

---

## `PUT /v1/billing-files/{job_id}/alias`

Dá ao billing um nome escolhido pelo usuário (melhoria M9, 17/10). A tela mostra o
nome de exibição no lugar do nome do arquivo.

### Request

```json
{ "alias": "Fatura AWS de março" }
```

- Espaços nas pontas saem, e espaços repetidos viram um só. No máximo 120
  caracteres; caractere de controle é recusado.
- `null` ou texto vazio tira o alias: o nome volta a ser o do arquivo.
- Vale em qualquer estado do billing (`queued` a `failed`) e para todas as
  revisões da análise dele, porque a lista mostra uma linha por billing.

### Response — 200 OK

O mesmo item de `GET /v1/billing-files`, com os campos novos:

```json
{
  "job_id": "07531638-0c1f-4453-acdb-703ebb01c0f7",
  "source_filename": "aws_cur_2026-03.csv",
  "alias": "Fatura AWS de março",
  "nome_exibicao": "Fatura AWS de março"
}
```

`nome_exibicao` = `alias` quando existe; senão o nome do arquivo. Sai também em
`GET /v1/billing-files`, `GET /v1/billing-files/{job_id}` e em cada item de
`GET /v1/analyses`. **A tela lê `nome_exibicao`.** `source_filename` continua
igual: é o rastro do upload.

### Erros

| Status | Quando |
|---|---|
| 404 | Billing não existe **OU** é de outro usuário |
| 422 | Mais de 120 caracteres, caractere de controle ou corpo sem `alias` |

### Análise de impacto (escrita antes do código)

- **Banco:** coluna nova `billing_reports.alias VARCHAR(120) NULL`, sem
  preenchimento. Migration aditiva; o deploy do develop não roda migration, então
  ela é aplicada no pod antes do push.
- **Contrato:** só campos novos (`alias`, `nome_exibicao`). O front atual, que lê
  `source_filename`, continua funcionando até passar a ler `nome_exibicao`.
- **Análise, LLM e números:** nada muda. O nome do arquivo não entra no payload da
  análise, no prompt nem no snapshot do agente; o alias também não. Nenhuma nota ao
  MCP é necessária.
- **Ingestão em andamento:** o `update` do repositório (estado da ingestão) não
  grava o alias; ele tem o próprio `rename`. Uma ingestão que leu o billing antes
  da renomeação não apaga o nome dado.
- **Curadoria de nomes:** o filtro que tira billings de mock olha o nome do
  arquivo, não o alias. Renomear não tira nem põe billing na coleta.
- **Exclusão de conta:** o alias está na linha do billing e sai com ela.
- **Telas que passam a mostrar o alias:** lista de análises (com a busca, que
  procura no alias e no nome do arquivo), menu de análises recentes, aviso de
  análise concluída, trilha e título da análise, linha do tempo do billing e
  confirmação de exclusão.

---

## `GET /v1/billing-files/{job_id}/items`

Drill-down: linhas normalizadas de um job processado.

### Query params

| Param | Default | Range |
|---|---|---|
| `limit` | 100 | 1..1000 |
| `offset` | 0 | >= 0 |

Ordenação fixa: `cost DESC, service ASC` (top spenders primeiro).

### Response — 200 OK

**Envelope** com paginação:

```json
{
  "report_id": "07531638-0c1f-4453-acdb-703ebb01c0f7",
  "total": 57,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "id": "cf3eb8bb-7283-440d-aedf-a4c0f5aef319",
      "report_id": "07531638-0c1f-4453-acdb-703ebb01c0f7",
      "provider": "aws",
      "account_id": "",
      "service": "Elastic Container Service for Kubernetes",
      "resource_id": null,
      "region": null,
      "cost": "1369.516505",
      "cost_discounted": "1369.516505",
      "currency": "USD",
      "usage_amount": null,
      "usage_unit": null,
      "usage_start": "2026-04-01T00:00:00Z",
      "usage_end": "2026-04-01T23:59:59Z",
      "billing_period": "2026-04",
      "tags": {},
      "is_correction": false,
      "is_estimate": false,
      "line_hash": "f804d05d9cf8d32a805c7b411132a256a9394a8d4ae252374769e42332dc6b5c"
    }
  ]
}
```

`cost` e `cost_discounted` são **strings** (preservam precisão `Decimal`). Cliente que
faz aritmética deve converter para `BigDecimal`/`Decimal` na sua linguagem, nunca `float`.

### Erros

| Status | Quando |
|---|---|
| 404 | Job não existe ou pertence a outro usuário |
| 422 | `limit` fora do range ou `offset < 0` |

---

## `GET /v1/health`

Liveness + DB probe. **Sem auth.**

### Response — 200 OK

```json
{ "status": "ok", "db": "ok" }
```

Quando o PG está fora:

```json
{ "status": "degraded", "db": "down" }
```

Note que **sempre retorna 200** — health probe lê pelo body. Mudar para 503 em
`degraded` é melhor quando tivermos Kubernetes (liveness probe HTTP-status-based).

---

## Documentação relacionada

- [`../api-conventions.md`](../api-conventions.md) — paginação, envelopes,
  serialização, status codes.
- [`../parsers.md`](../parsers.md) — formato aceito por provider, política
  `global_zero` do Cost Explorer.
- [`../data-model.md`](../data-model.md) — schema dos `billing_items` /
  `billing_reports`.
- [`../architecture.md`](../architecture.md) — Clean Arch, auth, background
  tasks.
- [`../dev-setup.md`](../dev-setup.md) — como rodar local e testar com `curl`.
