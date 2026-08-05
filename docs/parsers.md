# Parsers — guia por provider

Como cada provedor de cloud é convertido para o
[schema canônico](data-model.md#schema-canônico-do-parser), o que cada formato
tem de armadilha, e como adicionar uma cloud nova.

> **Audiência:** quem vai adicionar um parser novo, debugar um existente, ou
> entender por que `cost=0` às vezes vira linha e às vezes não. Para regras de
> Clean Arch, ver [`architecture.md`](architecture.md).

## Visão geral

```
upload → detect_parser (por conteúdo)
       → parser.parse() → DataFrame canônico
       → deduplicate(line_hash)
       → validate_normalized_df (Pandera)
       → PgBillingItemRepository.bulk_insert
```

Hoje há **7 parsers**, todos em
[`app/infrastructure/file_ingestion/parsers/`](../app/infrastructure/file_ingestion/parsers/):

| Parser | Formato | `schema_version` emitido | Status |
|---|---|---|---|---|
| `AWSParser` | AWS CUR 2.0 + Legacy CUR (.csv/.csv.gz/.csv.zip/.xlsx) | `AWS_CUR2.0` ou `AWS_CUR_LEGACY` | ✅ |
| `AWSCostExplorerParser` | AWS Cost Explorer "Daily by service" (TSV/CSV pivotado, BOM, locale) | `AWS_CE_DAILY_BY_SERVICE` | ✅ |
| `AwsDetailedBillingParser` | AWS Invoice / Detailed Billing Report | `AWS_DETAILED_BILLING` | ✅ |
| `AzureParser` | Azure Cost Management Export (PAYG / EA) | `AZURE_PAYG` ou `AZURE_EA` | ✅ |
| `GCPParser` | GCP Cloud Billing Export (NDJSON / JSON / CSV / XLSX) | `GCP_BILLING_STD` | ✅ |
| `GcpCostTableParser` | GCP Cost Table (Console BR, semicolon, BRL) | `GCP_COST_TABLE` | ✅ |
| `OCIParser` | OCI Cost Report (.csv.gz proprietário) | `OCI_COST_REPORT` | ✅ |

> **Ordem de detecção importa.** Em [`detector.PARSERS`](../app/infrastructure/file_ingestion/detector.py),
> o CUR é testado **antes** do Cost Explorer porque a assinatura do CUR
> (`line_item_unblended_cost`) é mais específica; a do CE é genérica
> (`"Service"` + `"Total costs($)"`).

## Contrato `BaseParser`

Definido em [`base_parser.py`](../app/infrastructure/file_ingestion/base_parser.py).
Todo parser **DEVE**:

```python
class BaseParser(ABC):
    def __init__(self, user_id: UUID, job_id: UUID) -> None: ...

    @abstractmethod
    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        """Identifica se este parser reconhece o arquivo. NÃO levanta exceção."""

    @abstractmethod
    def parse(self, file_path: Path) -> pd.DataFrame:
        """Lê o arquivo → DataFrame no schema canônico. Chama `_set_version()`."""
```

Helpers oferecidos pela base:

- `_cost_to_decimal(value)` — conversão segura via string. **Nunca** `Decimal(float)`.
- `_hash_row(row)` — SHA-256 ordenado, base do `line_hash`.
- `_parse_tags(raw)` — normaliza tags (dict, JSON string ou `None`) para `dict[str, str]`.
- `_set_version(v)` / `get_schema_version()` — qual variante o parser detectou.

E **toda saída** vai pelo schema canônico (ver [`data-model.md`](data-model.md#schema-canônico-do-parser))
antes de virar `BillingItem` persistido.

---

## AWS CUR (Cost and Usage Report)

Arquivo: [`aws_parser.py`](../app/infrastructure/file_ingestion/parsers/aws_parser.py)

### Formato

CUR é o export "oficial" da AWS, entregue em S3. Suporta:

- **CUR 2.0** — colunas em `snake_case` (`line_item_unblended_cost`).
- **Legacy CUR** — colunas com `/` (`lineItem/UnblendedCost`).
- Extensões: `.csv`, `.csv.gz`, `.csv.zip`, `.xlsx`, `.xlsm`.

### Assinatura para detecção

Presença de `line_item_unblended_cost` **OU** `lineItem/UnblendedCost` no
sample de 8 KB inicial.

### Colunas críticas

| Schema canônico | CUR 2.0 | Legacy CUR |
|---|---|---|
| `cost` | `line_item_unblended_cost` | `lineItem/UnblendedCost` |
| `cost_discounted` | `line_item_net_unblended_cost` | `lineItem/NetUnblendedCost` |
| `usage_start` | `line_item_usage_start_date` | `lineItem/UsageStartDate` |
| `service` | `line_item_product_code` | `lineItem/ProductCode` |
| `resource_id` | `line_item_resource_id` | `lineItem/ResourceId` |
| `account_id` | `bill_payer_account_id` | `bill/PayerAccountId` |
| `region` | `product_region` | `product/region` |
| `billing_period` | `bill_billing_period_start_date[:7]` | `bill/BillingPeriodStartDate[:7]` |
| `currency` | `line_item_currency_code` | `lineItem/CurrencyCode` |
| `tags` | `resource_tags` (JSON string) | `resourceTags` |
| `is_estimate` | `not bool(bill_invoice_id)` | `not bool(bill/InvoiceId)` |

### Armadilhas

- `cost` chega como **string** no CSV (`dtype=str` no read_csv). Conversão via
  `_cost_to_decimal` (passa por `str`).
- `bill_invoice_id` **vazio** = dados não finalizados → `is_estimate=True`.
- Schema do Legacy CUR pode variar mensalmente; detectar pela presença das
  colunas snake_case vs slash, não pela extensão do arquivo.
- Múltiplos arquivos por mês (split por tamanho) — cada upload processa **um**
  CSV; quem orquestra múltiplos arquivos é o cliente, não o parser.

---

## AWS Cost Explorer (Daily by service)

Arquivo: [`aws_cost_explorer_parser.py`](../app/infrastructure/file_ingestion/parsers/aws_cost_explorer_parser.py)

### Formato

Esse é o CSV que vem de **"Download CSV"** dentro do AWS Cost Explorer UI —
**não é o CUR**. Características:

- **TSV** (tab-separated) por default; comma-separated também ocorre (sniff).
- **UTF-8 BOM** comum (`utf-8-sig`).
- **Pivotado / wide:** colunas = serviços, linhas = datas. O parser melta para
  long-form.
- **Linha 1** geralmente é `"Service total"` — rollup por coluna. **Dropada**
  para não duplicar.
- **Última coluna** é `"Total costs($)"` — soma por linha. **Dropada**.
- **Sufixo `($)`** nos headers de serviço: `"EC2-Instances($)"`.
- **Locale decimal:** pt-BR usa `","` (`1369,5165`), en-US usa `"."`. O helper
  `_locale_to_decimal` resolve ambos + thousand separators mistos.

### Assinatura

Coluna `"Service"` **E** coluna `"Total costs($)"` presentes no sample.

### Política de emissão de `cost=0` (Cost Explorer)

Esta é a regra **mais importante** do parser, porque define o que o agente de
IA de migração consegue ver. **Específica do Cost Explorer**; CUR/Azure/GCP/OCI
**não** aplicam.

#### O problema

CE é pivotado: após o melt, cada `(data × serviço)` vira uma linha. A maioria
das células é zero (90 dias × 200 serviços = 18k linhas, ~10% com custo).
Emitir tudo infla o banco; descartar tudo perde sinal de "recurso foi
observado".

#### A política

```python
# Pass 1: classificar serviços
active_services      = colunas com cost > 0 em pelo menos UMA data
global_zero_services = colunas com cost == 0 em TODAS as datas

# Pass 2a: serviços ATIVOS
#   → emitir linha por dia, INCLUSIVE dias com cost=0
#   → permite à IA detectar "serviço zerou neste mês"
#   → raw_payload: {date, service_column, cost_raw}

# Pass 2b: serviços GLOBAL ZERO
#   → emitir UMA linha por billing_period (não por dia)
#   → usage_start/usage_end cobrem o range completo do mês
#   → cost = Decimal("0")
#   → raw_payload: {scope: "global_zero", service_column, date_range, billing_period}
#   → permite à IA saber "recurso observado no account, sem cobrança"
#     sem inflar billing_items mês a mês
```

#### Como a IA consulta

```sql
-- Serviços que zeraram em um mês específico (drift detection)
SELECT service, billing_period
  FROM billing_items
 WHERE user_id = :user
   AND cost = 0
   -- e a linha NÃO é um summary global_zero
   -- (placeholder: até raw_payload virar coluna persistida)
;

-- Serviços observados mas nunca cobrados (free tier / RIs / créditos)
SELECT service
  FROM billing_items
 WHERE user_id = :user
   AND cost = 0
   -- AND raw_payload->>'scope' = 'global_zero'    (quando coluna existir)
;
```

#### Por que essa escolha

O CE **não exporta** `usage_amount` nem `resource_id` — só custo agregado por
`(data × serviço)`. Então `cost=0` no CE é intrinsecamente ambíguo (free tier,
reserved, créditos, ou não-uso). Para sinal forte de "recurso em uso", o
caminho é o **CUR** (que traz `usage_amount` + `resource_id`).

Mesmo dentro do limite do CE, a distinção entre "ativo com zero pontual" e
"global zero compactado" preserva o máximo de sinal sem explodir cardinalidade.

### Armadilhas

- BOM nas duas primeiras linhas — usar `encoding="utf-8-sig"` ou strip manual.
- Header com `($)` é parte do nome — strippar antes de virar `service`.
- Decimal locale: `1.369,5165` (pt-BR) vs `1,369.5165` (en-US). O helper
  `_locale_to_decimal` decide pelo **último** separador.
- `"Service total"` é diferente de `"Service"` (header). Skip explícito.

---

## Azure Cost Management Export

Arquivo: [`azure_parser.py`](../app/infrastructure/file_ingestion/parsers/azure_parser.py)

### Formato

Export do Azure Cost Management para Blob Storage. Suporta:

- **PAYG** (Pay-As-You-Go): coluna `Date`, `SubscriptionGuid`.
- **EA** (Enterprise Agreement): coluna `UsageDateTime`, `SubscriptionId`.
- **MCA** (Microsoft Customer Agreement): usa FOCUS por default — não coberto
  ainda; adicionar parser específico ou estender.
- Extensões: `.csv` (UTF-8 BOM aware), `.csv.gz`, `.xlsx`, `.xlsm`. Parquet
  via pyarrow está diferido.

### Assinatura

Presença simultânea de `CostInBillingCurrency` **E** `MeterCategory`.

### Colunas críticas

| Schema canônico | Azure |
|---|---|
| `cost` | `CostInBillingCurrency` |
| `usage_start` / `usage_end` | `Date` (PAYG) / `UsageDateTime` (EA) — mesmo valor em ambos |
| `service` | `ServiceName` ou `MeterCategory` (fallback) |
| `account_id` | `SubscriptionGuid` (PAYG) / `SubscriptionId` (EA) |
| `account_name` | `SubscriptionName` |
| `region` | `ResourceLocation` |
| `resource_id` | `ResourceId` |
| `resource_type` | `MeterSubCategory` |
| `currency` | `BillingCurrency` |
| `tags` | `Tags` (string JSON) |

### Armadilhas

- `SubscriptionGuid` ≠ `SubscriptionId` — mesmo dado, **nomes diferentes** por
  tipo de contrato. Detectar pela presença das colunas em runtime.
- `Date` ≠ `UsageDateTime` — idem.
- Decimais às vezes truncados por ferramentas intermediárias (Excel, BI tools).
  Ler sempre como string e converter via `_cost_to_decimal`.
- Export pode vir em múltiplos arquivos (partitioning automático). Cliente
  precisa orquestrar; o parser processa um arquivo por vez.

---

## GCP Cloud Billing Export

Arquivo: [`gcp_parser.py`](../app/infrastructure/file_ingestion/parsers/gcp_parser.py)

### Formato

Dados vivem nativamente no BigQuery; export para JSON/NDJSON/CSV/XLSX.

- **Tabela standard:** `gcp_billing_export_v1_{BILLING_ACCOUNT_ID}`.
- **Tabela detailed:** `gcp_billing_export_resource_v1_{BILLING_ACCOUNT_ID}`.

### Assinatura

Presença de `billing_account_id` **E** `usage_start_time`.

### Colunas críticas

| Schema canônico | GCP |
|---|---|
| `cost` | `cost` (**FLOAT** no BigQuery — converter via string!) |
| `cost_discounted` | `cost + sum(credits[*].amount)` (credits são negativos) |
| `usage_start` / `usage_end` | `usage_start_time` / `usage_end_time` |
| `service` | `service.description` (fallback `service.id`) |
| `account_id` | `billing_account_id` |
| `account_name` | `project.name` |
| `resource_id` | `resource.name` ou `resource.global_name` |
| `resource_type` | `sku.description` |
| `region` | `location.region` |
| `usage_amount` | `usage.amount` |
| `usage_unit` | `usage.unit` |
| `tags` | `labels[]` ({key, value}) |
| `is_correction` | `cost_type in {"adjustment", "rounding_error"}` |
| `billing_period` | `invoice.month` (YYYYMM → YYYY-MM) ou fallback de `usage_start` |

### Armadilhas críticas

- **`cost` é FLOAT** no BigQuery — **NUNCA somar diretamente**. Caminho seguro:
  ler como string, `Decimal(str(value))`. O parser faz isso via `_cost_to_decimal`.
- **`credits`** é array de `{name, amount, type}` — somar em `cost_discounted`.
  Não somar em `cost`.
- **`labels`** é array de `{key, value}` (≠ Azure que é JSON string). Achatar
  via `_labels_to_dict`.
- Schema pode mudar sem aviso — preferir BigQuery **views** para proteger
  queries downstream.
- Sem backfill se export não estava habilitado — dados históricos podem ser
  incompletos. Não inferir nada do silêncio.

---

## OCI Cost Report

Arquivo: [`oci_parser.py`](../app/infrastructure/file_ingestion/parsers/oci_parser.py)

### Formato

Cost Report proprietário da Oracle, entregue em Object Storage.

- **Formato nativo:** `.csv.gz`. Múltiplos arquivos por dia (gerado a cada
  6h).
- **Também aceita:** `.xlsx` / `.xlsm` (exports manuais de FinOps).
- **FOCUS:** disponível em `FOCUS/{yyyy}/{mm}/{dd}/` — não coberto ainda;
  adicionar parser específico se necessário.

### Assinatura

Presença simultânea de `cost/myCost` **E** `lineItem/intervalUsageStart`.

### Colunas críticas

| Schema canônico | OCI |
|---|---|
| `cost` | `cost/myCost` |
| `usage_start` / `usage_end` | `lineItem/intervalUsageStart` / `lineItem/intervalUsageEnd` |
| `service` | `product/service` |
| `account_id` | `tenantId` |
| `account_name` | `compartmentName` |
| `resource_id` | `product/resourceId` |
| `resource_type` | `product/resource` |
| `region` | `product/region` |
| `usage_amount` | `usage/billedQuantity` |
| `usage_unit` | `usage/billedUnit` |
| `tags` | colunas `tags/*` (dinâmicas — cada tag vira coluna) |
| `is_correction` | `lineItem/isCorrection == "true"` |
| `currency` | `cost/currencyCode` |

### Armadilhas

- **Correções substituem, não somam.** Linhas com `lineItem/isCorrection=true`
  apontam para `lineItem/backReference`; a linha referenciada deve ser
  **dropada** do DataFrame antes de processar, e a correção mantida. O parser
  faz isso em `_apply_corrections`.
- **Tags dinâmicas:** o número e os nomes das colunas `tags/*` variam entre
  tenancies. Achatar tudo em `tags: dict[str, str]`.
- **Múltiplos arquivos por dia:** deduplicar por `lineItem/referenceNo` antes
  de persistir (ou pelo `line_hash` do parser).
- `.csv.gz` precisa descompactar antes — o `loaders.read_tabular` resolve.

---

## Como adicionar um parser novo

Cenário: nova cloud (digamos, **DigitalOcean**) ou novo formato (digamos,
**FOCUS** cross-cloud).

### Checklist

1. **Criar** `app/infrastructure/file_ingestion/parsers/<provider>_parser.py`
   herdando de `BaseParser`.
2. **Implementar** `can_parse(file_path, content_sample) -> bool`:
   - Decodificar `content_sample` (`utf-8` ou `utf-8-sig`).
   - Procurar **tokens únicos** do formato (idealmente 2+ tokens).
   - **Nunca levantar exceção** — só retornar `False`.
3. **Implementar** `parse(file_path) -> pd.DataFrame`:
   - Chamar `self._set_version("<TAG_VERSION>")`.
   - Ler o arquivo com `loaders.read_tabular` (já trata `.csv` / `.csv.gz` /
     `.csv.zip` / `.xlsx` / `.tsv`).
   - **Normalizar** para o schema canônico — ver
     [`data-model.md`](data-model.md#schema-canônico-do-parser).
   - **Toda cifra monetária via `_cost_to_decimal`** — nunca `float`.
   - **Preservar `raw_payload`** (linha original sem vazios).
   - Calcular `line_hash = self._hash_row(raw)`.
4. **Exportar** em [`parsers/__init__.py`](../app/infrastructure/file_ingestion/parsers/__init__.py).
5. **Registrar** em
   [`detector.PARSERS`](../app/infrastructure/file_ingestion/detector.py) na
   posição correta. **Mais específico vem antes** — se a sua assinatura é
   ampla, vá depois dos parsers cujas assinaturas podem se sobrepor.
6. **Criar fixture** em `app/tests/fixtures/billing/<provider>_sample.<ext>`
   (pequena, anonimizada, com pelo menos uma row de cada cenário relevante).
7. **Adicionar fixture pytest** em
   [`app/tests/conftest.py`](../app/tests/conftest.py).
8. **Escrever testes** em
   `app/tests/unit/file_ingestion/test_<provider>_parser.py`:
   - Detector pega o parser certo.
   - Provider e schema_version corretos.
   - `cost` é `Decimal` (nunca `float`).
   - Armadilhas específicas do formato cobertas.
9. **Documentar** este arquivo (adicionar seção como as acima) com:
   - Formato + extensões.
   - Assinatura para detecção.
   - Tabela de colunas críticas.
   - Armadilhas.
10. **Atualizar** [`CLAUDE.md`](../CLAUDE.md) na linha que enumera os 5 parsers.

### Anti-padrões

- ❌ Detectar provider pela **extensão** (`.csv` pode ser AWS, Azure, OCI…).
- ❌ Levantar exceção em `can_parse` — pula o parser e segue.
- ❌ Usar `dtype` numérico no `read_csv` — sempre `dtype=str`.
- ❌ Persistir `Decimal(float(value))` — perde precisão.
- ❌ Esquecer `_hash_row` — quebra o `deduplicate`.
- ❌ Registrar antes do parser cuja assinatura é menos específica.
- ❌ **Deixar linhas de resumo contábil passarem** — linhas como
  `RecordType = StatementTotal/InvoiceTotal` (AWS Invoice) e
  `Cost type = "tax"` (GCP Cost Table) devem ser filtradas no parser e
  no enriquecimento, sob pena de **duplicar custos na agregação**.
  Ex: AWS InvoiceTotal duplica custo real, Tax GCP não é custo de infra.

## Como debugar uma detecção que falha

```bash
# 1. Tentar parsing manual no REPL
.venv/bin/python -c "
from pathlib import Path
from uuid import UUID
from app.infrastructure.file_ingestion.detector import detect_parser
p = detect_parser(Path('seu_arquivo.csv'),
                  UUID('00000000-0000-0000-0000-000000000001'),
                  UUID('00000000-0000-0000-0000-000000000002'))
print(type(p).__name__, p.get_schema_version())
"

# 2. Ver os primeiros bytes (BOM? compressed? UTF-16?)
xxd seu_arquivo.csv | head -5

# 3. Ver as colunas detectadas
.venv/bin/python -c "
import pandas as pd
print(pd.read_csv('seu_arquivo.csv', nrows=0).columns.tolist())
"
```

## Normalização de `usage_type` → `match_key` (downstream)

O parser emite `usage_type` **cru** (ex.: GCP `"E2 Instance Core running in
Americas"`, AWS `"BoxUsage:m7g.medium"`). A classificação acontece depois, no
enriquecimento, via **Matching Ladder** — o parser **não** classifica nem
normaliza role. Para que as camadas L1+ casem apesar de variação textual trivial,
deriva-se um `match_key` determinístico:

```
match_key = collapse_whitespace(trim(lowercase(casefold(usage_type))))
```

O `provider` é normalizado por `normalize_provider()` (caso-insensível: `'GCP'` ↔
`'gcp'`) nos dois lados da comparação. Importante: o `sku_id` armazenado em
`billing_enriched` é a **chave estruturada do SKU casado** (ex.: `e2-standard-2`),
**não** a descrição crua — separar os dois é o fix do root-cause #3. Spec completa
em [`sku-matching.md`](sku-matching.md#3-data-contracts).

## Documentação relacionada

- [`data-model.md`](data-model.md) — schema canônico e DDL.
- [`sku-matching.md`](sku-matching.md) — Matching Ladder e normalização de matching.
- [`endpoints/billing.md`](endpoints/billing.md) — como o frontend consome o
  resultado.
- [`architecture.md`](architecture.md) — regras de Clean Architecture (parser
  vive em `infrastructure/`; sem imports de framework no `domain/`).
- [`product.md`](product.md#módulo-1--extração-de-billing-fase-1-mvp) — papel
  do Módulo 1 no produto.
