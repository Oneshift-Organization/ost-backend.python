# Pipeline de Ingestão de Billing Multi-Cloud

> **Propósito:** documentar o fluxo completo desde o upload de um arquivo de
> billing até a persistência dos line items normalizados, incluindo a lógica
> de detecção do provedor, os 7 parsers atualmente suportados, e como lidamos
> com os padrões radicalmente diferentes de cada cloud provider.
>
> **Público-alvo:** desenvolvedores que precisam entender, depurar ou estender
> a pipeline de ingestão.

---

## Sumário

1. [Visão geral do pipeline](#1-visão-geral-do-pipeline)
2. [Detecção do provedor (detector)](#2-detecção-do-provedor-detector)
3. [Os 7 parsers](#3-os-7-parsers)
   - [3.1 AWS CUR (Cost and Usage Report)](#31-aws-cur-cost-and-usage-report)
   - [3.2 AWS Cost Explorer](#32-aws-cost-explorer)
   - [3.3 AWS Detailed Billing (Invoice)](#33-aws-detailed-billing-invoice)
   - [3.4 Azure Cost Management Export](#34-azure-cost-management-export)
   - [3.5 GCP BigQuery Billing Export](#35-gcp-bigquery-billing-export)
   - [3.6 GCP Cost Table (Console BR)](#36-gcp-cost-table-console-br)
   - [3.7 OCI Cost Report](#37-oci-cost-report)
4. [Schema canônico do DataFrame](#4-schema-canônico-do-dataframe)
5. [Loaders — leitura de arquivos tabulares](#5-loaders--leitura-de-arquivos-tabulares)
6. [Pós-parse: dedup, validação, normalização](#6-pós-parse-dedup-validação-normalização)
7. [Edge cases críticos](#7-edge-cases-críticos)
8. [Fluxo de erro e fallbacks](#8-fluxo-de-erro-e-fallbacks)

---

## 1. Visão geral do pipeline

A pipeline completa executa neste fluxo:

```
POST /v1/billing-files  (rota billing.py:56-114)
└── valida extensão (.csv, .tsv, .gz, .zip, .json, .xlsx, .xlsm)
└── valida tamanho (max_upload_size_mb via settings)
└── cria billing_reports(QUEUED)
└── BackgroundTask: _run_ingest_isolated()  (billing.py:202-230)
    └── sm = get_sessionmaker()  ← sessão própria (request já fechou)
        └── IngestBillingFileUseCase.execute()  (ingest_billing_file.py:42-76)
            ├── report.mark_processing()
            ├── detect_and_parse(file_path, user_id, job_id)
            │   ├── detect_parser()  →  escolhe parser por conteúdo
            │   └── parser.parse()   →  DataFrame canônico
            ├── deduplicate(df)          ← drop line_hash duplicados
            ├── validate_normalized_df(df)  ← Pandera schema
            ├── dataframe_to_items(df)  ← NaN→None, BillingItem entities
            ├── delete_by_report()      ← idempotência (limpa dados velhos)
            ├── bulk_insert()           ← chunks de 1000, ON CONFLICT DO NOTHING
            └── report.mark_done(row_count, provider, schema_version)
```

**Por que BackgroundTask e não Celery?** Decisão MVP (2026-05-18). Parsing de
CSV típico leva < 30s. Celery + RabbitMQ estão diferidos para pós-MVP.

---

## 2. Detecção do provedor (detector)

Arquivo: `app/infrastructure/file_ingestion/detector.py`

### 2.1 Leitura da amostra

O detector lê os **primeiros 8KB** do arquivo via `_read_sample()`. Suporta:

| Extensão | Leitura |
|---|---|
| `.csv`, `.tsv`, `.txt` | `read_bytes()[:8192]` |
| `.gz` | `gzip.open() -> read(8192)` |
| `.zip` | Abre o primeiro CSV dentro do ZIP |
| `.xlsx`, `.xlsm` | Lê apenas o header (1ª linha) via openpyxl e sintetiza bytes simulando CSV |

### 2.2 Ordem dos parsers

Cada parser implementa `can_parse(content_sample: bytes) -> bool`. O detector
testa em **ordem definida** em `PARSERS`:

```python
PARSERS = [
    AWSParser,                # 1. CUR 2.0 / Legacy
    AWSCostExplorerParser,    # 2. Cost Explorer (wide/pivoted)
    AzureParser,              # 3. Azure Cost Export
    GCPParser,                # 4. GCP BigQuery export
    GcpCostTableParser,       # 5. GCP Cost Table (Console BR)
    OCIParser,                # 6. OCI Cost Report
    AwsDetailedBillingParser, # 7. AWS Invoice CSV
]
```

**Regra:** o primeiro parser cujo `can_parse()` retornar `True` vence. Se
nenhum reconhecer → `ProviderDetectionError`.

**Por que nesta ordem?** Assinaturas mais específicas primeiro, genéricas
depois:
- CUR (`line_item_unblended_cost`) é mais específico que CE (`"Service"` +
  `"Total costs($)"` — que poderia casar em outros arquivos pivotados).
- GCP BigQuery (`billing_account_id` + `usage_start_time`) é checado antes
  do Cost Table para evitar falso positivo.
- AWS Invoice (`ProductCode` + `CostBeforeTax`) é genérico demais para ficar
  no começo — pode conflitar se outro CSV tiver colunas similares.

### 2.3 Tabela de tokens de detecção

| Parser | Tokens no header | Formato esperado |
|---|---|---|
| `AWSParser` | `line_item_unblended_cost` **ou** `lineItem/UnblendedCost` | CUR 2.0 (snake_case) ou Legacy (camelCase) |
| `AWSCostExplorerParser` | `"Service"` **e** `"Total costs($)"` | Wide/pivoted (colunas = serviços, linhas = datas) |
| `AzureParser` | `"CostInBillingCurrency"` **e** `"MeterCategory"` | Pay-As-You-Go ou Enterprise Agreement |
| `GCPParser` | `"billing_account_id"` **e** `"usage_start_time"` | BigQuery export (JSON, NDJSON, CSV, XLSX) |
| `GcpCostTableParser` | `"Service description"` **e** `"Cost (R$)"` | Console GCP PT-BR (semicolon, BRL) |
| `OCIParser` | `"cost/myCost"` **e** `"lineItem/intervalUsageStart"` | OCI Cost Report (CSV.gz, XLSX) |
| `AwsDetailedBillingParser` | `"ProductCode"` **e** `"CostBeforeTax"` | AWS Invoice / Detailed Billing |

---

## 3. Os 7 parsers

Cada parser fica em `app/infrastructure/file_ingestion/parsers/` e segue o
contrato de `BaseParser`:

```python
class BaseParser(ABC):
    def __init__(self, user_id: UUID, job_id: UUID)
    @abstractmethod
    def can_parse(self, file_path: Path, content_sample: bytes) -> bool
    @abstractmethod
    def parse(self, file_path: Path) -> pd.DataFrame
    def get_schema_version(self) -> str
    def _cost_to_decimal(self, value) -> Decimal
    def _hash_row(self, row) -> str  # SHA-256 determinístico
```

### 3.1 AWS CUR (Cost and Usage Report)

**Arquivo:** `parsers/aws_parser.py`

**Schema versions:** `AWS_CUR2.0` | `AWS_CUR_LEGACY`

**Formato suportado:** CSV, CSV.gz, CSV.zip, XLSX/XLSM com colunas do CUR 2.0
(snake_case) ou CUR Legacy (camelCase com `/`).

**Mapeamento de colunas:** TODO parser usa dicionários `_CUR2_COLS` e
`_LEGACY_COLS` que traduzem nomes de coluna do CUR para chaves normalizadas:

| Chave normalizada | CUR 2.0 | CUR Legacy |
|---|---|---|
| `cost` | `line_item_unblended_cost` | `lineItem/UnblendedCost` |
| `cost_net` | `line_item_net_unblended_cost` | `lineItem/NetUnblendedCost` |
| `usage_start` | `line_item_usage_start_date` | `lineItem/UsageStartDate` |
| `service` | `line_item_product_code` | `lineItem/ProductCode` |
| `resource` | `line_item_resource_id` | `lineItem/ResourceId` |
| `region` | `product_region` | `product/region` |
| `currency` | `line_item_currency_code` | `lineItem/CurrencyCode` |
| `tags` | `resource_tags` | `resourceTags` |
| `invoice` | `bill_invoice_id` | `bill/InvoiceId` |

**Detecção da versão:** verificado em `df_raw.columns` — se
`line_item_unblended_cost` está presente → CUR 2.0, senão → Legacy.

**Edge cases:**
- `is_estimate = True` quando `bill_invoice_id` está vazio (linhas de dezembro
  ainda não faturadas).
- `cost_discounted` usa `line_item_net_unblended_cost` como fallback para
  `line_item_unblended_cost`.
- Tags são parseadas via `self._parse_tags()` que aceita JSON string ou dict.

### 3.2 AWS Cost Explorer

**Arquivo:** `parsers/aws_cost_explorer_parser.py`

**Schema version:** `AWS_CE_DAILY_BY_SERVICE`

**Formato:** arquivo **wide/pivoted** — colunas são nomes de serviço com
sufixo `($)`, linhas são datas, a primeira coluna é `"Service"`. Diferente
do CUR (formato longo, um line item por linha).

**Pipeline interno:**
1. `read_tabular()` carrega o CSV/TSV (sniffa delimitador)
2. Identifica colunas de serviço: todas que terminam em `($)` exceto
   `"Total costs($)"`
3. **Pass 1 — `_scan()`:** classifica serviços como **active** (cost > 0 em
   pelo menos uma data) vs **global_zero** (cost = 0 no mês inteiro)
4. **Pass 2a — `_emit_active_rows()`:** para serviços ativos, emite TODOS os
   dias (incluindo cost=0) — o agente IA precisa ver o drift
5. **Pass 2b — `_emit_global_zero_rows()`:** para serviços global-zero, emite
   **1 summary row** por billing_period com `raw_payload.scope = "global_zero"`

**Política global_zero (importante):**
- Serviço com cost > 0 em qualquer dia → "ativo": emite linhas diárias,
  inclusive cost=0 (sinal pro agente: "serviço zerou este mês")
- Serviço com cost = 0 no período inteiro → "global-zero": 1 resumo por mês
  com `raw_payload.scope = "global_zero"` (sinal: "serviço observado, nunca
  cobrado")

**Locale decimal:** Cost Explorer exporta conforme o locale do Excel:
- en-US: `"1369.5165"` → `Decimal("1369.5165")`
- pt-BR: `"1369,5165"` → `Decimal("1369.5165")`
- Misto (milhar + decimal): `"1,369.5165"` ou `"1.369,5165"` → detecta pela
  posição do último separador

**Linha de resumo:** A primeira linha geralmente é `"Service total"` (soma
de todos os serviços por data) → **descartada** para evitar dupla contagem.

### 3.3 AWS Detailed Billing (Invoice)

**Arquivo:** `parsers/aws_invoice_parser.py`

**Schema version:** `AWS_DETAILED_BILLING`

**Formato:** CSV linha-item do AWS Billing Console, formato "Detailed Billing
Report" (também chamado de Invoice CSV no console). É o formato mais básico —
uma linha por uso, sem pivotamento.

**Colunas esperadas:**
```
InvoiceID, PayerAccountId, LinkedAccountId, RecordType,
BillingPeriodStartDate, BillingPeriodEndDate,
ProductCode, UsageType, Operation,
UsageStartDate, UsageEndDate,
UsageQuantity, BlendedRate, CurrencyCode,
CostBeforeTax, Credits, TaxAmount, TotalCost
```

**Mapeamento:**
| Campo canônico | Fonte |
|---|---|
| `service` | `ProductCode` (ex: `"AmazonEC2"`) |
| `resource_type` | `UsageType` (ex: `"BoxUsage:c4.4xlarge"`) |
| `cost` | `CostBeforeTax` |
| `currency` | `CurrencyCode` |
| `account_id` | `LinkedAccountId` |
| `usage_amount` | `UsageQuantity` |
| `usage_unit` | `"hours"` se UsageType começa com `BoxUsage` |
| `usage_start` | `BillingPeriodStartDate` (formato `YYYY/MM/DD HH:MM:SS`) |

**Limpeza de NaN:** Usa `_clean()` helper que trata `pd.isna()`, `None`,
string `"nan"`, `"none"`, `"null"` e string vazia → retorna `""`.

### 3.4 Azure Cost Management Export

**Arquivo:** `parsers/azure_parser.py`

**Schema versions:** `AZURE_EA` | `AZURE_PAYG`

**Dois dialetos detectados por nome de coluna:**

| Coluna | PAYG | EA (Enterprise Agreement) |
|---|---|---|
| Data | `Date` | `UsageDateTime` |
| Subscription | `SubscriptionGuid` | `SubscriptionId` |

**Colunas comuns:** `CostInBillingCurrency`, `MeterCategory`, `ServiceName`,
`ResourceId`, `MeterSubCategory`, `ResourceLocation`, `BillingCurrency`,
`Quantity`, `UnitOfMeasure`, `Tags`, `SubscriptionName`.

**Detecção:** `"CostInBillingCurrency"` + `"MeterCategory"` no sample.

**Encoding:** Azure CSV exports geralmente têm UTF-8 BOM. O parser lê com
`utf-8-sig`.

### 3.5 GCP BigQuery Billing Export

**Arquivo:** `parsers/gcp_parser.py`

**Schema version:** `GCP_BILLING_STD`

**Formatos aceitos:**
- NDJSON (um JSON object por linha) — formato nativo da exportação BigQuery
- JSON array
- CSV com mesmo schema de colunas
- XLSX/XLSM (exportações manuais via Looker Studio)

**Campos aninhados:** GCP usa JSON aninhado. O parser acessa via `_nested()`
helper que navega `dict.get()`:

| Campo canônico | Caminho JSON |
|---|---|
| `service` | `service.description` (fallback: `service.id`) |
| `account_id` | `billing_account_id` |
| `account_name` | `project.name` |
| `resource_id` | `resource.name` (fallback: `resource.global_name`) |
| `resource_type` | `sku.description` |
| `region` | `location.region` |
| `usage_amount` | `usage.amount` |
| `usage_unit` | `usage.unit` |
| `tags` | `labels[]` (array de `{key, value}`) |
| `is_correction` | `cost_type in ("adjustment", "rounding_error")` |

**Créditos:** GCP entrega créditos como um array `credits[]` com
`{name, amount, type}`. O parser soma ao `cost_discounted` (créditos são
tipicamente negativos).

**Importante:** GCP `cost` é FLOAT no BigQuery. O parser **nunca** lê como
float — converte via `Decimal(str(value))`.

### 3.6 GCP Cost Table (Console BR)

**Arquivo:** `parsers/gcp_cost_table_parser.py`

**Schema version:** `GCP_COST_TABLE`

**Formato:** CSV exportado do Console Google Cloud → "Cost Table" → "Download
CSV". Delimitado por `;`, colunas em português, valores em R$ (BRL).

**Colunas:**
```
Billing account name; Billing account ID; Project name; Project ID;
Project hierarchy; Service description; Service ID;
SKU description; SKU ID; Consumption model description;
Credit type; Cost type;
Usage start date; Usage end date;
Usage amount; Usage unit;
Unrounded Cost (R$); Cost (R$)
```

**Locale brasileiro:** Este formato usa `.` como separador decimal e `,`
como separador de milhar:
- `"4.38"` → `Decimal("4.38")`
- `"1,489"` → `Decimal("1489")` (comma é milhar, não decimal)
- `"1,564.84"` → `Decimal("1564.84")`

O parser usa `_br_to_decimal()` que **remove todas as vírgulas** antes da
conversão (diferente do `_cost_to_decimal` genérico da `BaseParser`, que
trata comma como possível decimal europeu).

**is_correction:** Marcado como `True` quando:
1. `Cost type` é `"adjustment"` ou `"rounding_error"`, **ou**
2. `cost < 0` (créditos como `SPENDING_BASED_DISCOUNT` aparecem como linhas
   de custo negativo com `Cost type = Usage`)

**Moeda:** fixo `"BRL"` — este formato só exporta em Reais.

### 3.7 OCI Cost Report

**Arquivo:** `parsers/oci_parser.py`

**Schema version:** `OCI_COST_REPORT`

**Formato nativo:** CSV.gz com 6 arquivos por dia (um a cada 4h). Também
aceita XLSX/XLSM.

**Colunas com `/` no nome:** OCI usa `/` como namespace separator:
```
cost/myCost, cost/currencyCode,
lineItem/intervalUsageStart, lineItem/intervalUsageEnd,
lineItem/isCorrection, lineItem/backReference, lineItem/referenceNo,
product/service, product/resourceId, product/resource, product/region,
usage/billedQuantity, usage/billedUnit,
tenantId, compartmentName
```

**Tags dinâmicas:** OCI tem colunas de tag com namespace `tags/`. O parser
coleta **todas** as colunas que começam com `tags/` e monta um dict:

```python
tags = {
    col[len("tags/"):]: str(val)
    for col, val in row.items()
    if col.startswith("tags/") and val
}
```

**Correções (edge case crítico):** OCI tem linhas de correção
(`lineItem/isCorrection = true`) que **substituem** as linhas originais
(referenciadas por `lineItem/backReference`). O método
`_apply_corrections()`:
1. Identifica linhas com `isCorrection == true`
2. Coleta os `lineItem/backReference` (que apontam para `lineItem/referenceNo`
   das linhas originais)
3. **Remove** as linhas originais referenciadas
4. **Mantém** as linhas de correção (que têm o valor correto)

Nunca somar correções com originais — isso geraria double-count.

---

## 4. Schema canônico do DataFrame

**Arquivo:** `app/infrastructure/file_ingestion/normalizer.py`
**Validação:** `app/infrastructure/file_ingestion/validator.py`

TODOS os parsers devolvem um DataFrame com exatamente estas 25 colunas:

| Coluna | Tipo Python | Exemplo | Obrigatório | Origem |
|---|---|---|---|---|
| `user_id` | `str` (UUID) | `"00000000-..."` | Sim | Injetado pelo contrutor do parser |
| `job_id` | `str` (UUID) | `"abc123..."` | Sim | Injetado pelo construtor do parser |
| `provider` | `str` | `"aws"` | Sim | Fixo de cada parser |
| `schema_version` | `str` | `"AWS_CUR2.0"` | Sim | `self._set_version()` |
| `account_id` | `str` | `""` | Sim | Coluna do arquivo |
| `account_name` | `str \| None` | `None` | Não | Coluna do arquivo |
| `service` | `str` | `"AmazonEC2"` | Sim | Nome do serviço |
| `service_raw` | `str` | `"AmazonEC2"` | Sim | Nome original (pode ter sufixos) |
| `resource_id` | `str \| None` | `None` | Não | ID do recurso |
| `resource_type` | `str \| None` | `"BoxUsage:c4.4xlarge"` | Não | Tipo do recurso / SKU |
| `region` | `str \| None` | `None` | Não | Região do recurso |
| `usage_start` | `datetime \| None` | `2026-03-01 00:00:00+00:00` | Não | Início do uso |
| `usage_end` | `datetime \| None` | `2026-04-01 00:00:00+00:00` | Não | Fim do uso |
| `billing_period` | `str` | `"2026-03"` | Sim | `YYYY-MM` |
| `cost` | `Decimal` | `Decimal("592.224")` | Sim | Custo no período |
| `cost_discounted` | `Decimal` | `Decimal("592.224")` | Sim | Custo com descontos/créditos |
| `currency` | `str` | `"USD"` | Sim | ISO-4217 |
| `usage_amount` | `Decimal \| None` | `Decimal("744")` | Não | Quantidade de uso |
| `usage_unit` | `str \| None` | `"hours"` | Não | Unidade da quantidade |
| `tags` | `dict[str, str]` | `{}` | Sim | Tags/labels do recurso |
| `is_correction` | `bool` | `False` | Sim | É linha de ajuste/crédito |
| `is_estimate` | `bool` | `False` | Sim | É linha não faturada |
| `raw_payload` | `dict` | `{...}` | Sim | Linha original completa |
| `source_file` | `str` | `"report.csv"` | Sim | Nome do arquivo original |
| `line_hash` | `str` | SHA-256 hex | Sim | Hash determinístico do raw_payload |

**Regras inegociáveis para implementar um parser:**
1. `cost` **nunca** é `float` — sempre `Decimal(str(valor))`
2. `currency` sempre ISO-4217 de 3 caracteres (ou `"USD"` fallback)
3. `billing_period` sempre `YYYY-MM`
4. `line_hash` é SHA-256 determinístico de `json.dumps(raw_payload, sort_keys=True)`
5. `raw_payload` preserva a linha original para replay/debug
6. Toda linha deve ter `user_id` e `job_id` preenchidos

---

## 5. Loaders — leitura de arquivos tabulares

**Arquivo:** `app/infrastructure/file_ingestion/loaders.py`

### 5.1 `read_tabular(file_path)`

Função central que lê qualquer formato tabular com `dtype=str` e normaliza
NaN → `""`.

**Pipeline de leitura:**

```
Path → detecta extensão + magic bytes
├── .xlsx/.xlsm com ZIP magic → pd.read_excel(dtype=str, sheet_name=0)
├── .csv.gz → gzip + sniff delimitador
├── .csv.zip → extrai primeiro CSV + sniff
└── qualquer outro → sniff delimitador direto
↓
normalize_columns(df)  ← limpa BOM, zero-width chars, quotes
```

**Sniffing de delimitador:** Pandas `sep=None + engine="python"` usa
`csv.Sniffer` para detectar `,` vs `\t` vs `;` automaticamente.

**Fallback de encoding:** Tenta nesta ordem:
1. `utf-8-sig` (BOM-aware)
2. `latin-1` (para arquivos Excel legacy)

### 5.2 `normalize_columns(df)`

Remove artefatos comuns dos nomes de coluna:
- BOM UTF-8 (`U+FEFF`) no início do nome
- Mis-encoded UTF-8 BOM em latin-1 (`ï»¿`)
- Zero-width chars (`U+200B`, `U+200C`, `U+200D`)
- Wrapping quotes (`"aspas"` ou `'aspas'`)
- Whitespace externo

---

## 6. Pós-parse: dedup, validação, normalização

### 6.1 Deduplicação (`normalizer.py:52-56`)

```python
def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop_duplicates(subset=["line_hash"], keep="first")
```

Se um mesmo arquivo for enviado duas vezes, ou se dois arquivos diferentes
tiverem linhas idênticas, a segunda ocorrência é ignorada.

### 6.2 Validação Pandera (`validator.py`)

O schema `BillingNormalizedSchema` valida:

| Coluna | Check |
|---|---|
| `user_id`, `job_id` | Regex UUID |
| `provider` | `in {"aws", "azure", "gcp", "oci"}` |
| `cost` | `isinstance(Decimal)` (relaxado: aceita negativos para créditos) |
| `currency` | `len == 3` |
| `billing_period` | Regex `^\d{4}-\d{2}$` |
| `line_hash` | `len == 64` (SHA-256 hex) |

`strict=False` + `coerce=False` → colunas extras não documentadas no schema
são ignoradas (e.g., colunas intermediárias do parser que não são persistidas).

**Nota sobre cost negativo:** Originalmente o schema exigia `cost >= 0`, mas
créditos GCP (SPENDING_BASED_DISCOUNT) aparecem como custo negativo legítimo.
A validação foi relaxada para apenas `isinstance(Decimal)` — a consistência
de `is_correction` para custos negativos é responsabilidade de cada parser.

### 6.3 Normalização NaN → None (`normalizer.py`)

**Problema:** Pandas converte `None` (Python) → `float('nan')` no DataFrame.
Quando `df.to_dict(orient="records")` é chamado, esses `float('nan')`) vão
para os registros. O `BillingItem` entity tem campos `Optional[str]` — se
receber `float('nan')` em vez de `None`, o dado vai para o INSERT como NaN
float, quebrando no PostgreSQL.

**Solução:** `_nan_to_none()` converte qualquer valor que satisfaça
`pd.isna()` de volta para `None` antes de construir o `BillingItem`.

### 6.4 Persistência (`pg_billing_item_repo.py`)

```python
async def bulk_insert(self, items: list[BillingItem]) -> int:
    rows = [self._to_row(it) for it in items]
    for i in range(0, len(rows), _BATCH_SIZE):  # BATCH_SIZE = 1000
        chunk = rows[i : i + _BATCH_SIZE]
        stmt = pg_insert(BillingItemModel).values(chunk)
        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
        result = await self._session.execute(stmt)
```

**Idempotência:** Antes do bulk insert, o use case chama
`delete_by_report(report_id, user_id)` — se o mesmo job for reprocessado,
os dados antigos são limpos. O `ON CONFLICT DO NOTHING` pelo `id` previne
duplicatas em caso de re-execução parcial.

---

## 7. Edge cases críticos

### 7.1 Locale decimal (Cost Explorer)

Cost Explorer exporta conforme o locale do Excel do usuário. A função
`_locale_to_decimal()` em `aws_cost_explorer_parser.py`:
- Presença de `,` e `.` → último separador é o decimal
- Só `,` → comma é decimal (europeu)
- Só `.` → dot é decimal (padrão)

### 7.2 Milhar BR (GCP Cost Table)

Formato brasileiro usa `,` como milhar e `.` como decimal. A função
`_br_to_decimal()` **remove todas as vírgulas** antes de converter:
`"1,489"` → `"1489"`, `"1,564.84"` → `"1564.84"`.

**Diferente do `_cost_to_decimal` da BaseParser** que trata comma único como
decimal europeu. Cada parser usa a função adequada ao seu formato.

### 7.3 NaN propagation

| Origem | Como aparece | Onde é tratado |
|---|---|---|
| `None` no dict do parser | `float('nan')` no DataFrame | `_nan_to_none()` no normalizer |
| Célula vazia no CSV | `""` via `read_tabular().fillna("")` | Já limpo |
| `raw_payload` com NaN | `"nan"` string | `_clean()` helper no parser |

### 7.4 Custos negativos (créditos)

GCP e OCI emitem linhas de crédito com custo negativo. O `BillingItem`
valida: `if self.cost < 0 and not self.is_correction: raise`. Cada parser
marca `is_correction=True` quando:
- GCP BigQuery: `cost_type in ("adjustment", "rounding_error")`
- GCP Cost Table: `cost < 0` **ou** `cost_type` de adjustment/rounding_error
- OCI: `lineItem/isCorrection == "true"`
- AWS/ Azure: sempre `False` (não têm créditos negativos no formato)

### 7.5 Correções OCI (substituem originais)

OCI não segue o padrão de "nota de crédito". Linhas de correção
(`isCorrection=true`) contêm o valor correto e referenciam a linha original
via `backReference`. O parser **remove a linha original** e **mantém a
correção**.

### 7.6 Global-zero (Cost Explorer)

Serviços com custo zero no mês inteiro viram **1 row por billing_period**
com `raw_payload.scope = "global_zero"`. Isso permite:
- O agente IA saber que o serviço existe na conta
- Não inflar a tabela com 30 linhas de $0.00
- Diferenciar de "serviço que zerou no meio do mês" (que emite linhas diárias)

### 7.7 Tags dinâmicas (OCI)

OCI não tem colunas de tag fixas — cada tenant pode ter tags diferentes. O
parser coleta **todas** as colunas com prefixo `tags/` dinamicamente.

### 7.8 BOM e encoding

Arquivos de billing frequentemente têm UTF-8 BOM. O `loaders.py` tenta
`utf-8-sig` primeiro, depois `latin-1`. Colunas com BOM mis-encoded
(`ï»¿Nome`) são limpas em `normalize_columns()`.

### 7.9 Wide-to-long melt (Cost Explorer)

Cost Explorer é o **único** parser wide/pivoted (colunas = serviços). Os
demais são long (um line item por linha). O CE parser faz um melt
programático: identifica colunas `Service` + `*($)`, itera linha a linha,
e produz um registro por (data, serviço).

---

## 8. Fluxo de erro e fallbacks

### 8.1 Falha no parse

Se `detect_and_parse()` falha (formato não reconhecido, arquivo corrompido):

```
IngestBillingFileUseCase.execute()
  → detect_and_parse() levanta ProviderDetectionError
  → except Exception → report.mark_failed(str(e))
  → raise → _run_ingest_isolated()
  → rollback() → await _persist_failure()
```

`_persist_failure()` cria uma **nova sessão limpa** (depois do rollback) para
gravar o status `FAILED`. Isso garante que a mensagem de erro sobreviva mesmo
que a transação principal tenha sido revertida.

### 8.2 Falha na validação Pandera

Se o DataFrame não passa no schema (coluna faltando, tipo errado):

```
validate_normalized_df() → raise ValidationError
  → capturado pelo mesmo except em execute()
```

### 8.3 Falha no INSERT

Se o bulk insert falha (e.g., NaN escapou, constraint violada):

```
bulk_insert() → raise
  → capturado pelo mesmo except
```

### 8.4 Cleanup

No `finally` de `_run_ingest_isolated()`:

```python
finally:
    with contextlib.suppress(OSError):
        file_path.unlink(missing_ok=True)
```

O arquivo temporário é sempre removido, independente de sucesso ou falha.

---

## Referências

| O quê | Onde |
|---|---|
| Código da rota | `app/api/v1/routes/billing.py` |
| Use case de ingestão | `app/use_cases/billing/ingest_billing_file.py` |
| Detector | `app/infrastructure/file_ingestion/detector.py` |
| Base parser | `app/infrastructure/file_ingestion/base_parser.py` |
| Parsers | `app/infrastructure/file_ingestion/parsers/` |
| Loaders | `app/infrastructure/file_ingestion/loaders.py` |
| Normalizer | `app/infrastructure/file_ingestion/normalizer.py` |
| Validator | `app/infrastructure/file_ingestion/validator.py` |
| Repositório de items | `app/infrastructure/repositories/pg_billing_item_repo.py` |
| BillingItem entity | `app/domain/entities/billing_item.py` |
| Schema de dados | `docs/data-model.md` |
| Guia de parsers (original) | `docs/parsers.md` |
