# API Conventions

Contratos transversais da API do backend. Todas as rotas em `/v1/...` seguem
estas regras. Quando uma rota PRECISAR fugir, documente a exceção no
catálogo ([`endpoints/*.md`](endpoints/)) com justificativa.

> **Audiência:** quem vai adicionar rota nova ou consumir a API. Para regras
> técnicas de Clean Arch, ver [`architecture.md`](architecture.md). Para o
> shape de cada recurso, ver [`data-model.md`](data-model.md).

## URL

- Versionamento por path: `/v1/...`. A próxima quebra de contrato vai em `/v2`.
- Coleções em **kebab-case plural**: `/v1/billing-files`, `/v1/cloud-credentials`.
- Verbo HTTP expressa ação. **Nunca verbo na URL** (`/createBillingFile` proibido).
- Sub-recursos seguem o pai: `/v1/billing-files/{job_id}/items`.

## Autenticação

- `Authorization: Bearer <jwt>` em **toda** rota, exceto `GET /v1/health`.
- JWT validado contra Supabase JWKS (RS256, `audience=authenticated`).
- Sem header válido → **401** com `WWW-Authenticate: Bearer`.
- `claims.sub` é o `user_id` (UUID). Todo dado pertence a um `user_id`.
- **Bypass de dev**: `AUTH_ENABLED=false` no `.env.dev` aceita qualquer request como
  usuário sintético `00000000-0000-0000-0000-000000000001`. Hard-blocked em produção
  pelo próprio `get_current_user` (retorna 500 se `is_production`).

## Isolamento por `user_id`

Regra inegociável: **toda query filtra por `user_id`**.

- `GET /v1/billing-files/{id}` que não pertence ao caller → **404** (não 403).
  Esconde a existência do recurso — não vaza enumeração.
- Repositórios concretos têm assinatura `(resource_id, user_id)`. Não existe
  método público que aceite só o `resource_id`.

## Paginação

Padrão `limit` + `offset` em **query params**. Aplicado em qualquer endpoint
que retorna coleção (`GET /v1/...` listas).

```
GET /v1/billing-files?limit=50
GET /v1/billing-files/{id}/items?limit=100&offset=0
```

| Param | Significado | Default | Range |
|---|---|---|---|
| `limit` | Quantas linhas trazer | varia por rota | 1..teto da rota |
| `offset` | Quantas linhas pular antes | `0` | `>= 0` |

- Fora do range → **422** com mensagem explícita (`"limit must be 1..1000"`).
- Tetos defensivos por rota (definidos em [`app/api/v1/routes/*.py`](../app/api/v1/routes/)
  como constantes com prefixo `_MAX_`). Mudar teto = documentar a justificativa.

Tetos atuais (consultar [`billing.py`](../app/api/v1/routes/billing.py) para a
verdade ao vivo):

| Rota | `_MAX_*` | Valor |
|---|---|---|
| `GET /v1/billing-files` | `_MAX_REPORTS_PER_PAGE` | 500 |
| `GET /v1/billing-files/{id}/items` | `_MAX_ITEMS_PER_PAGE` | 1000 |

### Envelope vs. array direto

| Caso | Resposta | Quando usar |
|---|---|---|
| Coleção **stand-alone** (ex: lista de jobs do usuário) | Array JSON direto: `[{...}, {...}]` | Quando o total é trivial pelo cliente (length do array). |
| Coleção **paginada com total**, ou drill-down de um pai | Envelope: `{report_id, total, limit, offset, items: [...]}` | Quando o cliente precisa saber quanto resta (frontend com "Próxima página"). |

Inconsistência atual: `GET /v1/billing-files` retorna array, `GET /v1/billing-files/{id}/items`
retorna envelope. Está consciente — vamos uniformizar quando tiver caso real
de paginação na lista de jobs (hoje o cap é 500, suficiente pro MVP).

## Serialização (JSON)

Pydantic v2 com `model_config = ConfigDict(frozen=True)` em todo response model.

| Tipo Python | JSON | Por quê |
|---|---|---|
| `Decimal` | string (`"1369.516505"`) | Preserva precisão. JSON number é float IEEE 754. |
| `datetime` (UTC) | ISO 8601 (`"2026-04-01T00:00:00Z"`) | Sem timezone implícito. |
| `UUID` | string | Padrão Pydantic. |
| `Enum` (`JobStatus`, `CloudProvider`) | string do valor (`"done"`, `"aws"`) | Stable wire format. |
| `dict` (tags, raw_payload) | object | — |
| `None` | `null` | Não usar string `"None"`. |

**Nunca** declarar campo monetário como `float` em response model — quebra a
regra do `NUMERIC(18,6)` ponta-a-ponta. Ver [`data-model.md`](data-model.md#por-que-numeric186).

## Status Codes

| Status | Quando |
|---|---|
| **200** | GET com sucesso |
| **201** | POST que cria recurso síncronamente (não usado no MVP — uploads são async) |
| **202** | POST que enfileira processamento async. Retorna `{job_id, status: "queued", ...}` |
| **400** | Input inválido em nível de domínio (ex: período de billing inválido). Mensagem clara. |
| **401** | Token ausente ou inválido. Header `WWW-Authenticate: Bearer`. |
| **404** | Recurso não existe **OU** existe mas pertence a outro usuário. |
| **413** | Upload > `MAX_UPLOAD_SIZE_MB` (default 50). |
| **415** | Extensão não suportada. Mensagem lista as permitidas. |
| **422** | Schema Pydantic falhou, ou paginação fora do range. FastAPI gera automaticamente para schema. |
| **500** | Bug do servidor. Logado com `event=...`. Cliente não precisa ver detalhes. |

## Ordenação

- Listas de **jobs**: `created_at DESC` (mais recente primeiro).
- Listas de **items**: `cost DESC, service ASC` (top spenders primeiro).
- **Não há query param `?sort=...`** por enquanto. Quando precisar, padrão será
  `?sort=campo` (asc) ou `?sort=-campo` (desc).

## Erros — formato

FastAPI default:

```json
{ "detail": "billing job 00000000-... not found" }
```

Para erros de validação Pydantic, FastAPI retorna lista estruturada — não
sobrescrevemos. Mensagens em português ou inglês, mas **consistente por rota**.

## CORS

- `allow_origins=["*"]` **proibido** em qualquer ambiente. `create_app()` levanta
  `RuntimeError` se detectar `"*"`.
- Origins permitidos vêm do `Settings.cors_origins`.
- `allow_credentials=True` é obrigatório (cookies do Supabase SSR).

## OpenAPI / Swagger

- `GET /docs` (Swagger UI) e `GET /redoc` (ReDoc) — **desabilitados em produção**
  via `Settings.is_production`.
- `GET /openapi.json` também desligado em prod.
- Em dev, são a referência viva — `response_model` tipado é o que aparece lá.

## Background tasks

Rotas que disparam trabalho async (parsing pesado, IA) seguem este padrão:

1. Persistem entidade com `status=QUEUED` síncronamente.
2. `background_tasks.add_task(...)` enfileira a função pesada.
3. Retornam **202** + `job_id` imediatamente.
4. Cliente faz polling em `GET /v1/.../{job_id}`.

A função de background **cria sua própria `AsyncSession`** via `get_sessionmaker()`
— a session do request já foi commitada/fechada. Se a task falha, status é
gravado como `FAILED` em uma **session limpa** (rollback da session original
descartaria a escrita).

## Idempotência

- **Envio do agente (`POST /v1/agent/snapshots/{id}/submissions`): idempotente
  pelo conteúdo.** O mesmo corpo validado no mesmo snapshot devolve o envio
  gravado (`200` e `Idempotent-Replayed: true`); corpo diferente é uma revisão
  nova. Garantido no banco (lock de linha e constraint única), não por consulta
  prévia. Sem `Idempotency-Key`: o resultado é função pura do snapshot e do
  corpo, e uma chave só obrigaria o cliente a persisti-la entre reinícios. Ver
  [`endpoints/agent-snapshots.md`](endpoints/agent-snapshots.md).
- **Preparação de snapshot (`POST /v1/agent/snapshots`):** o mesmo conteúdo
  devolve o mesmo snapshot (`200`, `reused: true`), via `INSERT … ON CONFLICT`.
- Os demais endpoints de criação não têm `Idempotency-Key`. Adicionar quando
  houver retry agressivo (cliente mobile, GitHub Actions).
- Operações idempotentes por design:
  - `IngestBillingFileUseCase`: `delete_by_report` + INSERT em uma transação.
  - `bulk_insert` usa `ON CONFLICT (id) DO NOTHING`.

## Quando o cliente é um humano vs. máquina

Estes endpoints são consumidos por:
- **Frontend SaaS** (CFO/controller). UX > consistência: prefira envelopes
  com `total`/`limit`/`offset` para popular "X de Y" na tela.
- **Operadores via curl/Postman** (debug). Array direto é mais ergonômico em
  pipes de jq.

Esta tensão é o motivo de algumas rotas serem array e outras envelope. Catálogo
de endpoints ([`endpoints/*.md`](endpoints/)) diz qual é qual.

## Documentação relacionada

- [`architecture.md`](architecture.md) — Clean Arch, CORS, auth.
- [`data-model.md`](data-model.md) — schema dos recursos retornados.
- [`endpoints/billing.md`](endpoints/billing.md) — catálogo da Fase 1.
- [`parsers.md`](parsers.md) — formatos aceitos no upload.
