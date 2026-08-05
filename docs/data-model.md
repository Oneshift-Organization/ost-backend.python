# Data model

Esquema do banco PostgreSQL + schema canônico do parser. Tudo que entra no banco
**passa por esses dois shapes**.

> **Audiência:** quem vai escrever SQL, adicionar coluna, criar query nova ou
> entender por que `cost` é string no JSON e Decimal no Python.

## Visão geral

Duas tabelas, relação 1:N com CASCADE, isolamento por `user_id` em **toda**
query.

```
billing_reports (1)  ──CASCADE──▶  billing_items (N)
```

- **`billing_reports`** — um por upload. Carrega `status` do job
  (`queued → processing → done | failed`) + metadados do arquivo (provider,
  schema_version, source_filename, row_count, error_message).
- **`billing_items`** — uma linha por observação normalizada (resultado do
  parser). Carrega `cost` em `NUMERIC(18,6)`, `tags` em JSONB, marcadores
  semânticos (`is_correction`, `is_estimate`) e `raw_payload` para
  rastreabilidade.

## DDL completo

Origem da verdade: migration
[`0001_initial_billing_schema.py`](../app/infrastructure/database/migrations/versions/0001_initial_billing_schema.py).
A versão SQL abaixo é equivalente.

### `billing_reports`

```sql
CREATE TABLE billing_reports (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID         NOT NULL,
    provider        VARCHAR(10),
    schema_version  VARCHAR(50),
    source_filename VARCHAR(500),
    status          VARCHAR(20)  NOT NULL DEFAULT 'queued',
    row_count       INTEGER      NOT NULL DEFAULT 0,
    error_message   VARCHAR(2000),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_billing_reports_user_id_created_at
  ON billing_reports (user_id, created_at);
```

Estados de `status`:

| Estado | Significado | Origem |
|---|---|---|
| `queued` | Upload aceito, ingestão ainda não iniciou. | `UploadBillingFileUseCase` |
| `processing` | Pipeline rodando (detect → parse → validate → bulk_insert). | `IngestBillingFileUseCase.execute` início |
| `done` | Sucesso. `provider`, `schema_version` e `row_count` preenchidos. | `mark_done()` |
| `failed` | Erro em algum estágio. `error_message` carrega o motivo. | `mark_failed()` em session limpa |

Modelo SQLAlchemy: [`billing_reports.py`](../app/infrastructure/database/models/billing_reports.py).
Entidade do domínio: [`BillingReport`](../app/domain/entities/billing_report.py).

### `billing_items`

```sql
CREATE TABLE billing_items (
    id               UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id        UUID            NOT NULL REFERENCES billing_reports(id) ON DELETE CASCADE,
    user_id          UUID            NOT NULL,

    provider         VARCHAR(10)     NOT NULL,
    account_id       VARCHAR(200),
    service          VARCHAR(200)    NOT NULL,
    resource_id      VARCHAR(500),
    region           VARCHAR(100),

    cost             NUMERIC(18, 6)  NOT NULL,
    cost_discounted  NUMERIC(18, 6),
    currency         VARCHAR(3)      NOT NULL DEFAULT 'USD',

    usage_amount     NUMERIC(18, 6),
    usage_unit       VARCHAR(50),
    usage_start      TIMESTAMPTZ,
    usage_end        TIMESTAMPTZ,
    billing_period   VARCHAR(7)      NOT NULL,   -- 'YYYY-MM'

    tags             JSONB           NOT NULL DEFAULT '{}'::jsonb,
    is_correction    BOOLEAN         NOT NULL DEFAULT FALSE,
    is_estimate      BOOLEAN         NOT NULL DEFAULT FALSE,
    line_hash        VARCHAR(64)     NOT NULL,

    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_billing_items_user_period_service
  ON billing_items (user_id, billing_period, service);

CREATE INDEX ix_billing_items_report_id
  ON billing_items (report_id);
```

Modelo SQLAlchemy: [`billing_items.py`](../app/infrastructure/database/models/billing_items.py).
Entidade do domínio: [`BillingItem`](../app/domain/entities/billing_item.py).

### `billing_enriched`

Tabela de enriquecimento: `billing_items` cruzado com ontologia de serviço e
catálogo de SKUs. Populada pelo `BuildBillingEnrichedUseCase` durante o
pós-parse (em BackgroundTask). Contém os mesmos line items com campos
adicionais de classificação e matching de equivalência.

```sql
CREATE TABLE billing_enriched (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id           UUID            NOT NULL REFERENCES billing_reports(id) ON DELETE CASCADE,
    user_id             UUID            NOT NULL,
    provider            VARCHAR(10)     NOT NULL,
    billing_period      VARCHAR(7)      NOT NULL,

    service_name        VARCHAR(200)    NOT NULL,
    service_type        VARCHAR(50),
    category            VARCHAR(50),
    usage_type          VARCHAR(200),
    region              VARCHAR(100),

    usage_amount        NUMERIC(18, 6),
    usage_unit          VARCHAR(50),
    cost                NUMERIC(18, 6)  NOT NULL,
    currency            VARCHAR(3)      NOT NULL DEFAULT 'USD',

    sku_id              VARCHAR(200),
    sku_metadata        JSONB,
    pricing_source      VARCHAR(50),
    pricing_confidence  VARCHAR(20),

    -- Rastreabilidade do matching (ver sku-matching.md §3.2):
    -- mapping_confidence = certeza da CLASSIFICAÇÃO (role/family);
    -- match_layer = qual rung da Matching Ladder (L0–L5) casou.
    mapping_confidence  VARCHAR(10),
    match_layer         VARCHAR(2),

    scope               VARCHAR(20)     NOT NULL DEFAULT 'line_item',
    source_line_hash    VARCHAR(64)     NOT NULL,

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_enriched_user_provider_period
  ON billing_enriched (user_id, provider, billing_period);

CREATE INDEX ix_enriched_scope
  ON billing_enriched (report_id, scope);
```

### `cloud_sku_catalog`

Catálogo multi-cloud de SKUs com especificações de hardware (vCPUs, RAM).
Usado pelo `EquivalenceMatcher` para encontrar SKUs equivalentes entre
provedores com base em recursos (vcpus, ram_gb) — matching determinístico
sem if/else em Python. Populado por `seed_mock_catalog.py` (mock MVP) e
futuramente por Pricing Workers (Fase 2).

```sql
CREATE TABLE cloud_sku_catalog (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    provider          VARCHAR(10)     NOT NULL,
    sku_id            VARCHAR(200)    NOT NULL,
    service_name      VARCHAR(200)    NOT NULL,
    service_type      VARCHAR(50),
    category          VARCHAR(50),
    region            VARCHAR(100),
    usage_unit        VARCHAR(50),
    price_per_unit    NUMERIC(18, 6),
    currency          VARCHAR(3)      NOT NULL DEFAULT 'USD',
    vcpus             NUMERIC(6, 2),
    ram_gb            NUMERIC(8, 2),

    -- Papel do SKU (role), consumido pelo aggregator no lugar de regex sobre
    -- description. Enum em sku-matching.md §3.1. Coluna dedicada
    -- (indexável) preferida a guardar em attributes JSONB.
    component_type    VARCHAR(30)     NOT NULL DEFAULT 'unclassified',

    attributes        JSONB,
    effective_date    TIMESTAMPTZ,
    collected_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_catalog_provider_service_type
  ON cloud_sku_catalog (provider, service_type);

CREATE INDEX ix_catalog_provider_vcpus_ram
  ON cloud_sku_catalog (provider, service_type, vcpus, ram_gb);
```

### `service_ontology`

Ontologia de serviços: mapeia `(provider, usage_type)` para `category` e
`service_type`. Usada pelo enriquecimento para classificar linhas de billing
sem if/elif/else em Python. Populada por seed de 781+ registros.

```sql
CREATE TABLE service_ontology (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    provider          VARCHAR(10)     NOT NULL,
    usage_type        VARCHAR(200)    NOT NULL,
    service_name      VARCHAR(200)    NOT NULL,
    category          VARCHAR(50)     NOT NULL,
    service_type      VARCHAR(50)     NOT NULL,
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (provider, usage_type)
);
```

### `sku_classification_rules`

Regras data-driven da **Matching Ladder** (rungs L2/L3): casam a `description`
livre do SKU a um `component_type` (role) sem `if/else` por cloud em Python.
Versionada e avaliada por `priority ASC` (primeira regra que casa vence). Spec
completa em [`sku-matching.md`](sku-matching.md#41-sku_classification_rules-new-table).

```sql
CREATE TABLE sku_classification_rules (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    provider          VARCHAR(10)     NOT NULL,
    pattern           TEXT            NOT NULL,   -- glob ou regex
    kind              VARCHAR(10)     NOT NULL,   -- 'glob' | 'regex'
    component_type    VARCHAR(30)     NOT NULL,
    family            VARCHAR(30),                -- nullable; extraído se NULL
    confidence        VARCHAR(10)     NOT NULL DEFAULT 'medium',
    priority          INTEGER         NOT NULL DEFAULT 100,  -- menor = primeiro
    active            BOOLEAN         NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_sku_rules_provider_priority
  ON sku_classification_rules (provider, priority) WHERE active;
```

### `unmapped_billing_lines`

Dead-letter table: linhas de billing que não encontraram ontologia (usage_type
não mapeado em `service_ontology`). Usada para human-in-the-loop: admin aprova
mapeamento sugerido por LLM.

```sql
CREATE TABLE unmapped_billing_lines (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID            NOT NULL,
    report_id         UUID            NOT NULL,
    provider          VARCHAR(10)     NOT NULL,
    billing_period    VARCHAR(7)      NOT NULL,
    usage_type        VARCHAR(200),
    service_name      VARCHAR(200),
    region            VARCHAR(100),
    reason            VARCHAR(100)     NOT NULL,
    raw_payload       JSONB,
    source_line_hash  VARCHAR(64),
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
```


### `exchange_rates` (Precision Engine v3 — migration `f5a6b7c8d9e0`)

Taxa de câmbio por par e data. Existe porque comparar BRL de origem com USD de
catálogo superestimava a economia ~5,7× e podia **inverter** a recomendação.

```sql
CREATE TABLE exchange_rates (
    par    VARCHAR(7)    NOT NULL,      -- 'BRL/USD'
    data   DATE          NOT NULL,
    taxa   NUMERIC(18,8) NOT NULL,      -- unidades da origem por 1 do destino
    fonte  VARCHAR(40)   NOT NULL DEFAULT 'manual',
    PRIMARY KEY (par, data)
);
```

Fallback por env `FX_BRL_USD` quando o par não está na tabela. Sem taxa conhecida,
a análise marca `fonte = "SEM_TAXA_CAMBIO"` no ledger — sinaliza em vez de mentir.

### `cloud_price_points` (Precision Engine v3 — migration `a6b7c8d9e0f1`)

Preço **oficial** buscado da API do provedor, com proveniência. Diferente do
`cloud_sku_catalog` (seed, preço aproximado), cada linha é autossuficiente:
carrega os próprios specs, vindos da mesma resposta da API — então o matcher a
consome sem precisar casar chave com o catálogo.

```sql
CREATE TABLE cloud_price_points (
    provider     VARCHAR(10)   NOT NULL,
    sku_key      VARCHAR(120)  NOT NULL,  -- chave nativa (armSkuName, partNumber…)
    region       VARCHAR(60)   NOT NULL,
    service_name VARCHAR(200)  NOT NULL,
    service_type VARCHAR(100)  NOT NULL,  -- 'virtual_server' | categoria não-compute
    price        NUMERIC(18,8) NOT NULL,
    currency     VARCHAR(3)    NOT NULL,
    unit         VARCHAR(30)   NOT NULL DEFAULT 'instance_hour',
    vcpus        NUMERIC(6,2),
    ram_gib      NUMERIC(8,2),
    source       VARCHAR(40)   NOT NULL DEFAULT 'manual',
    confidence   VARCHAR(10)   NOT NULL DEFAULT 'official',
    captured_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, sku_key, region)
);
CREATE INDEX ix_price_points_provider_type
    ON cloud_price_points (provider, service_type);
```

| Campo | Observação |
|---|---|
| `service_type` | `virtual_server` para compute; para não-compute guarda a **categoria** (`storage_ssd`, `network_egress`, `database`) |
| `unit` | `instance_hour` (compute) · `gb_month` (storage) · `gb` (egress) · `db_vcpu_month` |
| `confidence` | `official` (API do provedor) · `reference` (taxa publicada, digitada) · `low` |
| `source` | `azure_retail_api`, `oci_cetools_api`, `published_reference_2026` |

Populado offline por `python scripts/sync_prices.py` — **nunca** no caminho
crítico da análise. O matcher prefere estes preços e rebaixa a confiança quando
cai no `cloud_sku_catalog`.

> **Sobre `raw_payload`:** o schema canônico do parser inclui um campo
> `raw_payload: dict`, mas a **migration 0001 não persiste essa coluna** ainda
> (decisão MVP — adiada para uma migration futura). Atualmente vive só no
> DataFrame em memória durante o processamento. Quando voltar, será `JSONB`.

## Por que `NUMERIC(18,6)`?

| Decisão | Por quê |
|---|---|
| Não `FLOAT` / `DOUBLE PRECISION` | IEEE 754 perde precisão em somas; bilhões de linhas de billing acumulam centavos errados — CFO recebe relatório com US$ a menos. |
| 18 dígitos totais | Cabe US$ 999.999.999.999,xxxxxx (~10¹² USD) — folga para múltiplas contas grandes. |
| 6 dígitos decimais | GCP exporta micros (10⁻⁶ USD). 6 dígitos preservam exatidão sem conversão de unidade. |
| Aplicar em `cost`, `cost_discounted`, `usage_amount` | Tudo que entra em soma/agregação. |
| **Não** `MONEY` do PG | `MONEY` é dependente de `lc_monetary` (configuração de locale). Quebra ao mover entre clusters. |

No Python, o caminho é sempre `Decimal(str(valor))` — nunca `Decimal(float)`,
nunca `float(value)` em coluna de custo. Ver
[`base_parser.py:_cost_to_decimal`](../app/infrastructure/file_ingestion/base_parser.py).

## Schema canônico do parser

Toda saída de parser obrigatoriamente bate com este shape antes de virar
`BillingItem`. Definido em
[`domain/value_objects/billing_record.py`](../app/domain/value_objects/billing_record.py)
como `TypedDict` (e em `NORMALIZED_COLUMNS` como tuple para ordem de DataFrame).

### Identificação

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `user_id` | `str` (UUID format) | sim | Vem do JWT (`sub`) ou do dev bypass. |
| `job_id` | `str` (UUID format) | sim | = `report_id` no banco. |
| `provider` | `str` | sim | `aws` / `azure` / `gcp` / `oci`. Validado por Pandera. |
| `schema_version` | `str` | sim | Ex: `AWS_CUR2.0`, `AWS_CE_DAILY_BY_SERVICE`, `AZURE_EA`, `GCP_BILLING_STD`, `OCI_COST_REPORT`. |
| `account_id` | `str` | sim (`""` aceito) | ID da conta/subscription/projeto/tenancy. |
| `account_name` | `str \| None` | não | Nome legível quando o provider expõe. |

### Recurso

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `service` | `str` | sim | Nome normalizado do serviço. |
| `service_raw` | `str` | sim | Nome original do provider (preservar p/ debug). |
| `resource_id` | `str \| None` | não | ID específico (ARN, nome, OCID). |
| `resource_type` | `str \| None` | não | Tipo (instance/bucket/etc.) — preenchido quando disponível. |
| `region` | `str \| None` | não | Região do recurso. |

### Tempo

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `usage_start` | `datetime \| None` | não | tz-aware UTC. |
| `usage_end` | `datetime \| None` | não | tz-aware UTC. |
| `billing_period` | `str` | sim | Formato `YYYY-MM`; valida com regex `^\d{4}-\d{2}$`. |

### Custo — **nunca `float`**

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `cost` | `Decimal` | sim | Custo sem descontos. **Pode ser negativo** para créditos/ajustes. |
| `cost_discounted` | `Decimal` | sim | Custo com descontos/créditos aplicados (`cost + credits` no GCP, p.ex.). Pode ser negativo. |
| `currency` | `str` (3 chars) | sim | ISO-4217 — `USD`, `BRL`, `EUR`, etc. |

### Uso

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `usage_amount` | `Decimal \| None` | não | Quantidade consumida (horas, GB, etc.). |
| `usage_unit` | `str \| None` | não | Unidade da quantidade. |

### Metadados

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `tags` | `dict[str, str]` | sim (`{}` aceito) | Tags normalizadas. OCI usa colunas `tags/*`; GCP usa `labels[]`; Azure usa string JSON. |
| `is_correction` | `bool` | sim | `True` para linhas que **substituem** outras (OCI). Não somar — substituir. |
| `is_estimate` | `bool` | sim | `True` quando dados não finalizados (AWS sem `bill_invoice_id`). |

### Rastreabilidade

| Coluna | Tipo Python | Obrigatório | Notas |
|---|---|---|---|
| `raw_payload` | `dict` | sim | Linha original preservada — replay e auditoria. Hoje só em memória; persistência adiada. |
| `source_file` | `str` | sim | Nome do arquivo que originou a linha. |
| `line_hash` | `str` | sim | SHA-256 de `raw_payload` ordenado. Chave de dedup. |

## Convenções de `raw_payload`

`raw_payload` é JSONB livre por design — cada parser escolhe a forma de
preservar a linha original. **Convenções estabelecidas:**

- **CUR / Azure / GCP / OCI** (parsers densos): `raw_payload` = linha original
  da fonte (campos não vazios apenas), para replay 1:1.
- **AWS Cost Explorer** (pivotado, esparso): cada célula vira uma linha com
  `raw_payload = {date, service_column, cost_raw}` — pequeno, identifica a
  origem.
- **Markers semânticos** em `raw_payload` (não inventar novos sem
  documentar aqui):

| Marker | Quem emite | Significado |
|---|---|---|
| `scope: "global_zero"` | Cost Explorer parser | Linha "summary" de serviço que ficou em zero **durante todo o export**. Carrega `date_range` e `service_column`. Permite à IA detectar "recurso observado, sem cobrança no período" sem inflar a tabela mês a mês. Ver [`parsers.md`](parsers.md#política-de-emissão-de-cost0-cost-explorer). |

## Idempotência e dedup

- `line_hash` é SHA-256 do `raw_payload` com `json.dumps(sort_keys=True)`.
  Mesma linha do mesmo arquivo → mesmo hash, mesmo `Decimal`.
- `deduplicate(df)` em [`normalizer.py`](../app/infrastructure/file_ingestion/normalizer.py)
  remove duplicatas por `line_hash` antes da validação.
- Reingestão do mesmo `report_id`:
  `PgBillingItemRepository.delete_by_report` + `bulk_insert` em uma
  transação. **Não há `ON CONFLICT line_hash` no banco** — a unicidade é
  garantida pelo pipeline.

## Queries comuns (atalhos para a IA / dashboard)

```sql
-- 1) Top serviços por custo de um job
SELECT service, SUM(cost) AS total
  FROM billing_items
 WHERE user_id = :user AND report_id = :job
 GROUP BY service
 ORDER BY total DESC
 LIMIT 10;

-- 2) Custo mensal por provedor (para Módulo 2)
SELECT provider, billing_period, SUM(cost) AS total
  FROM billing_items
 WHERE user_id = :user
 GROUP BY provider, billing_period
 ORDER BY billing_period, provider;

-- 3) Serviços "zerados globalmente" no export (sinal para Módulo 3 / IA)
--    Apenas Cost Explorer emite essas linhas; ver parsers.md.
SELECT DISTINCT service
  FROM billing_items
 WHERE user_id = :user
   AND cost = 0
   AND tags = '{}'::jsonb
   AND is_correction = FALSE;
-- Filtro mais robusto usando raw_payload (quando coluna for persistida):
--   AND raw_payload->>'scope' = 'global_zero'

-- 4) Detectar drift (serviço ativo em um mês, zero em outro)
WITH per_month AS (
  SELECT service, billing_period, SUM(cost) AS m_cost
    FROM billing_items
   WHERE user_id = :user
   GROUP BY service, billing_period
)
SELECT service,
       MAX(m_cost) FILTER (WHERE m_cost > 0) AS max_month_cost,
       MIN(m_cost)                          AS min_month_cost
  FROM per_month
 GROUP BY service
HAVING MAX(m_cost) > 0 AND MIN(m_cost) = 0;
```

## Documentação relacionada

- [`architecture.md`](architecture.md#banco-de-dados) — regras de sessão e tipo.
- [`parsers.md`](parsers.md) — como cada provider produz o schema canônico.
- [`endpoints/billing.md`](endpoints/billing.md) — serialização Decimal→string no JSON.
- [`api-conventions.md`](api-conventions.md) — paginação, envelopes, status codes.
- [`sku-matching.md`](sku-matching.md) — `component_type`, `mapping_confidence`, `match_layer`, `sku_classification_rules` e a Matching Ladder.
