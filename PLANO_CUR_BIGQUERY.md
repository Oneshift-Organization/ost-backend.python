# Plano: exports detalhados de billing (CUR 2.0, BigQuery, Azure, OCI)

**Base:** decisão de 26/09/2026 — CUR/BigQuery para o DE-PARA, API do provedor
para Terraform/migração (esta segunda parte fica no `PLANO_INVENTARIO_API.md`).
**Dado real:** ainda não temos. Trabalhamos com **mock derivado dos dois bills
reais versionados**, e o mock é tratado como código: gerado por script
determinístico, com invariantes testados.

**Regra que continua valendo:** o caminho legado não muda de comportamento. Os
dois CSVs reais (`aws_cost_explorer_full.csv`, `gcp_cost_table_full.csv`)
continuam produzindo exatamente o mesmo payload; o teste de caracterização
(`test_legado_caracterizacao.py`) é o juiz.

> ## Status
>
> | etapa | estado |
> |---|---|
> | E0 — versão preservada (`baseline/pre-cur-inventario`) | ✅ |
> | E1 — contrato canônico ampliado | ✅ |
> | E2 — mocks + invariantes | ✅ |
> | E3 — parser CUR 2.0 | ✅ |
> | E4 — parser BigQuery | ✅ |
> | E5 — enriquecimento | ✅ |
> | E6 — agregador + fatos medidos | ✅ |
> | E7 — payload e premissas | ⬜ |
> | E8 — Terraform usando fatos medidos | ⬜ |
> | E9 — Azure e OCI | ⬜ |
> | E10 — E2E + auditoria de coerência | ⬜ |

> **Antes de ligar em dev/produção:** `alembic upgrade head` (colunas novas),
> re-seed de `sku_classification_rules` (regras novas e `*rds:*` com prefixo de
> região) e `scripts/re_enrich_report.py` nos relatórios existentes.

---

## 1. Fontes verificadas

Nomes de coluna foram conferidos na documentação oficial, não deduzidos do
código. Motivo: as fixtures atuais repetem os nomes que o parser espera. Se o
parser erra a coluna, a fixture erra junto e o teste passa. Mais abaixo mostro
que isso já aconteceu em três parsers.

| provedor | fonte |
|---|---|
| AWS CUR 2.0 | [dicionário](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2.html): [line item](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2-line-item.html), [product](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2-product.html), [savings plan](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2-savings-plan.html), [reservation](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2-reservation.html), [Savings Plans no CUR](https://docs.aws.amazon.com/cur/latest/userguide/cur-sp.html), [migração legado→2.0](https://docs.aws.amazon.com/cur/latest/userguide/dataexports-migrate.html) |
| GCP | [detailed usage export](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/detailed-usage) |
| Azure | [cost details fields](https://learn.microsoft.com/en-us/azure/cost-management-billing/automate/understand-usage-details-fields) |
| OCI | [cost reports](https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/costusagereportsoverview.htm) |

Fatos que sustentam as regras abaixo:

- **CUR 2.0**: região é `product_region_code`, e não existe `product_region`
  como coluna estática (vira chave do mapa `product`). O tipo da linha fica em
  `line_item_line_item_type`: `Usage`, `DiscountedUsage`, `SavingsPlanCoveredUsage`,
  `SavingsPlanNegation`, `SavingsPlanRecurringFee`, `SavingsPlanUpfrontFee`,
  `RIFee`, `Fee`, `Tax`, `Credit`, `Refund`, `Discount`, `BundledDiscount`,
  `FlateRateSubscription`. Não existe `RecordType`. `SavingsPlanCoveredUsage`
  traz em `unblended` o preço **sob demanda**, e a `SavingsPlanNegation`
  correspondente anula esse valor. Por isso o custo de cada instância soma zero.
  O custo real dela está em `savings_plan_savings_plan_effective_cost`.
  `DiscountedUsage` (RI) tem `unblended_rate = 0`, e o custo real está em
  `reservation_effective_cost`. `resource_tags`, `product`, `cost_category` e
  `discount` são colunas-mapa (JSON no CSV). `line_item_resource_id` só existe
  com `INCLUDE_RESOURCES`. A conta que consumiu é `line_item_usage_account_id`;
  `bill_payer_account_id` é quem paga. Formatos entregues: GZIP (CSV), ZIP ou
  Parquet.
- **BigQuery**: `usage.amount`/`usage.unit` vêm na unidade base (segundos,
  byte-segundos). A unidade de preço está em `usage.amount_in_pricing_units` e
  `usage.pricing_unit` (`hour`, `gibibyte hour`). `cost_type` ∈ {`regular`,
  `tax`, `adjustment`, `rounding error`}. `credits[].type` ∈ {`COMMITTED_USAGE_DISCOUNT`,
  `COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE`, `FEE_UTILIZATION_OFFSET`,
  `SUSTAINED_USAGE_DISCOUNT`, `DISCOUNT`, `FREE_TIER`, `PROMOTION`,
  `RESELLER_MARGIN`, `SUBSCRIPTION_BENEFIT`}. `system_labels` traz
  `compute.googleapis.com/machine_spec`, `/cores` e `/memory` (MB). O shape da
  VM é **medido**, não deduzido do nome. O export detalhado traz `resource.name`
  e `resource.global_name`.
- **Azure**: `ChargeType` ∈ {`Usage`, `Purchase`, `Refund`, `UnusedReservation`,
  `UnusedSavingsPlan`, `RoundingAdjustment`}; `PricingModel` ∈ {`OnDemand`,
  `Reservation`, `SavingsPlan`, `Spot`}. `ServiceName` só existe em PAYG.
  `AdditionalInfo` (JSON) traz `ServiceType` (tamanho da VM) e `VCPUs`. O
  dataset *amortized* já distribui a reserva nas linhas de uso.
- **OCI**: `lineItem/tenantId`, `product/compartmentName`, `product/description`,
  `cost/billingUnitReadable`, `lineItem/backReferenceNo`. As quatro nuvens
  oferecem export FOCUS (ver §10).

---

## 2. Diagnóstico do código atual

Verificado lendo o código, com o efeito concreto de cada defeito.

### Parser CUR (`aws_parser.py`)

| # | defeito | efeito |
|---|---|---|
| D1 | região lida de `product_region` | no CUR 2.0 real a região sai `None` → todos os pools viram `global` |
| D2 | filtro por `RecordType` (coluna que o CUR não tem) | linhas `Tax` entram como custo de serviço |
| D3 | só `unblended` | instância coberta por SP soma 0 → **pool com capacidade e custo zero**, o defeito que hoje é só *declarado* por `cobertura_por_compromisso`. `DiscountedUsage` idem |
| D4 | `SavingsPlanRecurringFee` passa pela classificação como `core` (regra `computesp:*`) com unidade `Hrs` | o agregador soma as horas do compromisso como capacidade → **pool fantasma** de 1 vCPU com o custo inteiro do plano |
| D5 | conta = `bill_payer_account_id` | a conta que consumiu se perde |
| D6 | `resource_type` sempre `None`; mapa `product` ignorado | `product_instance_type`, vCPU e memória **medidos** são jogados fora; o shape volta a ser estimado pelo nome |
| D7 | tags do legado lidas de `resourceTags` (no legado são colunas `resourceTags/user:X`) | tags vazias no CUR legado |
| D8 | `iterrows()` + `raw_payload` com ~125 colunas por linha | CUR horário real (10⁵–10⁷ linhas) fica inviável em tempo, memória e JSONB |
| D9 | sem Parquet | um dos três formatos oficiais é recusado |

### Parser BigQuery (`gcp_parser.py`)

| # | defeito | efeito |
|---|---|---|
| D10 | quantidade de `usage.amount`/`usage.unit` (segundos) | o agregador não reconhece a unidade → **capacidade zero, custo inteiro de compute vai para `compromisso_reserva`** |
| D11 | `cost_type = tax` não é excluído | imposto entra como serviço |
| D12 | CSV achatado (`service.description`, `service_description`) não é lido: `_nested` espera dict | serviço `unknown`, SKU vazio em qualquer CSV baixado do console |
| D13 | `cost_discounted = cost + todos os créditos` | promoção (temporária) reduz a base como se fosse preço |
| D14 | `resource_type = sku.description` | campo com semântica trocada (SKU ≠ tipo de recurso) |
| D15 | `system_labels` ignorado | machine type e shape medidos se perdem |
| D16 | SKU `Commitment v1: …` cai como `core` (L3: família + "cpu") | mesmo pool fantasma do D4 |
| D16b | `.json.gz` (saída do `bq extract` comprimida): o sufixo é `.gz`, o parser tenta ler o binário como texto e cai no leitor de CSV | NDJSON comprimido, o formato mais comum de export, não é lido |

### Parser Azure (`azure_parser.py`)

| # | defeito | efeito |
|---|---|---|
| D17 | `str(row.get("ServiceName"))` sem a coluna (EA/MCA) | serviço literal **`"None"`** em todo export EA/MCA |
| D18 | `Tags` sem chaves externas (`"env": "prod","app": "x"`) não é JSON | tags vazias |
| D19 | `ChargeType`/`PricingModel` ignorados | compra de reserva somada como uso |
| D20 | `UnitOfMeasure` com multiplicador (`"1 Hour"`, `"10 Hours"`) | unidade não reconhecida → capacidade zero |
| D21 | `AdditionalInfo` ignorado | tamanho da VM e vCPU medidos se perdem |

### Parser OCI (`oci_parser.py`)

| # | defeito | efeito |
|---|---|---|
| D22 | `tenantId`, `compartmentName`, `usage/billedUnit`, `lineItem/backReference` | conta vazia, **unidade vazia (capacidade zero)**, **correção somada em dobro** (a regra de substituição nunca dispara) |
| D23 | OCPU tratado como vCPU | em x86, 1 OCPU = 2 vCPU → capacidade pela metade |

### Enriquecimento e análise

| # | defeito | efeito |
|---|---|---|
| D24 | descritor buscado em chaves com o nome das colunas dos 2 CSVs (`UsageType`, `SKU description`) | CUR, BigQuery, Azure e OCI caem no nome do serviço: **o EC2 inteiro vira o descritor `AmazonEC2`** |
| D25 | filtro financeiro também preso a essas chaves (`RecordType`, `ProductCode=Tax`, `Cost type`) | imposto de CUR/BigQuery passa |
| D26 | `resource_id` para em `billing_items`; `billing_enriched` não tem a coluna | nada a jusante sabe quantas instâncias existem |
| D27 | `list_by_report(limit=100000)` no enriquecimento e no contexto | acima de 100 mil linhas **trunca em silêncio** → custo total errado sem aviso |
| D28 | seed de regras só tem AWS e GCP | Azure e OCI como origem caem quase inteiros em L4/L5 |

### Fixtures que confirmam o risco

`app/tests/fixtures/billing/oci_cost_sample.csv` usa `tenantId`,
`compartmentName`, `usage/billedUnit` e `lineItem/backReference`, nomes que não
existem no relatório real. `aws_cur2_sample.csv` usa `product_region`. As
fixtures foram escritas a partir do parser, e não do provedor.

---

## 3. O que já existe e será reaproveitado

| peça | reaproveita? | impacto de mexer |
|---|---|---|
| `BaseParser` + `detector.PARSERS` | sim, sem mudar o contrato | nenhum: detecção por conteúdo continua |
| `loaders.read_tabular` | sim; ganha Parquet | aditivo. CSV/XLSX intocados |
| Os 4 parsers detalhados | **reescritos por dentro**, mesma classe e mesma assinatura | os testes atuais de cada um têm de continuar passando, com fixtures corrigidas para nomes reais onde o nome era inventado |
| `AWSCostExplorerParser`, `AwsDetailedBillingParser`, `GcpCostTableParser` | **não tocados** | zero. São o caminho dos 2 CSVs reais |
| `SkuMatcher` + regras AWS/GCP | sim. `line_item_usage_type` do CUR tem o mesmo formato do `UsageType` do bill real, e `sku.description` do BigQuery é igual ao `SKU description` do Cost Table | as regras já validadas no bill real valem para o CUR/BigQuery **sem alteração** |
| `BillingAggregator` | sim; ganha shape medido, componente `commitment` e contagem de recursos | aditivo, com os caminhos atuais como fallback |
| `instance_shape` / `azure_shape` | sim, como **fallback** do shape medido | nenhum |
| `cobertura_por_compromisso` | mantido. Com custo amortizado ele deixa de disparar para CUR, o que é o comportamento correto | nenhum no legado |
| `terraform_plan.derivar_plano` | sim; passa a preferir fato medido quando existir | aditivo: sem `inventario_medido` no payload o código atual roda igual |
| `test_legado_caracterizacao.py` | **é o gate** | qualquer diferença no payload dos 2 CSVs reais é regressão |

---

## 4. Regras de negócio

Cada regra diz **onde** vive. Regra de provedor fica no parser (infraestrutura).
O que fazer com cada natureza de linha fica no enriquecimento e no agregador,
sem `if` por nuvem.

**RN1: base de custo amortizada.** Para exports detalhados, `cost` é o custo
**efetivo amortizado**: o que a linha custa de fato, com o compromisso
distribuído sobre o uso que ele cobriu. É a base padrão de FinOps e a única em
que "custo por vCPU" faz sentido. Com a base faturada, uma instância coberta
por Savings Plan custa zero e o plano custa tudo.
- *AWS*: fórmula da documentação (CUDOS). `SavingsPlanCoveredUsage` →
  `savings_plan_savings_plan_effective_cost`; `SavingsPlanNegation` e
  `SavingsPlanUpfrontFee` → 0; `SavingsPlanRecurringFee` →
  `total_commitment_to_date − used_commitment` (parte **não usada**);
  `DiscountedUsage` → `reservation_effective_cost`; `RIFee` →
  `unused_amortized_upfront_fee_for_billing_period + unused_recurring_fee`;
  `Fee` com `reservation_reservation_a_r_n` → 0; o resto → `unblended`. Quando o
  export traz as colunas `net_*` (há desconto negociado), elas prevalecem.
  **Degradação:** o CUR 2.0 permite escolher colunas. Sem as colunas de SP/RI,
  a amortização é impossível. A linha fica na base faturada, marcada com
  `attributes.cost_basis = "billed"`, e a premissa `base_de_custo` sai
  `"misto"`. O diagnóstico `cobertura_por_compromisso` volta a disparar, que é
  o correto: o problema reaparece e é declarado. Sem nem a coluna
  `line_item_line_item_type` (export mínimo ou antigo), toda linha é `usage` na
  base faturada. É o comportamento de hoje, só que declarado.
- *GCP*: `cost + créditos estruturais + parcela do compromisso`. Créditos
  estruturais: CUD, SUD, `DISCOUNT`, `SUBSCRIPTION_BENEFIT`, `RESELLER_MARGIN`.
  `PROMOTION` e `FREE_TIER` ficam fora (RN3). *Ajuste de 26/09:* o free tier
  estava na lista de estruturais; saiu porque não migra, a cotação do destino
  não o tem, e o SKU "…free tier discount" (horas positivas, custo negativo)
  criava um pool fantasma. A taxa de compromisso (SKU
  `Commitment…`) é rateada entre as linhas com crédito de CUD, na proporção do
  crédito. A ociosidade de CUD não aparece no export, então o rateio é integral
  e sai como **ESTIMATIVA** declarada.
- *Azure*: o valor exportado. No dataset *amortized* ele já é amortizado. No
  *actual*, a linha `Purchase` de reserva vira compromisso (RN2) e o uso coberto
  fica em zero, que é o caso que `cobertura_por_compromisso` já declara.
- *OCI*: `cost/myCost` (já é custo de consumo).
- O valor **faturado** (unblended) fica em `cost_billed`, para reconciliar com a
  fatura.

**RN2: natureza da linha (`charge_kind`).** Cada parser detalhado classifica a
linha em `usage | commitment | tax | credit | refund | adjustment | other`. O
enriquecimento e o agregador decidem pela natureza, nunca pelo nome da nuvem:
- `usage` → segue a escada de SKU normalmente;
- `commitment` → componente `commitment`, direto para o balde de compromisso,
  **nunca** para pool de capacidade (fecha D4/D16);
- `tax`, `credit`, `refund`, `adjustment` → fora da base de análise (RN3);
- `other` → escada normal (se não casar, vira `nao_classificado`, como hoje).

Mapeamento por provedor (fica no parser):

| natureza | AWS `line_item_line_item_type` | GCP | Azure `ChargeType` | OCI |
|---|---|---|---|---|
| usage | `Usage`, `DiscountedUsage`, `SavingsPlanCoveredUsage` | `cost_type=regular`; `adjustment` de `USAGE_CORRECTION`/`PRICE_CORRECTION` | `Usage` | linha comum |
| commitment | `SavingsPlanRecurringFee`, `SavingsPlanUpfrontFee`, `SavingsPlanNegation`, `RIFee`, `Fee` com ARN de reserva | SKU `Commitment…` | `Purchase` de `Reservation`/`SavingsPlan`, `UnusedReservation`, `UnusedSavingsPlan` | — |
| tax | `Tax` | `cost_type=tax` | — | — |
| credit | `Credit`, `Discount`, `BundledDiscount` | `adjustment` de goodwill/SLA/transferência | — | — |
| refund | `Refund` | — | `Refund` | — |
| adjustment | — | `cost_type=rounding error` | `RoundingAdjustment` | — |
| other | `Fee` sem reserva, `FlateRateSubscription` | — | `Purchase` sob demanda (marketplace) | — |

`Discount`/`BundledDiscount` saem porque o desconto que elas representam já
está nas colunas `net_*`. A AWS só inclui essas colunas quando há desconto no
período, e é justamente quando elas existem que as linhas de desconto
aparecem. Somar os dois contaria o desconto duas vezes.

**RN3: fora da base.** Imposto, crédito promocional, reembolso e ajuste não
entram no custo analisado. Imposto não muda entre nuvens. Crédito promocional
acaba, e se entrasse na base um cliente com crédito pareceria gastar zero, e
toda migração pareceria mais cara. Ajuste (arredondamento, goodwill) não é
custo recorrente. É a mesma política que hoje exclui `Tax` do bill AWS real e
`Tax`/`Rounding error` do Cost Table GCP; aqui ela vira explícita e vale para
as quatro nuvens. Correção de uso ou preço **não** é ajuste nesse sentido: ela
corrige custo recorrente e fica na base.

**RN4: capacidade medida prevalece.** Quando a linha traz vCPU/RAM do próprio
provedor (mapa `product` da AWS, `system_labels` do GCP, `AdditionalInfo.VCPUs`
da Azure), o shape é **fato** e prevalece sobre o shape estimado pelo nome.
`premissas.dimensionamento_estimado` passa a contar só o que ainda é estimado.
Ordem: medido > catálogo > nome. `attributes` carrega **só fato medido**:
quando o provedor informa vCPU mas não RAM (Azure), a RAM sai da estimativa
pelo nome no agregador, e o pool fica marcado como parcialmente estimado.
Estimativa gravada em `attributes` apareceria a jusante como fato.

**RN5: unidade canônica.** O parser entrega a quantidade na unidade de preço
normalizada: horas (`hours`), GiB-hora (`gibibyte hour`), GB-mês
(`gibibyte month`). O multiplicador vai para a quantidade (`"10 Hours"`,
quantidade 3 → 30 horas). OCPU x86 carrega `vcpus_per_unit = 2`; Ampere (A1),
`1`. O agregador continua sem saber de nuvem.

**RN6: consolidação na ingestão.** Primeiro a deduplicação: pela identidade da
linha quando o export a tem (`identity_line_item_id` + `identity_time_interval`
no CUR), e pelo hash da linha inteira quando não tem. Depois, os exports
detalhados são consolidados em **uma linha por (período, conta, região,
recurso, tipo de uso, operação, tipo de recurso, natureza, unidade, base de
custo)**, somando custo, custo faturado, custo sob demanda e quantidade. Primeiro/último uso, dias ativos e número de linhas de origem vão
para `attributes`. O efeito é tirar o volume do problema (D8, D27): o que o
pipeline usa é mensal, e a granularidade horária só serve para contar horas
ativas, que a soma já dá. O `raw_payload` da linha consolidada guarda a
**chave** e a contagem, e não uma linha original. É uma exceção declarada à
regra "preservar a linha original", justificada pelo volume.

**RN7: sem truncamento silencioso.** Leitura de itens em páginas até o fim. O
limite fixo de 100 mil sai.

**RN8: região e conta vêm do export.** `global` só quando o export não tem
região. Conta = quem consumiu. O `project_id` do pool vem de
`attributes.project_id` (conta AWS, projeto GCP, assinatura Azure,
compartimento OCI) quando o parser detalhado o fornece. O caminho legado
continua lendo do `raw_payload` como hoje.

**RN9: fatos medidos no payload, aditivos.** Quando existem, o payload ganha
`inventario_medido`: instâncias distintas por pool, distribuição por tipo de
origem, fração de tempo ativo, GB medido e contagem de volumes/buckets, e
bancos com classe, motor e multi-AZ. Sem dado detalhado, a seção não aparece,
e o payload do legado fica byte a byte igual.
- *Fração de tempo ativo* = `Σ instância-hora ÷ (instâncias × horas do
  período)`. Só é calculada onde a instância-hora é derivável: linha por
  instância (AWS, Azure) ou core-hora ÷ cores medidos (GCP). É fato de billing
  (quanto tempo ficou ligada), **não** utilização de CPU.
- *GB medido* = `Σ GB-mês` das linhas de armazenamento. Num período de um mês
  inteiro, isso é o tamanho médio provisionado. GB e GiB são tratados como
  equivalentes, com a aproximação declarada (~7%).

**RN10: o Terraform prefere fato medido.** Disco com GB **medido** vira
`derivacao` (antes era `estimativa` por custo ÷ taxa). Banco com classe e motor
medidos: vCPU pela classe, motor informado na variável. A contagem de
instâncias da origem entra na premissa do compute. **O shape de destino
continua o do empacotamento da análise**, porque é o que foi cotado. Trocar
por 1:1 faria o `.tf` divergir do número que o cliente aprovou. Quando o
empacotamento consolida N instâncias em menos máquinas, sai um aviso explícito
de consolidação.

**RN11: desconto de compromisso visível.** Com custo sob demanda disponível
(`pricing_public_on_demand_cost` na AWS, `cost_at_list` no GCP), a premissa
`desconto_origem_sobre_lista` mostra quanto a origem economiza hoje:
`(Σ sob demanda − Σ amortizado) ÷ Σ sob demanda`, somado só sobre as linhas
`usage` que têm valor sob demanda. O número inclui compromisso, desconto
negociado e SUD, porque todos somem ao migrar sem recontratar.
O destino é cotado sem compromisso. Sem essa premissa, o comparativo parece
dizer que a nuvem de destino é mais cara, quando o que acontece é que o cliente
perde o desconto se não recontratar.

---

## 5. Arquitetura da mudança

```
arquivo ─▶ parser detalhado ─▶ DataFrame canônico (+ usage_type, resource_type,
            (provedor)          charge_kind, cost_billed, attributes)
                    │
                    ▼
            consolidar()  ── puro, genérico, testável ──▶ 1 linha/recurso/uso/mês
                    │
                    ▼
            billing_items (+5 colunas nulas)
                    │
                    ▼
            enriquecimento: descritor = usage_type ▸ legado;
                            natureza decide filtro/rota;
                            resource_id + attributes seguem
                    │
                    ▼
            billing_enriched (+ resource_id, attributes)
                    │
                    ▼
            agregador: shape medido ▸ catálogo ▸ nome; commitment; recursos
                    │
                    ▼
            payload (+ inventario_medido, premissas novas) ─▶ Terraform
```

- **Contrato**: `NormalizedBillingRecord` + validador + `BillingItem` + tabela
  ganham campos **opcionais**. Parser legado não os preenche e nada muda para
  ele.
- **Desacoplamento**: o nome de coluna de cada provedor fica no parser dele. O
  enriquecimento lê só campos canônicos (e o `raw_payload` como fallback do
  legado). Consolidação e fórmula de amortização são funções puras, testadas
  sem banco.
- **Migração**: duas, aditivas e reversíveis (`billing_items`: `usage_type`,
  `resource_type`, `charge_kind`, `cost_billed`, `attributes`;
  `billing_enriched`: `resource_id`, `attributes`).

---

## 6. Mocks

**Princípio:** o mock é derivado do bill real, e o dinheiro se conserva. Quem
lê o mock precisa conseguir reconciliá-lo com o bill de onde saiu. Se não
reconcilia, o mock está errado, e o teste diz isso.

**AWS CUR 2.0** (`mock_aws_cur2_2026-03.csv.gz`, granularidade **diária**, com
recursos), gerado a partir de `aws_cost_explorer_full.csv`:
- cada `BoxUsage:<tipo>` com H horas vira ⌈H/744⌉ instâncias (`i-…`
  determinístico), as horas distribuídas por dia, com `product` (vCPU, memória,
  SO) e `product_instance_type`;
- a região sai do prefixo do `UsageType` do bill real (`SAE1-` → `sa-east-1`,
  `USE2-` → `us-east-2`, sem prefixo ou `USE1-` → `us-east-1`), e
  `product_region_code` é coerente com ele;
- linhas com custo zero no bill real (cobertas pelo SP) viram
  `SavingsPlanCoveredUsage` + `SavingsPlanNegation`. O custo efetivo delas sai
  do SP real (US$ 10.081,65), rateado pelo valor sob demanda, com 95% de uso e
  5% ocioso na `SavingsPlanRecurringFee`;
- linhas com custo **parcial** (parte coberta, parte sob demanda) são
  divididas: as horas sob demanda são `custo real ÷ preço sob demanda`, o que
  deixa o `Usage` com exatamente o custo do bill real; o restante das horas é
  coberto pelo SP;
- o preço sob demanda vem de uma tabela de referência declarada no gerador
  (preço público aproximado por tipo, us-east-1). Ela só serve para gerar o
  valor sob demanda do mock, e **nenhum número de produção sai dela**. Um
  invariante barra desconto implícito do SP fora de [20%, 72%]: fora dessa
  faixa, o mock seria implausível para um Compute SP real;
- EBS vira volumes (`vol-…`) com GB-mês; S3 vira buckets; RDS vira instâncias
  `db.*` com motor e multi-AZ (motor escolhido pelo gerador e declarado, já que
  o bill real não o informa); o resto vira linha sem recurso (transferência,
  requisições), preservando o custo por `UsageType`;
- `Tax` do bill real vira `line_item_line_item_type = Tax`;
- **invariantes testados**: Σ custo efetivo (sem imposto) = total do bill real
  sem imposto, com tolerância de centavos; Σ `unblended` idem; Σ
  `CoveredUsage` + Σ `Negation` = 0; Σ custo efetivo do coberto + ocioso =
  compromisso real.

Uma variante **horária** pequena (um dia, poucos recursos) testa a
consolidação.

**GCP BigQuery** (`mock_gcp_bigquery_2025-12.jsonl.gz`, export detalhado),
gerado a partir de `gcp_cost_table_full.csv`:
- core-hora e RAM-hora por projeto viram VMs com `machine_spec` compatível
  (`e2-standard-N`/`highmem`/`highcpu`, escolhido pela razão RAM/vCPU real),
  com `system_labels` e `resource.global_name`;
- `SPENDING_BASED_DISCOUNT` vira crédito `DISCOUNT`; moeda BRL mantida;
- **invariante**: Σ (`cost` + créditos) por (projeto, SKU) = total do Cost Table.

Mais uma variante CSV achatada (colunas `service.description`…) para o
caminho de CSV.

**Azure e OCI**: não temos bill real dessas nuvens. Os mocks são sintéticos e
pequenos (dezenas de recursos), com preço de varejo aproximado declarado no
cabeçalho do gerador, e têm invariantes internos (soma e unidade). Não há
invariante contra bill real porque não há bill real.

Tudo sai de `scripts/gerar_mocks_billing.py`, com semente fixa: rodar duas
vezes dá byte a byte o mesmo arquivo.

---

## 7. Etapas

Cada etapa termina com: testes da etapa, suíte inteira verde, caracterização
do legado intacta, e uma revisão de impacto (o que ela muda para as etapas
anteriores e para as seguintes). Um commit por etapa.

| etapa | entrega | testes | critério de saída |
|---|---|---|---|
| **E1** contrato | campos opcionais no VO, validador, entidade, modelo, repo, normalizer; migração; resposta de `/items` ganha os campos novos como opcionais | round-trip no Postgres; parser legado não preenche nada novo | suíte verde; `alembic upgrade/downgrade` |
| **E2** mocks | gerador + fixtures + teste de invariantes (feito com pandas direto, **sem** o parser sob teste) | invariantes da §6 | mocks reconciliam com os bills reais |
| **E3** CUR | parser vetorizado; natureza; amortização; região/conta/tipo/atributos/tags; `consolidar()`; Parquet (`pyarrow` em `requirements.txt`, leitor e detector, `.parquet` na allowlist da rota de upload) | exemplo da doc da AWS (t3.nano: 0,0052/0,0026/−0,0078 → 0,0026/0,0013); RI sintético; colunas de SP ausentes → degradação declarada; mock → totais batem com E2 | D1–D9 fechados |
| **E4** BigQuery | unidade de preço; imposto; política de crédito; rateio de CUD; `system_labels`; CSV achatado; NDJSON `.json.gz`; `.jsonl` na allowlist | idem, contra o mock GCP | D10–D16b fechados |
| **E5** enriquecimento | descritor canônico; rota por natureza; `resource_id` + `attributes`; paginação | legado idêntico; mock AWS sem `AmazonEC2` como descritor de compute | D24–D27 fechados |
| **E6** agregador | shape medido; componente `commitment`; recursos/tipos/tempo ativo por pool; GB e volumes medidos; bancos medidos | pools do mock com vCPU = Σ(horas × vCPU do tipo) / janela | nenhum pool com custo zero e capacidade > 0 no mock AWS |
| **E7** payload | `inventario_medido`; `base_de_custo`; `desconto_compromisso_origem`; `dimensionamento_estimado` coerente | caracterização do legado byte a byte; auditoria do payload do mock | reconciliação sem violação |
| **E8** Terraform | disco/banco medidos; premissas com contagem; aviso de consolidação | renderers das 4 nuvens + `terraform validate` | pacote válido a partir de cada mock |
| **E9** Azure/OCI | D17–D23, D28: parsers, regras de classificação, mocks | parsers + regras + pipeline | cobertura de classificação ≥ 95% nos mocks |
| **E10** E2E | HTTP: upload → ingestão → análise → Terraform → validate, para cada mock; relatório de coerência; docs | coerência de números entre as camadas | relatório sem inconsistência aberta |

---

## 8. Riscos

| risco | mitigação |
|---|---|
| O mock reflete o nosso entendimento, e não o export real | colunas conferidas na doc oficial (§1); o parser aceita as variantes documentadas (CUR 2.0, CUR legado, mapa JSON ou colunas achatadas); quando chegar dado real, ele vira fixture e o teste do mock vira teste de regressão |
| Mudar a base de custo muda número | só nos parsers detalhados; o legado é travado pela caracterização |
| Consolidação perde informação | a chave inclui operação (Windows ≠ Linux), unidade e natureza; primeiro/último uso e dias ativos são guardados |
| `pyarrow` pesa na imagem (~100 MB) | Parquet é o formato padrão de quem consulta CUR no Athena; o ganho justifica. Se o peso virar problema, cai para extra opcional |
| Upload de CUR real passa de 50 MB | orientar export **diário** com `INCLUDE_RESOURCES` (ordens de grandeza menor que o horário); o limite é configurável |
| Semântica de correção da OCI | a doc define `backReferenceNo` como "a linha corrigida", mas não diz se a correção **substitui** ou **soma delta**. Mantenho a semântica atual (substitui) e só corrijo o nome da coluna. Fica marcado para validar com o primeiro relatório real |
| Dado pessoal no CUR (`line_item_iam_principal`, `line_item_user_identifier`) | a consolidação não os carrega para o `raw_payload`. Hoje eles iriam inteiros para o JSONB |

---

## 9. Fora de escopo

- **Utilização de CPU/memória**: billing dá horas ligadas, não carga. Right-sizing
  por utilização precisa de CloudWatch, Cloud Monitoring etc.; fica para a API
  (`PLANO_INVENTARIO_API.md`).
- **Topologia, dependências, criticidade**: não estão em nenhum export de
  billing. Também ficam para o plano da API.
- **Migração de dados** (conteúdo de bancos e buckets).

## 10. Recomendado para depois: FOCUS

As quatro nuvens exportam FOCUS 1.x, com `EffectiveCost`, `ResourceId`,
`ChargeCategory` e `CommitmentDiscount*` padronizados. Um parser FOCUS cobriria
as quatro com uma semântica só. Não entra agora porque o cliente vai trazer o
export nativo primeiro. Depois desta etapa, porém, ele vira um parser a mais
atrás do mesmo contrato canônico, sem mudar nada a jusante.

---

## 11. Revisão crítica

Três rodadas sobre o próprio plano. As correções já estão incorporadas no
texto acima; aqui fica o que estava errado e por quê.

### Rodada 1: falhas de lógica no rascunho

| # | falha no rascunho | correção |
|---|---|---|
| 1 | Supunha que o CUR sempre traz as colunas de SP/RI. O CUR 2.0 deixa **escolher colunas** | RN1 ganhou a degradação: base faturada marcada, premissa `misto`, diagnóstico de compromisso volta a disparar |
| 2 | `adjustment` "seguia como hoje". Goodwill e crédito de SLA são valores **negativos** e reduziriam o custo do serviço em que caíssem | RN2/RN3: ajuste e crédito saem da base. Correção de uso/preço fica, porque corrige custo recorrente |
| 3 | `Discount`/`BundledDiscount` sem destino | saem da base: o desconto já está nas colunas `net_*`, e somar os dois contaria duas vezes |
| 4 | Consolidação sem deduplicação prévia e sem tipo de recurso/base na chave | RN6: dedupe por identidade da linha antes; chave ampliada |
| 5 | Parquet sem a rota: `.parquet` seria recusado com 415 antes de chegar no parser | E3 inclui a allowlist e a dependência |
| 6 | `.json.gz` do `bq extract` não era lido nem hoje | virou D16b, fechado em E4 |
| 7 | `attributes` misturaria fato e estimativa (RAM da Azure) | RN4: só fato medido; estimativa fica no agregador |
| 8 | Preço sob demanda do mock sem fonte | §6: tabela de referência declarada + invariante de plausibilidade do desconto |
| 9 | Semântica de correção da OCI assumida sem verificação | declarada como risco; semântica atual mantida |

### Rodada 2: consistência entre regras

| # | falha | correção |
|---|---|---|
| 10 | "desconto de compromisso" não dizia sobre quais linhas somar: incluir linhas não cobertas diluiria o número | RN11 com fórmula e nome precisos (`desconto_origem_sobre_lista`) |
| 11 | "fração de tempo ativo" sem definição: no GCP não existe linha por instância | RN9 define a fórmula e onde ela é calculável |
| 12 | Região do mock sem regra: pools do mock sairiam `global` e não testariam o D1 | §6: região derivada do prefixo do `UsageType` real |
| 13 | A fixture CUR atual não tem `line_item_line_item_type`; o parser novo precisa aceitá-la | RN1: sem a coluna de tipo, `usage` na base faturada |

### Rodada 3: efeitos colaterais fora do pipeline

| # | pergunta | resposta verificada no código |
|---|---|---|
| 14 | A consolidação mensal quebra o dashboard? | Não. `generate_billing_dashboard` agrega por `billing_period` (mês); nenhum consumidor usa `usage_start` por dia |
| 15 | O parser de imposto de hoje depende do `raw_payload`? | Sim (`RecordType`, `ProductCode`, `Cost type`). Continua como fallback para o legado; parser detalhado decide por `charge_kind` |
| 16 | `.jsonl` (outra extensão comum de NDJSON) passa na rota? | Não. Entra na allowlist em E4 |
| 17 | Conflito entre "crédito fora da base" (RN3) e "crédito estrutural dentro do custo" (RN1 GCP)? | Não: RN3 trata **linha** de crédito; RN1 trata crédito **anexado** à linha de uso (array `credits`). Promoção anexada também não reduz a base, e as duas regras dizem a mesma coisa |

Nenhuma falha aberta depois da rodada 3. Revisão de novo a cada mudança de
regra de negócio durante a execução (regra do projeto).

### Rodada 4: achados passando os mocks pelo pipeline real (E4–E6)

Nenhum destes aparecia lendo código: surgiram ao rodar os mocks de ponta a
ponta e olhar os números. Vários afetam também o caminho legado, e foram
corrigidos lá (os gates de integração seguem verdes).

| # | achado | efeito | correção |
|---|---|---|---|
| 18 | Regras sem `*` na frente (`rds:*`, `ebs:*`, `aurora:*`, `elasticache:*`) | tipo de uso com prefixo de região escapava: storage do RDS em São Paulo virava disco | padrões com `*` na frente |
| 19 | `EBS:SnapshotUsage` casava `ebs:*` (SSD) antes de `*snapshotusage*` (HDD) | o mesmo snapshot virava SSD ou HDD conforme a região | snapshot com prioridade 2 |
| 20 | Faltavam regras para Aurora I/O-Optimized, suporte estendido, Performance Insights, VPN, VPC Endpoint, IPv4 público, DNS | US$ 4 mil do mock (e US$ 1,7 mil do bill real) sem classificação | regras novas; órfão do mock 10,2% → 5,8% |
| 21 | `extract_family` lia o prefixo de região da AWS (`USE1-`) como família | monitoramento por vCPU-hora virava `core` da família "USE1": capacidade fantasma | prefixo de região ignorado no início do descritor |
| 22 | SKU "E2 Instance Core running free tier discount" (horas positivas, custo negativo) | pool fantasma de custo NEGATIVO, marcado "coberto por compromisso" (inclusive no legado) | free tier sai da base (RN1/RN3 ajustadas): não migra, e o destino é cotado sem ele |
| 23 | Linha de uso detalhada sem capacidade (CPU credit) caía no balde de compromisso | custo real de uso rotulado como compromisso | vai para órfão; no legado continua compromisso (único caso que o bill agregado produz) |
| 24 | Ordenação de `billing_items` não era total | paginação por offset repetiria e pularia linhas | `id` como desempate |

**Reanálise depois das mudanças de regra:** RN1 (free tier fora dos créditos
estruturais) e RN3 (free tier fora da base) mudaram. Revisei quem consome
`cost`: dashboard (lê `billing_items`, inclui a linha de crédito, que lá é
informação de fatura), enriquecimento (exclui), agregador e reconciliação
(recebem só o enriquecido). A conservação da reconciliação continua fechando
nos dois mocks e nos dois bills reais.
