# Precision Engine v3 — SDD Spec (master)

> Documento guarda-chuva da fase de PRECISÃO. Sucede — sem invalidar — as specs
> [`sku-accuracy-spec.md`](sku-accuracy-spec.md) (implementada: coverage GCP 98,6% /
> AWS 96,0%) e [`pool-equivalence-spec.md`](pool-equivalence-spec.md) (E1–E7
> implementados). Plano de execução vivo: `ost-docs.backend.plan/precision_engine_execution_plan.md`
> (local-only). Benchmark de referência: AWS Migration Evaluator, Azure Migrate,
> Google Migration Center, Infracost, Cloudamize, Densify, CAST Highlight, FOCUS.

## 0. Context

A fase anterior resolveu **classificação** (que recurso é cada linha de billing).
O erro dominante agora está nos **números comparados**:

| # | Defeito ativo | Efeito |
|---|---|---|
| 1 | `delta_custo` compara BRL (origem) com USD (catálogo) sem conversão | economia superestimada ~5,7×; recomendação pode inverter |
| 2 | Preços do catálogo são seeds mock | `confianca_preco=low` em 100% dos equivalentes |
| 3 | `match_pool` escolhe SKU mais barato e multiplica | mix absurdo (14×1vCPU/15GB p/ 13vCPU/52GiB — RAM 4× over) |
| 4 | Matching por vCPU/RAM ≥ apenas | ignora classe de família (burstable↔compute-opt) e perf real de CPU por cloud |
| 5 | `payback_period_months` vem do LLM | custo one-time de migração nunca é calculado |
| 6 | Serviços não-VM sem modelo de unidade | Cloud SQL/GKE/serverless/egress viram bucket auxiliar sem equivalência dimensionada |

**Tese do v3** (validada pelo benchmark): acurácia de recomendação =
`verdade dos preços × equivalência semântica por categoria × premissas explícitas`.

## 1. Goals

- **G1** Todo `delta_custo` em **uma única moeda**, com taxa e data registradas.
- **G2** `confianca_preco=high` para ≥90% do custo comparado (preços de API oficial).
- **G3** Mix alvo ótimo por custo total com desperdício de RAM ≤ 50% do necessário.
- **G4** Cada categoria de serviço (compute, storage, database, k8s, serverless,
  egress) com calculadora e modelo de unidade próprios — zero if/else por cloud.
- **G5** `payback_period_months` calculado no backend (egress one-time + esforço 6R);
  LLM apenas narra.
- **G6** Ledger de premissas (`premissas`) presente em 100% dos payloads e respostas.

## 2. Non-Goals

- Right-sizing por telemetria (P95) — fica atrás do knob `fator_utilizacao` (C3),
  conectores de métricas são fase futura.
- Descontos enterprise negociados (EA/EDP/PPA) — fora do escopo; tiers públicos
  on-demand/1yr/3yr apenas.
- LLM continua proibido de calcular (inalterado).

## 3. Data Contracts

### 3.1 `exchange_rates` (novo)
```sql
CREATE TABLE exchange_rates (
  par         VARCHAR(7) NOT NULL,      -- 'BRL/USD'
  taxa        NUMERIC(18,8) NOT NULL,
  data        DATE NOT NULL,
  fonte       VARCHAR(40) NOT NULL,     -- 'manual' | 'bcb' | 'ecb'
  PRIMARY KEY (par, data)
);
```
MVP: taxa manual via env `FX_BRL_USD`; worker de fonte oficial depois.

### 3.2 `cloud_price_points` (novo — estilo Infracost)
```sql
CREATE TABLE cloud_price_points (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider      VARCHAR(10) NOT NULL,
  service       VARCHAR(60) NOT NULL,      -- 'ec2','compute_engine','cloud_sql',...
  sku_code      TEXT NOT NULL,             -- id oficial do vendor
  region        VARCHAR(40) NOT NULL,
  unidade       VARCHAR(30) NOT NULL,      -- 'vcpu_hour','gb_month','request_1m','gb_egress'
  preco_ondemand NUMERIC(18,8),
  preco_1yr      NUMERIC(18,8),
  preco_3yr      NUMERIC(18,8),
  moeda         VARCHAR(3) NOT NULL DEFAULT 'USD',
  atributos     JSONB,                     -- vcpus, ram_gb, familia, engine, iops...
  coletado_em   TIMESTAMPTZ NOT NULL,
  fonte         VARCHAR(30) NOT NULL       -- 'azure_retail_api','aws_price_list',...
);
CREATE INDEX ix_price_points_lookup ON cloud_price_points (provider, service, region, unidade);
```
Fontes (ordem de implementação): Azure Retail Prices API (público, sem auth) →
AWS Price List Bulk (offer files) → GCP Cloud Billing Catalog API → OCI price list.
`cloud_sku_catalog` atual passa a ser VIEW/projeção de `cloud_price_points` p/ compat.

### 3.3 `family_equivalence` (novo)
```sql
CREATE TABLE family_equivalence (
  classe     VARCHAR(20) NOT NULL,   -- 'burstable','general','compute_opt','memory_opt','gpu'
  provider   VARCHAR(10) NOT NULL,
  familia    VARCHAR(20) NOT NULL,   -- 'E2','t3','B','N2','m6i','D'...
  perf_coef  NUMERIC(6,3) NOT NULL DEFAULT 1.0,  -- vCPU normalizado (benchmark-based)
  PRIMARY KEY (provider, familia)
);
```
Matching de compute passa a ser **dentro da mesma classe** (fallback: classe vizinha
com flag), e capacidade comparada como `vcpus × perf_coef`.

### 3.4 `premissas` (assumption ledger — bloco novo no payload e na resposta)
```json
"premissas": {
  "fx": {"par": "BRL/USD", "taxa": "5.70", "data": "2026-06-01", "fonte": "manual"},
  "precos": {"fonte": "azure_retail_api", "coletado_em": "2026-05-30", "tier": "ondemand"},
  "fator_utilizacao": 1.0,
  "cobertura_classificacao": 0.986,
  "janela_horas": 730
}
```

### 3.5 `ICategoryCalculator` (registry — domínio)
```python
class ICategoryCalculator(Protocol):
    categoria: str                       # 'compute'|'storage'|'database'|'kubernetes'|'serverless'|'egress'
    def dimensionar(self, demanda: Demanda) -> list[OpcaoEquivalente]: ...
```
Cada calculadora conhece seu modelo de unidade (compute: vCPU/RAM-h com packing;
storage: GB-mês+IOPS; database: vCPU/RAM/GB+engine; k8s: taxa fixa control plane +
node pools via compute; serverless: vCPU-s + requests; egress: tiers por GB).
Registro data-driven — **zero if/else por cloud** (regra inegociável mantida).

### 3.6 Custo one-time de migração (novo cálculo backend)
```
custo_migracao = egress_gb_estimado × tarifa_egress_origem
              + esforco_semanas(classe_6R) × custo_semana_config
payback_meses  = custo_migracao / economia_mensal   (se economia > 0)
```
`classe_6R` (rehost/replatform/refactor) vem da metadata curada L3 já existente.

## 4. Behavior (regras-chave)

1. Conversão FX acontece **antes** de qualquer `delta_custo`; nunca comparar moedas
   distintas. Sem taxa disponível → flag `SEM_TAXA_CAMBIO`, delta omitido (nunca errado).
2. `match_pool` v3: filtra classe de família compatível → gera candidatos → resolve
   mix por **custo total mínimo** com penalidade λ·RAM_desperdicada e teto de instâncias.
3. Preço buscado por (provider, service, region, unidade) em `cloud_price_points`;
   fallback catálogo mock rebaixa `confianca_preco` e registra em `premissas`.
4. LLM recebe `premissas` e é instruído (data-only, inalterado) — continua sem calcular.
5. Toda resposta persiste (já existente `llm_input_payload`) + `premissas` aplicadas.

## 5. Work Units

| # | Unidade | Toca | Onda |
|---|---|---|---|
| P1 | `exchange_rates` + conversão no pipeline + `premissas.fx` | migração, `equivalence_matcher`, `run_analysis_pipeline` | 1 |
| P2 | Bin-packing custo-total c/ penalidade (fix OCI 14×) | `equivalence_matcher.match_pool` | 1 |
| P3 | Golden CI gate (`e2e_accuracy_report` no CI) | `.github/workflows` | 1 |
| P4 | Worker Azure Retail Prices → `cloud_price_points` | novo worker + migração | 2 |
| P5 | Workers AWS Price List / GCP Catalog / OCI | novos workers | 2 |
| P6 | Commitment tiers no payload (cenários) | serialização, schema V2 | 2 |
| P7 | `family_equivalence` + perf_coef no matching | migração + matcher | 3 |
| P8 | `ICategoryCalculator` registry + 6 calculadoras | domínio + DI | 3 |
| P9 | Ledger `premissas` end-to-end | serialização, schema V2, docs | 4 |
| P10 | Custo one-time + payback backend (6R) | novo serviço + metadata L3 | 4 |
| P11 | `fator_utilizacao` knob | config + aggregator | 4 |
| P12 | Telemetria de acurácia por report + property tests | nova tabela/campos + tests | 4 |

## 6. Acceptance Criteria

- Report BRL (golden `d8be966a`): todos os `delta_custo` em USD com `premissas.fx`
  preenchido; economia E2 na ordem de **dezenas de USD**, não milhares (valida fix 5,7×).
- Mix OCI para pool 13,2 vCPU/52,7 GiB: ≤ 4 instâncias e RAM alvo ≤ 79 GiB (1,5×).
- ≥90% do custo comparado com `confianca_preco=high` após P4–P5.
- `payback_period_months` idêntico entre duas execuções (determinístico, backend).
- Suíte verde + golden CI gate obrigatório no merge.

## 7. Riscos

| Risco | Mitigação |
|---|---|
| APIs de preço mudam formato | workers isolados por fonte + `fonte`/`coletado_em` versionados |
| Volume de price points (milhões) | ingestão filtrada por regiões/serviços em uso; índice de lookup |
| perf_coef vira opinião | usar coeficientes publicados/benchmarks citados; default 1.0 conservador |
| FX manual desatualiza | data da taxa no ledger + flag se > 7 dias |
