# Precision Engine — arquitetura de precisão dos números

> Documento guarda-chuva da fase de PRECISÃO. Sucede — sem invalidar — as specs
> [`sku-matching.md`](sku-matching.md) (implementada: coverage GCP 98,6% /
> AWS 96,0%) e [`cross-cloud-equivalence.md`](cross-cloud-equivalence.md) (E1–E7
> implementados). Plano de execução vivo: `ost-docs.backend.plan/precision_engine_execution_plan.md`
> (local-only). Benchmark de referência: AWS Migration Evaluator, Azure Migrate,
> Google Migration Center, Infracost, Cloudamize, Densify, CAST Highlight, FOCUS.
>
> **Revisão 2026-07-28 — spec AS-BUILT.** As ondas 1–8 foram executadas e a
> implementação divergiu do plano em pontos importantes (marcados com ⚠️). Este
> documento agora descreve **o que existe**, não o que se pretendia; o que segue
> pendente está na §7.

## 0. Context

A fase anterior resolveu **classificação** (que recurso é cada linha de billing).
O erro dominante estava nos **números comparados** — e, descoberto na auditoria
de 2026-07-28, também na **informação enviada ao agente**:

| # | Defeito | Status |
|---|---|---|
| 1 | `delta_custo` comparava BRL (origem) com USD (catálogo) | ✅ resolvido (§3.1) |
| 2 | Preços do catálogo eram seeds mock | ✅ oficiais nos 4 provedores, por região (PLANO_PRECO_REGIONAL) |
| 3 | `match_pool` escolhia SKU mais barato e multiplicava (14×1vCPU) | ✅ resolvido (§4.2) |
| 4 | Matching por vCPU/RAM ≥ apenas (ignora classe de família) | ⚠️ arquitetura e burstable resolvidos (§3.3); `perf_coef` pendente |
| 5 | `payback_period_months` vinha do LLM | ⚠️ no envio do agente, derivado da premissa dele (§3.8); na tela, sem custo de migração, continua nulo |
| 6 | Serviços não-VM sem modelo de unidade | ⚠️ parcial — razão de taxa (§3.5) |
| 7 | Custo de compromisso (Savings Plan/RI/CUD) inflava o preço/vCPU do pool | ✅ resolvido (§4.1) |
| 8 | Instance-hours tratados como vCPU-hours (pool 8× subdimensionado) | ✅ resolvido (§3.6) |
| 9 | Serviços nomeados e DE-PARA nunca chegavam ao agente | ✅ resolvido (§4.5) |
| 10 | Schema pedia ao LLM números que o backend já calculava | ✅ resolvido (§4.6) |

**Tese do v3** (validada): acurácia de recomendação =
`verdade dos preços × equivalência correta × premissas explícitas × auto-auditoria`.

O quarto fator não estava na tese original — emergiu na execução e virou o
Pilar E (§5).

## 1. Goals — status

- **G1** Todo `delta_custo` em moeda única, com taxa registrada — ✅
- **G2** `confianca_preco` alta para ≥90% do custo comparado — ⚠️ compute
  oficial nos 4 provedores; disco da Azure e banco gerenciado seguem `reference`
- **G3** Mix alvo ótimo por custo total com desperdício controlado — ✅
- **G4** Calculadora por categoria, zero if/else por cloud — ⚠️ parcial (§3.5)
- **G5** `payback_period_months` calculado no backend — ⚠️ com a premissa do agente (§3.8)
- **G6** Ledger de premissas em 100% dos payloads — ✅

## 2. Non-Goals

- Right-sizing por telemetria (P95) — atrás do knob `fator_utilizacao`.
- Descontos enterprise negociados (EA/EDP/PPA).
- LLM continua proibido de calcular — agora **garantido em código** (§4.6).

## 3. Data Contracts (as-built)

### 3.1 `exchange_rates` — implementado
```sql
CREATE TABLE exchange_rates (
  par    VARCHAR(7)    NOT NULL,        -- 'BRL/USD'
  data   DATE          NOT NULL,
  taxa   NUMERIC(18,8) NOT NULL,        -- unidades da origem por 1 do destino
  fonte  VARCHAR(40)   NOT NULL DEFAULT 'manual',
  PRIMARY KEY (par, data)
);
```
Migration `f5a6b7c8d9e0`. Fallback por env `FX_BRL_USD` quando a tabela não tem o
par. Domínio: `domain/services/currency.py` (`FxRate`, `convert`, `as_ledger`).
Sem taxa conhecida a fonte vira `SEM_TAXA_CAMBIO` — sinaliza em vez de mentir.

### 3.2 `cloud_price_points` — implementado ⚠️ (schema difere do plano)
```sql
CREATE TABLE cloud_price_points (
  provider     VARCHAR(10)   NOT NULL,
  sku_key      VARCHAR(120)  NOT NULL,   -- chave nativa (armSkuName, partNumber…)
  region       VARCHAR(60)   NOT NULL,
  service_name VARCHAR(200)  NOT NULL,
  service_type VARCHAR(100)  NOT NULL,   -- 'virtual_server' | categoria não-compute
  price        NUMERIC(18,8) NOT NULL,
  currency     VARCHAR(3)    NOT NULL,
  unit         VARCHAR(30)   NOT NULL DEFAULT 'instance_hour',
  vcpus        NUMERIC(6,2),
  ram_gib      NUMERIC(8,2),
  source       VARCHAR(40)   NOT NULL DEFAULT 'manual',
  confidence   VARCHAR(10)   NOT NULL DEFAULT 'official',
  captured_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, sku_key, region)
);
CREATE INDEX ix_price_points_provider_type ON cloud_price_points (provider, service_type);
```
Migration `a6b7c8d9e0f1`. **Divergências do plano, e por quê:**

- Sem `preco_1yr`/`preco_3yr` — commitment tiers ficaram para depois (§7).
- Specs (`vcpus`/`ram_gib`) em colunas, não em `atributos` JSONB: vêm da mesma
  resposta da API, então o registro é **autossuficiente** e o matcher o consome
  sem precisar casar chave com o `cloud_sku_catalog` seed.
- `cloud_sku_catalog` **não** virou view: continua como fallback. O matcher
  prefere `cloud_price_points` e rebaixa a confiança quando cai no seed.

Fontes implementadas (`infrastructure/pricing/`):

| Fonte | Módulo | O que traz |
|---|---|---|
| AWS, calculadora pública | `aws_price_client.py` | EC2 Linux on-demand por região (famílias m/c/r/t) |
| GCP Cloud Billing Catalog (API key) | `gcp_price_client.py` | taxas vCPU/h e GB/h → tipos reais (E2, N2, N2D, N4, C3, C4A, T2A; standard/highmem/highcpu) |
| Azure Retail Prices API (público) | `azure_price_client.py` | VMs Linux on-demand por região (B/D/E/F, sem DC/EC confidenciais) |
| OCI cetools API (público) | `oci_price_client.py` | taxas flex → configs com 4, 8 e 16 GB por OCPU, gravadas em cada região |
| Categorias, 4 provedores | `category_rate_client.py` | SSD, HDD, objeto e egress por região; disco da Azure fica de fora (preço por disco). Rede por tipo: balanceador, processamento, NAT, IP, VPN e tráfego entre zonas |
| Referência publicada | `seed_category_rates.py` | storage/egress/database dos 4 provedores (`us-east`) |

Sync offline por zona: `python scripts/sync_prices.py --providers aws,gcp,azure,oci,categories,categories-live`
(`--zonas`, padrão `virginia,meio-oeste,sao-paulo,texas,california`).

**Cotação por região (PLANO_PRECO_REGIONAL).** Cada pool é cotado na zona dele
(`domain/services/regioes.py`, 11 metrópoles equivalentes). Sem preço da
região, outra da mesma zona; preço global (compute e armazenamento da OCI); por
último a Virgínia, com o rótulo rebaixado para `reference`. Categorias usam a
zona dominante do bill. O payload carrega `financeiro.preco_regiao`,
`taxa_origem_regiao`/`taxa_destino_regiao` e `premissas.regioes`. São Paulo
custa ~1,6x a Virgínia em AWS, Azure e GCP; a OCI cobra o mesmo.

### 3.3 `family_equivalence` — ⚠️ arquitetura e burstable (PLANO_PRECO_REGIONAL R5)
`domain/services/perfil_de_familia.py` lê do nome a arquitetura (x86/ARM) e se a
família é burstable. O packer não cota carga sustentada em burstable e tenta a
mesma arquitetura primeiro; a outra só entra quando nenhum mix da mesma cobre o
pool, marcada `arquitetura_diferente`. A classe por razão RAM/vCPU fica com a
penalidade de desperdício do packer. `perf_coef` segue pendente (§7): não há
fonte pública confiável de desempenho por vCPU entre gerações.

### 3.4 `premissas` (assumption ledger) — implementado, mais rico que o plano
```json
"premissas": {
  "fx": {"par": "BRL/USD", "taxa": "5.70", "data": null, "fonte": "config"},
  "moeda_origem": "BRL",
  "moeda_analise": "USD",
  "janela_horas": 730.0,
  "fonte_precos": "cloud_price_points (oficial) + seed_catalog_pareto (fallback)",
  "confianca_precos": {"azure": "official", "oci": "official", "aws": "low"},
  "cobertura_classificacao": 0.9857,
  "cobertura_analise": 0.748,
  "dimensionamento_estimado": 0.31,
  "fator_utilizacao": 1.0,
  "reconciliacao": { "ok": true, "resumo": {...}, "achados": [...] }
}
```
`confianca_precos` é **por provedor** (não um escalar) e `reconciliacao` (§5) não
existia no plano.

### 3.5 Calculadoras por categoria — ⚠️ implementado por razão de taxa
O plano previa um registry `ICategoryCalculator` com modelo de unidade próprio.
O que existe é `domain/services/category_calculator.py`, que reprecifica por
**razão de taxa**:

```
custo_alvo = custo_origem × (taxa_alvo / taxa_origem)
```

**Motivo da divergência:** `usage_unit`/`usage_amount` dos parsers é inconsistente
(vazio na AWS, escalas de bilhões no storage), mas o **custo** é confiável —
conservação validada ao centavo. A razão cancela o GB implícito e depende só de
uma tabela de taxas. Cobre storage SSD/HDD, egress e database; falta o modelo de
unidade fino (IOPS, engine, requests, tiers de egress).

Confiança do resultado = **elo mais fraco** entre a taxa de origem e a de destino.

**Rede por tipo, pela quantidade medida** ([`PLANO_PRECO_NAO_COMPUTE.md`](../PLANO_PRECO_NAO_COMPUTE.md)).
O balde `network_egress` misturava egress, balanceador, NAT, IP público, VPN e
tráfego entre zonas, e tudo saía pela taxa de egress. `domain/services/rede_por_uso.py`
separa cada linha pelo uso (tabela por nuvem). Balanceador (hora e processamento),
NAT (hora e GB), IP, VPN e tráfego entre zonas são cotados por **quantidade medida ×
taxa do destino**, sem precisar da taxa da origem; a linha sem quantidade usa o preço
efetivo das medidas. Taxa zero é destino grátis (NAT, VPN e IP da OCI). A confiança é
a da taxa do destino. Endpoint, DNS, resolver e log (`network_other`) ficam ao custo
atual. O egress de verdade segue pela razão.

A taxa de `storage_ssd` é a do SSD **de propósito geral** de cada provedor: gp3,
Premium SSD, **pd-balanced** e Block Volume balanceado. No GCP era a do pd-ssd
(US$ 0,170, o tier de desempenho). Somado à regra que levava o Balanced PD para
HDD, isso tratava o disco padrão do GCP como magnético na origem e como o SSD
mais caro no destino. `terraform_plan._TAXA_GB_MES` é uma cópia pura destas
taxas; um teste trava as duas juntas.

### 3.6 Shape de instância — não estava no plano
`domain/services/instance_shape.py` e `azure_shape.py` derivam (vCPU, RAM) do
**esquema de nomenclatura**, não de tabela por instância:

- AWS: tamanho → vCPU (`Nxlarge = N×4`), letra de classe → GiB/vCPU (c=2, m=4, r=8, i=6).
- Azure: número após a família → vCPU; letra → GiB/vCPU. Séries D/E/G v1–v2 são
  **rejeitadas** (ali o número é índice, não contagem: `D11_v2` são 2 vCPU) —
  melhor não dimensionar do que inventar spec.

Sem isso, uma linha `BoxUsage:m7g.2xlarge` (hora-de-instância) era contada como
hora-de-vCPU e o pool saía 8× subdimensionado.

### 3.7 Expectativa de migração — não estava no plano (era parte do payback)
`domain/services/migration_expectation.py` deriva a classe 6R da metadata curada:

```
requires_refactoring            → refactor    (esforço alto)
lock-in médio/alto, sem reescrita → replatform  (médio)
portável, sem reescrita         → rehost      (baixo)
```
Sem metadata retorna `None` — a IA saber que não temos o dado é melhor que
receber palpite. Alimenta o campo `migracao` de cada serviço no payload.


### 3.8 Payback — derivado da premissa do agente (PLANO_PRECO_REGIONAL R6)
O backend não modela o custo único de migração. Quando o agente declara
`payback.premissa_custo_migracao_usd`, `agent_result._derivar_payback` calcula
`meses = ⌈premissa ÷ economia mensal do provedor selecionado⌉` e sobrescreve o
`meses` dele (registrado em `campos_sobrescritos`). Sem economia, `meses: null`
com `motivo_backend`. Autoridade `agent-authority-v2`.

## 4. Behavior (regras-chave, as-built)

1. **FX antes de tudo.** Conversão acontece antes de qualquer `delta_custo`.
   Nenhum ponto do payload mistura moeda: `gasto_total`, pools, categorias e
   `resumo_executivo` saem todos em `moeda_analise` (USD).

2. **Capacidade só entra com o custo correspondente** (`services/billing_aggregator.py`).
   Invariante: *custo só entra no pool se a capacidade entrou*. Linha com papel de
   compute sem quantidade de uso (Savings Plan, RI, CUD) vai para o balde
   `commitment`. Foi o que unificou os bugs de Cloud Run (GCP) e Savings Plans (AWS).

3. **Família por forma, não por lista.** `sku_matcher.extract_family` usa um shape
   estrutural (`<letras><dígito><sufixo>` + separador de tamanho); antes era uma
   whitelist só-GCP e a AWS inteira colapsava num pool `general`.

4. **Packing por custo total** com penalidade de desperdício e teto de instâncias
   **relativo** (`max(12, menor_mix × 3)`) — teto absoluto impedia mix para pools
   grandes (228 vCPU).

5. **Nome casa por `squash_key`.** Parsers emitem `AmazonRDS`; o catálogo curado
   usa `Amazon RDS`. `domain/services/sku_normalization.squash_key` remove espaço
   e pontuação. Sem essa ponte o casamento falhava e o `service_type` vinha do
   classificador de SKU — foi assim que object storage virou block storage.
   Desambiguação 1:N (um serviço aparece uma vez por capacidade) prefere a
   capacidade sugerida pelo classificador.

6. **Backend manda no número** (`use_cases/analysis/backend_authority.py`).
   Após o parse da resposta, os campos numéricos e o provedor recomendado são
   sobrescritos com `resumo_executivo`; o qualitativo do modelo é preservado.
   Provedor alucinado é descartado; provedor omitido entra com o número.

7. **Instrumento financeiro não migra.** Serviço com custo e nenhuma quantidade de
   uso não recebe DE-PARA nem expectativa de migração.

## 5. Pilar E — Reconciliação (não estava no plano)

`domain/services/reconciliation.py`, executado em toda análise, grava em
`premissas.reconciliacao`. Cinco checagens que são **propriedades do modelo de
pool** — nenhuma cita nome de nuvem:

| Código | Severidade | O que pega |
|---|---|---|
| `conservacao_custo_violada` | violação | soma dos baldes ≠ gasto total |
| `preco_vcpu_implausivel` | suspeita | $/vCPU-mês fora da banda [2, 120] |
| `familia_generica_concentrada` | suspeita | gasto concentrado em família genérica (extração falhou) |
| `provedor_sem_base_preco` | info | alvo sem preço para dimensionar |
| `capacidade_sem_custo_direto` | info | vCPU real coberto por commitment |
| `cobertura_classificacao_baixa` | suspeita | abaixo de 95% |

**Ponto cego conhecido:** valida consistência **interna**, não validade
**externa**. Não pega taxa de referência otimista nem viés sistemático (ver §7).

## 6. Estado medido (2026-07-28, sobre os 2 CSVs reais)

| Métrica | Antes da fase | Agora |
|---|---|---|
| Cobertura de classificação | 36% GCP / 31% AWS | 98,6% / 96,0% |
| Cobertura de análise (com equivalente) | ~18% | 78,7% |
| DE-PARA por custo (AWS) | 26,5% | 86,3% |
| Custo com `service_type` divergente do catálogo | 77,7% ($71.590) | 0,0% ($1) |
| Suspeitas de reconciliação (AWS) | 12 | 0 |
| Testes | ~130 | 241 (214 unitários + 27 gates) |

Payload: `pools_computacao`, `custos_auxiliares`, `servicos` (nomeados, com
DE-PARA e `migracao`), `premissas`, `resumo_executivo`, `confianca_classificacao`.

## 7. Pendente

| Item | Por que ainda não |
|---|---|
| `perf_coef` | sem fonte pública de desempenho por vCPU entre gerações |
| Custos auxiliares por zona | disco, objeto e egress usam a zona dominante do bill; um bill com 12% em São Paulo cota tudo no meio-oeste |
| Disco da Azure e banco gerenciado oficiais | a Azure cobra por disco (P10, P20…), não por GB; banco não tem índice $/vCPU único |
| Commitment tiers (1yr/3yr) | custo é segregado, mas não há cenário |
| `payback_period_months` na tela | o fluxo do LLM não tem de onde tirar o custo de migração |
| `ICategoryCalculator` pleno | falta modelo de unidade fino por categoria; a rede por tipo já cota pela quantidade medida |
| **Validade externa das taxas** | 61% do custo AWS é reprecificado por taxa `reference` digitada; a reconciliação não pega viés (ex.: um provedor mais barato em *todas* as categorias) |
| `fator_utilizacao` por telemetria | knob existe fixo em 1.0 |
| Filestore como `storage_hdd` | é arquivo (NFS), não bloco; não há categoria `file_storage`. No mock BigQuery são 234 GiB e US$ 358/mês que viram volume HDD no Terraform |
| SKU `Storage` do Source Repositories | cai no catch-all `*storage*` → `storage_hdd` (custo zero, mas 185 GiB medidos viram volume). A regra L2 só vê a descrição do SKU, que não diz o serviço |

## 8. Verificação contínua

`app/tests/integration/` roda a pipeline inteira sobre os 2 CSVs reais
versionados e trava cada invariante acima. Pula graciosamente sem Postgres;
no CI (etapa 3 de `.github/workflows/devsecops.yml`) um skip é **falha** de ambiente.

`scripts/measure_request_quality.py` mede a qualidade de conteúdo do request
(DE-PARA por custo, divergência vs catálogo, cobertura de expectativa).

⚠️ **Ordem dos seeds importa.** `seed_service_metadata` também grava em
`cloud_services_comparation` a partir de uma lista inline com tipos grosseiros;
a fonte autoritativa é `cloud_services_mapping.csv` (816 linhas), carregada por
`seed_from_csv`, que **limpa e recarrega**. Ele tem que rodar **por último** —
senão um ambiente novo (inclusive produção) fica com o tipo grosseiro e o DE-PARA
perde precisão. Ver [`dev-setup.md`](dev-setup.md).

## 9. Riscos

| Risco | Mitigação |
|---|---|
| APIs de preço mudam formato | clients isolados por fonte; `source`/`captured_at` no registro |
| Taxas de referência enviesadas | §7 — falta checagem de plausibilidade externa |
| FX desatualizado | data no ledger; `SEM_TAXA_CAMBIO` quando ausente |
| Lint/CI não reprodutível | `ruff` pinado; local == CI |
