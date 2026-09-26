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
| `AWSParser` | AWS CUR 2.0 + Legacy CUR (.csv/.csv.gz/.csv.zip/.parquet/.xlsx) | `AWS_CUR2.0` ou `AWS_CUR_LEGACY` | ✅ |
| `AWSCostExplorerParser` | AWS Cost Explorer "Daily by service" (TSV/CSV pivotado, BOM, locale) | `AWS_CE_DAILY_BY_SERVICE` | ✅ |
| `AwsDetailedBillingParser` | AWS Invoice / Detailed Billing Report | `AWS_DETAILED_BILLING` | ✅ |
| `AzureParser` | Azure Cost Management cost details (PAYG / EA / MCA) | `AZURE_PAYG`, `AZURE_EA` ou `AZURE_MCA` | ✅ |
| `GCPParser` | GCP Cloud Billing Export (NDJSON / JSON / CSV / XLSX / Parquet) | `GCP_BILLING_STD` ou `GCP_BILLING_DETAILED` (com recurso) | ✅ |
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

## Exports detalhados: o que é comum (CUR, BigQuery, Azure, OCI)

Os quatro parsers de export detalhado seguem o mesmo desenho, e as regras de
negócio estão em [`PLANO_CUR_BIGQUERY.md`](../PLANO_CUR_BIGQUERY.md):

- **Natureza da linha** (`charge_kind`): `usage | commitment | tax | credit |
  refund | adjustment | other`
  ([`charge_kind.py`](../app/domain/value_objects/charge_kind.py)). O
  enriquecimento decide por ela: imposto, crédito, reembolso e ajuste ficam
  fora da base; compromisso nunca vira capacidade.
- **Custo amortizado** (`cost`): compromisso (Savings Plan, RI, CUD, reserva)
  distribuído sobre o uso que cobriu, desconto negociado aplicado. O valor da
  fatura fica em `cost_billed`. Crédito promocional e free tier não reduzem a
  base: acabam ou não migram.
- **Consolidação** ([`consolidacao.py`](../app/infrastructure/file_ingestion/consolidacao.py)):
  uma linha por (período, conta, região, recurso, tipo de uso, operação, tipo
  de recurso, natureza, unidade, base de custo), com deduplicação pela
  identidade da linha. O `raw_payload` guarda a chave e a contagem, não a linha
  original.
- **Unidade canônica** ([`unidades.py`](../app/infrastructure/file_ingestion/unidades.py)):
  `hours`, `gibibyte hour`, `gibibyte month`. O multiplicador embutido
  (`10 Hours`) vai para a quantidade.
- **Fatos medidos** (`attributes`): `instance_type`, `vcpus`, `memory_gib`,
  `project_id`, `capacity_unit` (`instance` | `vcpu` | `gib`),
  `instance_family`, `storage_kind`. Só entra fato informado pelo provedor,
  nunca estimativa.
- **Leitura em blocos** e só das colunas usadas (`loaders.iter_tabular`);
  Parquet aceito.
- **Mocks** derivados dos bills reais (CUR, BigQuery) e sintéticos (Azure,
  OCI): `scripts/gerar_mocks_billing.py`, determinístico, com invariantes em
  `test_mocks_invariantes.py`. E2E pela API: `scripts/e2e_exports_detalhados.py`.

---

## AWS CUR (Cost and Usage Report)

Arquivo: [`aws_parser.py`](../app/infrastructure/file_ingestion/parsers/aws_parser.py)

### Formato

- **CUR 2.0**: colunas `snake_case`; mapas (`product`, `resource_tags`) como
  JSON no CSV ou `map` no Parquet.
- **CUR legado**: colunas com `/` (`lineItem/UnblendedCost`), tags em colunas
  `resourceTags/user:X`.
- Extensões: `.csv`, `.csv.gz`, `.csv.zip`, `.parquet`, `.xlsx`.
- Recomendação ao cliente: granularidade **diária** com `INCLUDE_RESOURCES`.

### Assinatura para detecção

`line_item_unblended_cost` **ou** `lineItem/UnblendedCost`.

### Colunas críticas (CUR 2.0)

| Canônico | Coluna |
|---|---|
| natureza | `line_item_line_item_type` (sem a coluna: `usage` na base faturada) |
| `cost` | amortizado: `savings_plan_savings_plan_effective_cost` (coberto por SP), `reservation_effective_cost` (RI), parte ociosa do plano/reserva nas taxas; o resto `line_item_net_unblended_cost` ou `line_item_unblended_cost` |
| `cost_billed` | `line_item_unblended_cost` |
| `usage_type` | `line_item_usage_type` |
| `account_id` | `line_item_usage_account_id` (quem consumiu); fallback pagador |
| `region` | `product_region_code`; fallbacks `product_region` e mapa `product` |
| `resource_id` | `line_item_resource_id` |
| `resource_type` | `product_instance_type` (ou o sufixo do tipo de uso) |
| `attributes.vcpus/memory_gib` | mapa `product` (`vcpu`, `memory`) |
| `tags` | `resource_tags` (`user_env` vira `env`; `aws_x` vira `aws:x`) |

### Armadilhas

- **Savings Plan no faturado soma zero por instância**: `SavingsPlanCoveredUsage`
  traz o sob demanda, e a `SavingsPlanNegation` anula. Por isso a base é a
  amortizada.
- **Sem as colunas de SP/RI** (o CUR 2.0 deixa escolher colunas), a linha fica
  na base faturada: `attributes.cost_basis = "billed"` e premissa `misto`.
- `SavingsPlanRecurringFee` é compromisso, não `core`: pela escada, as horas do
  plano viravam vCPU.

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

Cost details (EA, MCA, PAYG). Prefira o dataset **amortized**: nele a reserva já
vem distribuída sobre o uso. No *actual*, o uso coberto sai zero e a compra
aparece como compromisso (é o que `cobertura_por_compromisso` declara).

### Assinatura

`CostInBillingCurrency` **e** `MeterCategory`.

### Colunas críticas

| Canônico | Coluna |
|---|---|
| natureza | `ChargeType` + `PricingModel` (compra de reserva/SP e reserva ociosa = compromisso) |
| `usage_type` | `MeterCategory | MeterSubCategory | MeterName` |
| `service` | `ServiceName` (só PAYG) ou `MeterCategory` |
| `region` | `ResourceLocation` normalizada (`East US` vira `eastus`) |
| `resource_id` | `ResourceId`, em minúsculas (o ARM não diferencia) |
| `attributes` | `AdditionalInfo`: `ServiceType` (tamanho, família), `VCPUs` |
| `usage_amount` | `Quantity` x multiplicador de `UnitOfMeasure`; disco por tier (P30) vira GB-mês |

### Armadilhas

- `Tags` vem **sem as chaves externas** (`"env": "prod","app": "x"`).
- Sem `ServiceName` (EA/MCA), a versão antiga gravava o serviço literal "None".
- Disco é cobrado por disco-mês do tier, não por GB: o parser converte pelo
  tamanho publicado do tier. O preço de lista é por disco-mês, então o sob
  demanda é calculado antes da conversão.
- A Azure informa vCPU e não RAM: a RAM sai da estimativa pelo nome e a
  capacidade conta como estimada.

---
## GCP Cloud Billing Export

Arquivo: [`gcp_parser.py`](../app/infrastructure/file_ingestion/parsers/gcp_parser.py)

### Formato

Export do BigQuery (padrão e detalhado): NDJSON (inclusive `.json.gz` do
`bq extract`), array JSON, CSV achatado (`service.description` ou
`service_description`, repetidos como JSON), XLSX, Parquet. NDJSON lido em
fluxo, com `parse_float=Decimal`.

### Assinatura

`billing_account_id` **e** `usage_start_time`.

### Colunas críticas

| Canônico | Coluna |
|---|---|
| `usage_amount` / `usage_unit` | `usage.amount_in_pricing_units` / `usage.pricing_unit` (fallback converte segundos e byte-segundos) |
| `cost` | `cost` + créditos estruturais (CUD, SUD, `DISCOUNT`…) + parcela rateada da taxa de CUD |
| `cost_billed` | `cost` + todos os créditos |
| natureza | `cost_type` (`tax`, `rounding error`, `adjustment` por `adjustment_info.type`); SKU `Commitment…` = compromisso; SKU de free tier = crédito |
| `usage_type` | `sku.description` |
| `resource_id` | `resource.global_name` ou `resource.name` |
| `attributes` | `system_labels` (`machine_spec`, `cores`, `memory`); `capacity_unit` vcpu/gib; `project_id` |

### Armadilhas críticas

- **`usage.amount` está na unidade BASE** (segundos). Sem a unidade de preço, a
  capacidade saía zero e o custo ia para compromisso.
- **Taxa de CUD** rateada pelo crédito de CUD de cada linha. A ociosidade não
  aparece no export, então o rateio é integral e declarado como estimativa.
- **SKU "…free tier discount"**: horas positivas e custo negativo. Pela escada
  virava `core` e um pool fantasma de custo negativo; é crédito.

---
## OCI Cost Report

Arquivo: [`oci_parser.py`](../app/infrastructure/file_ingestion/parsers/oci_parser.py)

### Formato

Cost Report proprietário (`.csv.gz` a cada 6h). Nomes lidos sem diferenciar
maiúsculas.

### Assinatura

`cost/myCost` **e** `lineItem/intervalUsageStart`.

### Colunas críticas

| Canônico | Coluna |
|---|---|
| `account_id` | `lineItem/tenantId` |
| `account_name` | `product/compartmentName` |
| `attributes.project_id` | `product/compartmentId` |
| `usage_type` | `product/description` |
| `usage_unit` | `cost/billingUnitReadable` (`OCPU Hours`, `GB Hours`, `GB Month`) |
| `attributes.vcpus_per_unit` | 2 por OCPU em x86; 1 no Ampere A1 |
| `is_correction` | `lineItem/isCorrection`; substitui `lineItem/backReferenceNo` |

### Armadilhas

- **Correção substitui, não soma**, e pode vir em bloco posterior ao da linha
  corrigida: leitura em duas passadas.
- **OCPU ≠ vCPU**.
- A versão antiga lia `tenantId`, `compartmentName`, `usage/billedUnit` e
  `lineItem/backReference`, colunas que o relatório real não tem.

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
