# Plano API v2 — equilíbrio entre a inteligência da IA e a confiabilidade do backend

**Regra number one:** o caminho legado (`Frontend → API → LLM`) **não é tocado**.
Tudo novo nasce ao lado, desligado, e entra quando alguém virar o interruptor.

> ## Status — 2026-08-11
>
> | bloco | estado | onde |
> |---|---|---|
> | **0** — preços AWS/GCP | ✅ feito | PR #21 (main) · #23 (develop) |
> | **A** — trava do legado | ✅ feito | PR #18 · tag `baseline/legado-travado` |
> | **B** — proveniência e frescor | ✅ feito | PR #20 |
> | **C** — confiança propagada | ✅ feito | PR #20 |
> | **D** — autoridade por categoria | ✅ feito | PR #24 |
> | **E** — recepção + sugestões | ✅ feito | PR #25 |
> | **F** — `report_id` aditivo | ✅ feito | PR #19 |
> | **G** — o interruptor | ✅ feito | PR #25 (`AGENT_FLOW_ENABLED`) |
>
> **Todos os blocos do plano estão implementados.** O que falta não é código
> deste repo — está em [§9](#9-o-que-falta-para-o-fluxo-novo-funcionar).

---

## 1. Por que o plano anterior estava errado

A primeira versão dizia: *"o backend calcula, a IA narra — onde o backend souber,
ele substitui"*. Medindo os payloads reais, isso não se sustenta:

| bill | preço oficial | **preço de catálogo seed** |
|---|---|---|
| AWS | 40 equiv → $47.259/mês | **20 equiv → $34.143/mês** |
| GCP | 6 equiv → $1.179/mês | 3 equiv → $270/mês |

**42% do dinheiro cotado no bill AWS sai de preço não-oficial** — todos os alvos
GCP, porque `cloud_price_points` só tem Azure e OCI.

E o câmbio:

```json
"fx": { "par": "BRL/USD", "taxa": "5.70", "data": null, "fonte": "config" }
```

`data: null`. Não sabemos de quando é a taxa. Metade das linhas do bill GCP está
em BRL.

Substituir o número do agente por um número nosso mais velho é trocar ruído por
viés. **A autoridade não pode ser por campo — tem que ser por tipo de
afirmação.**

---

## 2. A estratégia: separação epistêmica

Toda afirmação da análise cai em uma de três categorias. Quem manda muda em cada
uma.

### Categoria 1 — DERIVAÇÃO
*Segue necessariamente dos dados de entrada. Existe uma resposta certa.*

Soma, subtração, percentual, agregação de pool, delta entre provedores,
conversão de moeda dada uma taxa.

> **Backend, sempre. O agente nunca faz conta.**

Aqui não há discussão: a reconciliação já garante que a conta fecha, e um
resultado que muda entre duas execuções é defeito, não opinião.

### Categoria 2 — ESTIMATIVA
*Depende do mundo externo e envelhece. Existe resposta certa, mas talvez não a
tenhamos.*

Preço de um `n2-standard-8` hoje. Câmbio BRL/USD. Se uma família de instância
ainda é vendida.

> **Backend propõe, o agente contesta, ninguém sobrescreve em silêncio.**

É aqui que estava o erro. O backend é a fonte — porque precisa ser reprodutível —
mas passa a **declarar o frescor e a origem** de cada número, e o agente ganha um
canal formal para dizer *"esse preço parece defasado"*. A contestação não muda o
número; muda a **confiança** e vira tarefa de sync.

### Categoria 3 — JULGAMENTO
*Não há fato da questão.*

Risco de migração, complexidade, sequência recomendada, se a empresa absorve a
mudança, o que priorizar.

> **Agente, sempre. O backend nunca finge que sabe.**

É por isso que `technical_score` e `enterprise_score` não podem virar número
determinístico: não temos entrada. Um número inventado pelo backend é pior que um
do agente — tem cara de medida.

---

## 3. Onde cada campo cai

| categoria | campos | quem manda |
|---|---|---|
| **Derivação** | `estimated_monthly_cost`, `estimated_monthly_savings`, `percentage_reduction`, `cost_delta`, `total_monthly_savings_usd`, `recommended_provider`, contagem de instâncias | backend |
| **Estimativa** | preço unitário de cada SKU, taxa de câmbio, disponibilidade de família | backend propõe + agente contesta |
| **Julgamento** | `recommendation`, `recommendation_rationale`, `strengths`, `weaknesses`, `migration_complexity`, `portability`, `migration_risk`, `strategic_notes`, `action_plan`, os 3 `agent_views` | agente |
| **Composto** | `viability_score`, `analysis_confidence` | backend calcula de insumos medidos, com fórmula explícita em `premissas` |
| **Sem base** | `payback_period_months` | ver §5 |

`cross_cloud_equivalents` é misto e por isso é o caso mais interessante: a
**estrutura** (qual serviço equivale a qual, quantas instâncias, qual custo) é
derivação e vem do backend; o **julgamento** sobre aquele par (risco, precisa
refatorar, lock-in) fica com o agente.

---

## 4. Os quatro mecanismos novos

### 4.1 Proveniência e frescor em todo número de estimativa

`cloud_price_points` **já tem `captured_at`** no banco — mas
`domain/entities/cloud_price_point.py` para em `confidence` e não carrega o
campo. O frescor existe e se perde ao subir para o domínio.

Ligar isso faz cada equivalente passar a dizer:

```jsonc
"financeiro": {
  "custo_mensal_estimado": 236.52,
  "delta_custo": -539.63,
  "confianca_preco": "low",
  "preco_origem": "seed_catalog_pareto",     // ← novo
  "preco_capturado_em": "2026-05-30",        // ← novo
  "preco_idade_dias": 68                     // ← novo
}
```

Sem idade, `"official"` não quer dizer nada: pode ser de ontem ou de três meses.

Idem para o câmbio — `data: null` é bug. A premissa passa a carregar a data ou a
admitir que a taxa é fixa de config e não tem data.

### 4.2 Sugestões do agente — contestação com saída em tela

O agente ganha **um canal de saída novo**: uma lista de sugestões que o backend
persiste, usa para ajustar confiança, e **devolve para a UI exibir**.

Não é só contestação de preço. Contestação é **um tipo** de sugestão; a mesma
lista carrega oportunidade e risco. Um só contrato, uma só label na tela.

```jsonc
"sugestoes": [
  {
    "tipo": "contestacao_preco",
    "severidade": "alta",
    "titulo": "Preço de GCP possivelmente defasado",
    "detalhe": "O valor de n2-standard-8 está ~40% abaixo do preço de tabela que conheço.",
    "referencia": "GCP/n2-standard-8/us-central1",
    "efeito_backend": "confianca_rebaixada"      // ← preenchido pelo backend
  },
  {
    "tipo": "oportunidade",
    "severidade": "media",
    "titulo": "Committed use discount não considerado",
    "detalhe": "Workloads estáveis há 6 meses podem render 30-40% em contrato de 1 ano.",
    "referencia": null,
    "efeito_backend": "informativo"
  },
  {
    "tipo": "risco",
    "severidade": "baixa",
    "titulo": "Pool E2 sem equivalente cotado",
    "detalhe": "19 projetos nesse pool não tiveram alvo em OCI.",
    "referencia": "pool:E2",
    "efeito_backend": "informativo"
  }
]
```

**Tipos** — enum fechado, para a UI poder dar ícone e cor a cada um:

| tipo | significa | efeito no backend |
|---|---|---|
| `contestacao_preco` | o agente duvida de um preço | rebaixa confiança + tarefa de sync |
| `contestacao_equivalencia` | duvida do match cross-cloud | rebaixa confiança do pool |
| `oportunidade` | ganho que o backend não modela (desconto, rightsizing) | informativo |
| `risco` | algo que merece atenção antes de migrar | informativo |
| `dado_faltante` | falta insumo para concluir | informativo |

**Severidade:** `alta` \| `media` \| `baixa`. Só `alta` em contestação rebaixa
confiança — as demais são informativas.

#### O que o backend faz com cada contestação

1. **não troca o número** — trocaria reprodutibilidade por palpite;
2. **rebaixa a confiança** do resultado afetado (se severidade `alta`);
3. **preenche `efeito_backend`** para a tela poder dizer o que aconteceu;
4. **persiste** junto da análise, para virar fila de sync.

A IA vira **sinal**, não fonte. Ganhamos a percepção dela sobre defasagem sem
perder determinismo — e o usuário **vê** essa percepção em vez de ela morrer num
log.

#### Contrato para a tela

`GET /v1/agent/analyses/{report_id}/result` devolve `sugestoes[]` junto do resto.
A UI renderiza como label de sugestões, agrupando por severidade.

Regras que a tela pode assumir:

- a lista pode ser vazia (agente não teve o que dizer) — nunca `null`;
- `titulo` é curto, cabe em uma linha; `detalhe` é o parágrafo;
- `referencia` é opcional e serve para *deep link* (pool, SKU, provedor);
- `efeito_backend` nunca vem do agente — é sempre o backend que preenche.

O último item importa: sem isso, o agente poderia afirmar que "a confiança foi
rebaixada" quando não foi.

### 4.3 Confiança propagada (degradação automática)

Hoje `confianca_preco: low` vai no payload e ninguém faz nada. Passa a existir
regra computável:

```
razao_confiavel = custo_com_preco_oficial / custo_total_cotado

>= 0.90  → analysis_confidence = "high"
>= 0.60  → "medium"
 < 0.60  → "low"
```

No bill AWS de hoje a razão é **0,58** → `low`. Nenhuma narrativa pode declarar
alta confiança sobre isso, e o número deixa de ser opinião do modelo.

Contestação de severidade alta rebaixa um nível.

### 4.4 Autoridade por categoria, não por campo

`apply_backend_authority` atual sobrescreve uma lista fixa de campos. A versão
nova classifica: derivação sobrescreve sempre; estimativa preserva o valor do
backend e agrega proveniência; julgamento passa intocado.

Vantagem prática: campo novo no schema não exige mexer na autoridade — ele nasce
classificado.

---

## 5. `payback_period_months` — o caso que merece nuance

Não temos custo de migração modelado, então não é derivação. Mas o CFO quer
payback, e responder `null` é honesto e inútil.

A saída que equilibra: **o agente pode estimar, desde que declare a premissa.**

```jsonc
"payback": {
  "meses": 7,
  "tipo": "estimativa_do_agente",
  "premissa_custo_migracao_usd": 18000,
  "base": "esforço de engenharia estimado para 12 workloads"
}
```

O número fica claramente marcado como julgamento, com a premissa exposta e
contestável. O que **não** pode é `payback_period_months: 4` num campo ao lado de
valores calculados, indistinguível deles. Hoje é assim.

Este é o padrão para qualquer coisa que o backend não sabe: não proibir, **exigir
a premissa e marcar o tipo**.

---

## 6. Blocos de implementação

Ordem escolhida para que cada bloco valha por si, mesmo se o seguinte atrasar.

### Bloco 0 — sync de preços AWS e GCP · *fora do código*
São os 42% do bill AWS. Nenhuma arquitetura de autoridade compensa cotar
$34 mil/mês com catálogo mock. **Maior ganho de acurácia por esforço em todo o
plano** e não depende de nada aqui.

### Bloco A — trava do legado
Suíte de caracterização que fixa o comportamento atual de `POST /analyses/start`,
`GET /{job_id}/result` e `run_analysis_pipeline` com LLM mockado. Não julga se é
bom; registra qual é. Sem isso, os blocos seguintes são apostas.

### Bloco B — proveniência e frescor (§4.1)
`CloudPricePoint` passa a carregar `captured_at` e `source`; o payload passa a
expor origem e idade por equivalente; a premissa de FX ganha data.
**Aditivo** — o payload só cresce, ninguém quebra.

### Bloco C — confiança propagada (§4.3)
Função pura: razão de custo confiável → nível. Entra no payload e no
`analysis_confidence`. Testável sem banco e sem LLM.

### Bloco D — autoridade por categoria (§4.4)
Módulo novo `domain/services/authority.py`, puro, ao lado do
`backend_authority.py` atual, que **continua servindo o legado sem alteração**.

### Bloco E — recepção, sugestões e saída em tela (§4.2)
`POST /v1/agent/analyses/{report_id}/result`, atrás da flag. Recebe a análise do
agente, aplica autoridade por categoria, registra as **sugestões**, persiste
bruto e corrigido lado a lado.

`GET /v1/agent/analyses/{report_id}/result` devolve o corrigido **com
`sugestoes[]`** para a UI exibir na label de sugestões.

Exige uma 3ª tool no MCP (`submit_billing_analysis`) — especificada em
`MCP.PLAN/08-CONTESTACAO-E-SUGESTOES.md`.

### Bloco F — aditivo de contrato
`GET /v1/billing-files` passa a devolver **também** `report_id` (mesmo valor de
`job_id`), matando a armadilha de nomenclatura documentada para o time do MCP.

### Bloco G — o interruptor
Flag `agent_flow_enabled`, default `false`. Ligar **não desliga o legado**: os
dois convivem. Reverter é mudar a flag e reiniciar — sem migration, sem deploy.

| # | bloco | muda produção? | reversível |
|---|---|---|---|
| 0 | sync de preços | **sim, para melhor** | rodar de novo |
| A | trava do legado | não | — |
| B | proveniência | payload cresce (aditivo) | deploy |
| C | confiança propagada | `analysis_confidence` fica honesto | deploy |
| D | autoridade por categoria | não (não usada ainda) | — |
| E | recepção + contestação | não (flag off) | flag |
| F | `report_id` aditivo | payload cresce | deploy |
| G | interruptor | não | flag |

---

## 7. Garantias de que o legado não é tocado

1. **Trava primeiro** (Bloco A) — regressão vira teste vermelho, não incidente.
2. **Arquivos novos, não editados.** `analysis.py` (785 linhas) e
   `run_analysis_pipeline.py` não são refatorados nem "melhorados de passagem".
   Os existentes recebem no máximo uma linha de registro no router.
3. **Prefixo separado** `/v1/agent/*` — quem consome o legado não vê diferença,
   nem no OpenAPI.

Exceção declarada: os Blocos B, C e F **alteram o payload** que o legado também
usa — mas só acrescentando campo e corrigindo um valor de confiança que hoje é
opinião do modelo. Se isso for inaceitável, os três ficam atrás da mesma flag; o
custo é o legado continuar com `analysis_confidence` sem base.

---

## 8. Por que esta estratégia é melhor que qualquer um dos dois sozinho

**Backend sozinho:** exato na conta, cego para o mundo. Cota $34 mil/mês com
preço mock e não desconfia.

**Agente sozinho:** conhece o mercado, mas inventa número sem saber que está
inventando, e não repete o mesmo resultado duas vezes.

**Juntos, com papéis separados por tipo de afirmação:** o backend garante que a
conta fecha e que o número é reprodutível; o agente aponta onde o insumo cheira
mal e cuida do que é julgamento. Nenhum dos dois opina fora do que sabe.

O ganho concreto: hoje uma análise do bill AWS sai com aparência de precisão
sobre 42% de preço mock. Depois destes blocos, o mesmo caso sai com
`analysis_confidence: low`, cada equivalente dizendo de onde veio o preço e há
quantos dias, e a IA sinalizando quais números não confia — **sem que nenhum
valor tenha deixado de ser reprodutível.**


---

# 9. O que falta para o fluxo novo funcionar

Os oito blocos estão prontos e mergeados em `develop`. O fluxo MCP+Agente **não
funciona ponta a ponta** por três motivos, nenhum deles neste repositório.

## 9.1 O MCP precisa implementar as três tools · **bloqueante**

O pacote está pronto em `../MCP.PLAN/` — código TypeScript compilado e testado
contra um clone real do `ost-mcp.nodejs`, contrato da API campo a campo, deploy
e testes. **Ainda não foi entregue ao responsável.**

| tool | o que faz |
|---|---|
| `list_billing_reports` | descobre os relatórios do usuário |
| `get_billing_request` | busca a análise pronta |
| `submit_billing_analysis` | devolve a análise para o backend corrigir |

A terceira é a que fecha o circuito com o `POST /v1/agent/analyses/{id}/result`
deste repo. Especificada em `MCP.PLAN/08-CONTESTACAO-E-SUGESTOES.md`.

O MCP hoje é só um servidor de prompts: `grep -rn "fetch|axios" src/` não retorna
nada. Não é ajuste — é capacidade nova.

## 9.2 O cliente MCP precisa mandar a identidade do usuário

Toda query da API isola por `user_id` do JWT, e o MCP não é um usuário. A saída
é repassar o JWT do usuário final no header `X-OST-User-Token` — implementado no
pacote, mas exige mudança em **quem hospeda o agente**, possivelmente outro time.

Sem isso as tools respondem `SEM_CREDENCIAL` e nada funciona.

## 9.3 Ligar o interruptor

`AGENT_FLOW_ENABLED=true` no ambiente. Hoje `false` em dev e produção — as rotas
`/v1/agent/*` respondem 404 como se não existissem.

**Ligar não desliga o legado.** A virada real é o frontend parar de chamar
`POST /v1/analyses/start`.

---

# 10. Dívida conhecida

## 10.1 `exchange_rates` está vazia

Não existe seed para a tabela (o repositório só tem `get_latest`). O câmbio vem
do fallback `fx_brl_usd` no env — hoje `5.70`, sem data, que ninguém atualiza.

Metade das linhas do bill GCP está em BRL. Se a variável sumir do ambiente, a
conversão vira identidade e a análise compara real com dólar **sem avisar** — o
que é pior que falhar, porque o número sai plausível.

Conferido em 2026-08-11: `fx_brl_usd = 5.70` presente em dev.

## 10.2 A env da key do GCP não persiste no cluster

`GCP_BILLING_API_KEY` está no `.env.dev` local e o sync já rodou contra o banco
de dev (24 price points de GCP oficiais). Mas o `api-secret` do namespace
`develop` é gerenciado por Flux — alteração via `kubectl` é revertida no próximo
reconcile.

**Não bloqueia nada:** o sync é offline, o pod não precisa da key em runtime, a
análise só *lê* `cloud_price_points`. Só importa para rodar o sync de dentro do
pod. A correção definitiva é um PR no `ost-infra`.

## 10.3 Build e testes rodam em paralelo no CI

`Build, Push & Deploy` não espera `Tests`. Imagem quebrada sobe mesmo com teste
vermelho. A correção é um `needs:` no job de build, mas o workflow é mantido por
outra pessoa — combinar antes.

## 10.4 Preço só de uma região por provedor

Os fetchers coletam `us-east-1` (AWS), `eastus` (Azure), `us-central1` (GCP). Um
bill com carga em São Paulo é cotado com preço da Virgínia, que é mais barato —
a economia estimada sai otimista. Corrigir exige mapear a região do bill para a
do provedor-alvo.
