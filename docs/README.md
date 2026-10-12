# ost-backend docs

Documentação **versionada** do backend Python. Fonte de verdade pública —
acompanha o código em cada PR. Otimizada para leitura por humanos **e** por
agentes de IA.

> **Camadas de doc do projeto (ordem de prioridade ao buscar resposta):**
>
> 1. **`docs/` (aqui)** — versionado, evolui com o código. **Comece por aqui.**
> 2. **[`CLAUDE.md`](../CLAUDE.md)** (raiz do repo) — orientação rápida para
>    Claude Code; links pros docs abaixo.
> 3. **`ost-docs.backend.plan/`** (raiz, **gitignored**) — brain local-only do
>    arquiteto. Plano vivo, pode estar à frente em decisões em discussão.
>    Consultar, **não editar daqui**.
> 4. **`ost-docs.mkdocs/`** (repo separado, fora deste) — docs corporativos
>    read-only (PRD, TRD). **Não editar daqui**.
> 5. **Prompts foundation** (raiz do workspace) —
>    [`cloud_billing_analyzer_prompt.md`](../../cloud_billing_analyzer_prompt.md)
>    + [`fase1_parser_prompt.md`](../../fase1_parser_prompt.md): visão de
>    produto + spec do Módulo 1. Sintetizados em
>    [`product.md`](product.md) + [`parsers.md`](parsers.md).

## Índice

### Conceito + escopo

| Arquivo | Para quê |
|---|---|
| [`product.md`](product.md) | Visão de produto: 5 módulos, cronograma FIAP, glossário, decisões consolidadas, restrições inegociáveis. **Leia primeiro se você é novo no time.** |
| [`architecture.md`](architecture.md) | Clean Architecture, stack, regras inegociáveis, proibições. **Leia antes de mudar estrutura.** |

### Implementação

| Arquivo | Para quê |
|---|---|---|
| [`data-model.md`](data-model.md) | DDL das tabelas (`billing_reports`, `billing_items`, `exchange_rates`, `cloud_price_points`, inventário), schema canônico do parser, convenções de `raw_payload`, queries comuns. |
| [`parsers.md`](parsers.md) | Guia por provider (AWS CUR/CE, Azure, GCP, OCI) + política `global_zero` do Cost Explorer + como adicionar parser novo. |
| [`billing-ingestion-pipeline.md`](billing-ingestion-pipeline.md) | Pipeline completa: detecção, 7 parsers, schema canônico, edge cases (NaN, locale, correções). |
| [`capacity-pool-model.md`](capacity-pool-model.md) | Alinhamento FOCUS: taxonomia universal, buckets de capacidade, agregação por (Project, Family, Region). |
| [`sku-matching.md`](sku-matching.md) | Acurácia do SKU matching: Matching Ladder (L0–L5), `component_type`, `sku_classification_rules`, gate `sku_coverage_ratio ≥ 95%`. |
| [`cross-cloud-equivalence.md`](cross-cloud-equivalence.md) | Equivalência cross-cloud por Capacity Pool (DE-PARA): `match_pool`, `equivalents` aninhados, alinhamento FOCUS, LLM seleciona em vez de inventar. |
| [`precision-engine.md`](precision-engine.md) | **Motor de precisão — o que está construído**: o que existe hoje (FX, preços oficiais Azure/OCI, shape de instância, razão de taxa por categoria, consolidação por provedor, autoridade do backend sobre o LLM, camada de reconciliação) e o que segue pendente. Divergências do plano original estão marcadas. |

### Operação

| Arquivo | Para quê |
|---|---|
| [`api-conventions.md`](api-conventions.md) | Convenções transversais: paginação, envelopes, serialização Decimal/datetime, status codes, ordenação. **Leia antes de adicionar rota nova.** |
| [`endpoints/billing.md`](endpoints/billing.md) | Catálogo `/v1/billing-files`: contratos request/response, status codes, exemplos copy-paste. |
| [`endpoints/reports.md`](endpoints/reports.md) | Catálogo `/v1/reports`: dashboard de billing agregado por provedor/serviço/período. |
| [`endpoints/account.md`](endpoints/account.md) | `DELETE /v1/account`: exclusão definitiva da conta e de todos os dados do usuário. |
| [`endpoints/inventories.md`](endpoints/inventories.md) | Catálogo `/v1/inventories`: export de cada nuvem (sem credencial), topologia, correlação com o billing, declarações, e o que muda na análise e no Terraform. |
| [`endpoints/agent-snapshots.md`](endpoints/agent-snapshots.md) | Caminho do agente (MCP): snapshot de leitura gravado, envio idempotente pelo conteúdo, versões, revisões e consulta. |
| [`auditoria-e2e-2026-09-27-inventario.md`](auditoria-e2e-2026-09-27-inventario.md) | Billing, inventário, análise e Terraform pela API nos 4 mocks: coerência entre os três artefatos, número a número. |
| [`dev-setup.md`](dev-setup.md) | Rodar local: venv, Postgres via podman, alembic, bypass de auth, troubleshooting. |

## Fluxo de leitura recomendado

**Dev novo no time** (em ordem):

1. [`product.md`](product.md) — entender o "para quê".
2. [`architecture.md`](architecture.md) — entender o "como" estrutural.
3. [`dev-setup.md`](dev-setup.md) — rodar local em 5min.
4. [`endpoints/billing.md`](endpoints/billing.md) — ver a API funcionando.
5. [`parsers.md`](parsers.md) e [`data-model.md`](data-model.md) — quando for
   tocar billing.

**Agente de IA** (consulta dirigida):

| Tarefa | Comece por |
|---|---|---|
| Adicionar rota nova | [`api-conventions.md`](api-conventions.md) |
| Adicionar parser de cloud | [`parsers.md`](parsers.md#como-adicionar-um-parser-novo) |
| Entender pipeline de ingestão | [`billing-ingestion-pipeline.md`](billing-ingestion-pipeline.md) |
| Acurácia do SKU matching / orphan rate | [`sku-matching.md`](sku-matching.md) |
| Alterar schema/migration | [`data-model.md`](data-model.md) |
| Entender regra de negócio | [`product.md`](product.md) |
| Debug de detecção de formato | [`parsers.md`](parsers.md#como-debugar-uma-detecção-que-falha) |
| Decisão arquitetural | [`architecture.md`](architecture.md) |
| Setup / troubleshooting | [`dev-setup.md`](dev-setup.md) |

## Regras de manutenção

- **Editar junto com o código.** Se uma PR muda contrato de rota, ela atualiza
  [`endpoints/*.md`](endpoints/) na mesma branch. Drift é o maior inimigo.
- **[`architecture.md`](architecture.md) é stable.** Mudanças exigem
  alinhamento — não adicione uma exceção pontual; ou a convenção muda para
  todos, ou o caso específico vira documentação separada com justificativa.
- **[`api-conventions.md`](api-conventions.md) é stable** pelos mesmos motivos.
- **Exemplos de payload valem ouro.** Cole respostas reais (anonimizadas) em
  vez de descrever em prosa — copy-paste é o que o leitor quer.
- **Tabelas > prosa** sempre que possível. Cada decisão tem motivo + alternativa
  rejeitada explícitos. Otimiza tanto leitura humana quanto extração por IA.
- **Não duplicar.** Se uma regra vive em
  [`architecture.md`](architecture.md), os outros docs **linkam** para ela.
  Se ela vive em [`product.md`](product.md), idem. Drift entre docs é tão ruim
  quanto drift entre doc e código.
- **Não editar `ost-docs.backend.plan/`** (gitignored, brain do arquiteto) nem
  `ost-docs.mkdocs/` (repo separado, corporativo). Só consultar.

## Convenções de escrita

Para manter docs legíveis para humanos e parseáveis por IA:

- **Headers semânticos** (`##` para seções, `###` para subseções) — IAs usam
  pra ancoragem.
- **Termos do glossário** linkam para [`product.md`](product.md#glossário) na
  primeira ocorrência por documento.
- **Refs de código** sempre relativas: `[symbol](../app/path/file.py#L42)`.
- **Decisões em tabela:** coluna "Decisão" + "Por quê" + (quando aplica)
  "Alternativa rejeitada".
- **Proibições explícitas:** lista com `❌` e motivo curto. Sem ambiguidade.
- **Português** para o documento; **inglês** apenas em código/termos técnicos
  (`endpoint`, `payload`, `commit`, `pull request`). Consistente por arquivo.
