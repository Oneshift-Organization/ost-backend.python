# Product

Contexto de produto do **Cloud Intelligence Platform** — plataforma que extrai
billing multi-cloud (AWS · GCP · Azure · OCI), cota infraestrutura equivalente
em cada provedor, calcula um score de portabilidade e gera recomendações de
migração para CFOs/controllers.

> **Audiência deste doc:** dev novo no time, agente de IA que precisa entender
> o "para quê" antes de tocar código. Para regras técnicas, ver
> [`architecture.md`](architecture.md). Para contratos de API,
> [`api-conventions.md`](api-conventions.md).

## Origem

Challenge **FIAP 2026** proposto pela **Quave** (boutique de software, criadora
do Quave ONE PaaS cloud-agnostic) em parceria com **Oracle Cloud Infrastructure
(OCI)**. Apresentação final: **NEXT FIAP Festival — 10/out/2026**.

A premissa do produto vem de 15 anos de observação de campo da Quave:

| Dor real do mercado | Implicação técnica |
|---|---|
| "Estou preso no provedor onde comecei." | Score de portabilidade precisa quantificar lock-in. |
| "Hyperscalers são complexos demais." | UX para **não-engenheiros** (CFO de empresa de 200 pessoas em 5min). |
| "Cloud gerenciada não escala." | Cotação cross-cloud precisa mostrar onde o salto vale a pena. |
| "Não quero acordar de madrugada." | Recomendações narradas — CFO precisa decidir, não interpretar. |

Validação dos números (2025–2026):

- 72% das empresas estouraram o budget de cloud em 2025.
- 32% do orçamento de cloud é desperdiçado em média.
- US$ 44,5B desperdiçados globalmente em recursos ociosos.
- Gasto global com cloud > US$ 1 trilhão em 2026.

## Usuários-alvo

- **Primário:** CFOs, controllers, analistas financeiros — **não engenheiros**.
- **Secundário:** DevOps / FinOps que precisam justificar decisões de infra para
  o board.
- **Pré-requisito de validação:** empresa com billing **≥ US$ 5.000/mês** para
  uso com dados reais.

> **Regra de UX:** se um CFO de empresa de 200 pessoas não usar a plataforma em
> 5min, o design precisa ser refeito. **SaaS, não trabalho acadêmico.**

## Os 5 módulos

Cada módulo é uma fatia de produto independente, com responsabilidade clara.
A regra é: módulos comunicam por **resultados calculados** persistidos no banco
— nunca por chamada direta de função entre eles fora do orquestrador.

### Módulo 1 — Extração de Billing (Fase 1, MVP)

**Status:** ✅ implementado para upload manual; coleta via API diferida.

**Objetivo:** receber arquivo de billing de qualquer provedor, detectar formato
pelo conteúdo (não extensão), normalizar para schema canônico, persistir.

- Entradas: upload manual (CSV/JSON/.gz/.zip/.xlsx). API direta (pós-MVP).
- Saída: tabela `billing_items` normalizada por `user_id` + `billing_report`.
- Detalhes técnicos: [`parsers.md`](parsers.md), [`data-model.md`](data-model.md).
- Contrato HTTP: [`endpoints/billing.md`](endpoints/billing.md).

### Módulo 2 — Cotação Cross-Cloud (Fase 2)

**Status:** ⏳ próximo após F1G dashboard.

**Objetivo:** com o billing extraído, cotar **quanto custaria a mesma infra
(as-is) em cada um dos outros big clouds**. Não é multi-cloud simultâneo —
é descobrir **para onde vale migrar**.

- Entrada: `billing_items` consolidados (Módulo 1).
- Saída: `cross_cloud_quotes: dict[provider, Decimal]` + economia % e absoluta.
- Exemplo: empresa hoje na Azure → "AWS −12% · GCP −25% · **OCI −40%**".
- Requer: tabela de-para serviço equivalente entre provedores + APIs de preço.

### Módulo 3 — Score de Portabilidade (Fase 3)

**Status:** ⏳ planejado para 06/jul/2026.

**Objetivo:** quantificar (0–100) o quão fácil é sair do provedor atual.

| Fator | Impacto no score |
|---|---|
| Serviços proprietários sem equivalente (DynamoDB, Cloud Run) | reduz |
| Kubernetes / containers padronizados | aumenta |
| Complexidade de migração (downtime, refactor) | reduz |
| Custo-benefício (esforço de migração × benefício) | filtra |

> Score alto = portável e independente. Score baixo = preso ao provedor atual.

### Módulo 4 — Automação de Migrações (Fase 4)

**Status:** ⏳ planejado para 31/ago/2026.

**Objetivo:** receitas prontas e reutilizáveis para migrar workloads portáveis
entre big clouds. Não são scripts caso-a-caso.

Fluxo: **Identificar** componentes portáveis → **rodar receita pronta**
(K8s, VMs, storage) → **executar com validação a cada etapa**.

### Módulo 5 — IA de Insights (Fase 5, "O Cérebro")

**Status:** ⏳ planejado para 28/set/2026.

**Filosofia inegociável: regras calculam, LLM narra.**

A IA **nunca faz matemática**. Recebe resultados já calculados pelos módulos
1–4 e gera narrativa em linguagem natural para o CFO.

Tipos de recomendação:

| Tipo | Critério |
|---|---|
| `MIGRATE_NOW` | savings > 30% **e** breakeven < 6m **e** portability > 70 **e** confidence > 0.8 |
| `MIGRATE_PLANNED` | savings > 15% **e** breakeven < 12m **e** portability > 50 |
| `OPTIMIZE_FIRST` | idle_resources > 20% do custo total |
| `PARTIAL_MIGRATION` | portability misto — alguns workloads portáveis, outros não |
| `STAY_AND_MONITOR` | savings < 15% **ou** confidence < 0.5 |
| `INSUFFICIENT_DATA` | < 3 meses de histórico |

## Cronograma — Challenge FIAP 2026

Todas as entregas caem em segunda-feira (dia de aula).

| Fase | Entrega | Data |
|---|---|---|
| **F1 + F2** | Fundação + Core Engine — extração + cotação cross-cloud | **25/mai/2026** |
| F3 | Portabilidade — score com dados reais | 06/jul/2026 |
| F4 | Automação — receitas para infra portável | 31/ago/2026 |
| F5 | IA & Polish — O Cérebro + refinamento UX | 28/set/2026 |
| F6 | Apresentação & Cases — empresas reais | 10/out/2026 |
| **Final** | **NEXT FIAP Festival** | **10/out/2026** |

## Decisões consolidadas

Estas são as decisões de produto/arquitetura que **não voltam à mesa** sem
escalation. Para racionais técnicos detalhados, ver
[`architecture.md`](architecture.md).

| Decisão | Escolha | Razão curta |
|---|---|---|
| Valores monetários (Python) | `Decimal(str(valor))` | float IEEE 754 acumula erro |
| Valores monetários (PG) | `NUMERIC(18,6)` | precisão ponta a ponta |
| Validação de entrada | Pydantic v2 | nativo FastAPI |
| Validação de DataFrame | Pandera | schema declarativo |
| Async | FastAPI `BackgroundTasks` | sem cold start, sem fila no MVP |
| Lambda | adiada | cold start + IA = latência imprevisível |
| IA | híbrido (regras + LLM) | cálculo determinístico + narrativa fluida |
| Infra base | OCI | parceiro do challenge, custo/benefício competitivo |
| Mensageria pesada (Celery+RabbitMQ) | diferida pós-MVP | parsing CSV típico < 30s |
| Multi-tenancy | só `user_id` no MVP | `tenant_id` (org) adiado |

## Restrições inegociáveis

Estas valem para qualquer PR, qualquer fase, qualquer agente.

1. **Nunca `float` para valores monetários** em nenhuma camada.
2. **Nunca invocar LLM para calcular** — só para narrar resultados prontos.
3. **Único requisito obrigatório da Quave:** conexão via API com AWS/GCP/Azure/OCI
   para extrair billing.
4. **Validar com empresa real** — billing mínimo US$ 5.000/mês; sem mockups na
   entrega final.
5. **UX para não-engenheiros** — CFO sem treinamento em < 5min.
6. **IA isolada atrás de interface `analyze()`** — implementação trocável.
7. **Toda nova cloud** = parser isolado em
   [`app/infrastructure/file_ingestion/parsers/`](../app/infrastructure/file_ingestion/parsers/)
   + entrada em `detector.PARSERS`.
8. **Jobs sempre têm status rastreável** — nunca fire-and-forget.
9. **Respostas da API nunca bloqueiam** — > 200ms vai para background.
10. **Tratar como SaaS** — dashboards bonitos, fluxos claros, onboarding simples.

## Glossário

Termos que aparecem em código, docs e conversas. Padronizado.

| Termo | Definição |
|---|---|
| **Billing** | Arquivo de custos exportado por um provedor de cloud. |
| **CUR** | Cost and Usage Report — formato AWS de billing detalhado (CSV/Parquet). |
| **Cost Explorer** | UI da AWS que exporta CSV pivotado (≠ CUR). Ver [`parsers.md`](parsers.md#aws-cost-explorer). |
| **FOCUS** | FinOps Open Cost & Usage Specification — formato cross-cloud emergente. |
| **FinOps** | Prática de gestão financeira de infraestrutura cloud. |
| **Break-even** | Tempo até o custo de migração ser pago pela economia gerada. |
| **Score de portabilidade** | 0–100 indicando facilidade de sair do provedor atual. |
| **Idle resource** | Recurso provisionado com uso abaixo do threshold. |
| **Confidence score** | 0.0–1.0 indicando confiabilidade da recomendação. |
| **`job_id`** | UUID gerado no upload; usado para polling de status e drill-down. |
| **`report_id`** | Equivalente a `job_id`; nome interno em queries por convenção SQL. |
| **De-para** | Mapeamento de serviços equivalentes entre provedores (EC2 ⇄ Compute Engine ⇄ VM Azure ⇄ OCI Compute). |
| **Recomendação híbrida** | Regras calculam números; LLM gera narrativa. |
| **Lock-in** | Dependência de serviços proprietários que dificultam mudança. |
| **OCI** | Oracle Cloud Infrastructure — parceiro do challenge, infra base sugerida. |
| **Quave ONE** | PaaS cloud-agnostic da Quave; inspiração do produto. |
| **`global_zero`** | Marcador em `raw_payload.scope` que indica "serviço observado, mas com custo zero em todo o export" (específico do parser Cost Explorer — ver [`parsers.md`](parsers.md#política-de-emissão-de-cost0-cost-explorer)). |
