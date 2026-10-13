# Ciclo de vida — o que o front chama, em ordem

Extraído do OpenAPI da API, não de memória. Base: `https://api-dev.oneshift.com.br`.
Todas as rotas exigem `Authorization: Bearer <jwt do Supabase>`.

---

## A tabela

| # | passo | método e rota | o que o front envia | o que volta | quando avançar |
|---|---|---|---|---|---|
| 1 | **Subir a fatura** | `POST /v1/billing-files` | `multipart/form-data` com `file`<br>opcional: `?external_id=<uuid>` | `202` + `job_id` | imediatamente |
| 2 | **Aguardar a ingestão** | `GET /v1/billing-files/{job_id}` | — | `status` | polling até `done` |
| 3 | *(opcional)* **Drill-down** | `GET /v1/billing-files/{job_id}/items` | `?limit=50&offset=0` | linhas, `cost DESC` | — |
| 4 | **Disparar a análise** | `POST /v1/analyses/start/{report_id}` | nada no corpo<br>opcional: `?external_id=<uuid>` | `202` + `job_id` da análise | imediatamente |
| 5 | **Aguardar a análise** | `GET /v1/analyses/{job_id}/result` | — | `status` + `result` | polling até `COMPLETED` |
| 5b | **Tela de acoplamento** | `GET /v1/analyses/{job_id}/service-couplings` | — | uma linha por serviço, `resumo` com os contadores | depois de `COMPLETED` |
| 6 | **Listar análises** | `GET /v1/analyses` | `?limit=50`<br>`?incluir_depreciadas=true` só na lixeira | array de `AnalysisJobSummary` | — |
| 7 | **"Excluir" análise** | `PUT /v1/analyses/{job_id}/disp-status` | `{"disp_status": 0}` | `200` | tirar o card da lista |
| 7b | **Desfazer** | `PUT /v1/analyses/{job_id}/disp-status` | `{"disp_status": 1}` | `200` | recarregar a lista |
| 8 | **Gerar Terraform** | `POST /v1/terraform-generations` | ver §3 | `202` + `generation_id` | imediatamente |
| 9 | **Aguardar o pacote** | `GET /v1/terraform-generations/{id}` | — | `status` + `assumptions` | polling até `COMPLETED` |
| 10 | **Baixar o ZIP** | `GET /v1/terraform-generations/{id}/download` | — | `application/zip` | — |

> **Atenção ao `id` de cada passo.** O passo 1 devolve um `job_id` **de
> ingestão**, que é o `report_id` usado no passo 4. O passo 4 devolve um `job_id`
> **de análise**, que é o `analysis_job_id` do passo 8. São coisas diferentes.

---

## 1. Valores de `status`, por recurso

| recurso | onde | valores | terminais |
|---|---|---|---|
| Ingestão | passos 2 e 3 | `queued` · `processing` · `done` · `failed` | `done`, `failed` |
| Análise | passos 5 e 6 | `PENDING` · `PROCESSING` · `COMPLETED` · `FAILED` | `COMPLETED`, `FAILED` |
| Terraform | passos 9 e 10 | `PENDING` · `PROCESSING` · `COMPLETED` · `FAILED` | `COMPLETED`, `FAILED` |

Ingestão em **minúsculas**, análise e Terraform em **MAIÚSCULAS**. É assim na
API; não normalize antes de comparar.

---

## 2. `disp_status` — a exclusão

| valor | significado | efeito |
|---|---|---|
| `1` | ativa | aparece no `GET /v1/analyses` |
| `0` | depreciada | some da listagem; apagada do banco após 30 dias |

**Campo único.** O corpo é `extra="forbid"`: qualquer outra chave devolve `422`.
Idempotente — mandar `0` duas vezes devolve `200` nas duas.

Depois do `PUT` com `0`, **o front não precisa filtrar nada**: a análise já não
volta no `GET /v1/analyses`.

Cada item da listagem traz `disp_status`, útil só na tela de lixeira.

---

## 3. Corpo do `POST /v1/terraform-generations`

```json
{
  "analysis_job_id": "62d67cd7-2b80-4e61-9e72-2f17505d262c",
  "project_name": "migracao-empresa-x",
  "environment": "dev",
  "target_provider": "gcp",
  "region": "southamerica-east1",
  "network":      { "mode": "create", "cidr": "10.20.0.0/16", "public_access": false },
  "availability": { "high_availability": false },
  "tags":         { "managed_by": "oneshift" }
}
```

| campo | obrigatório | valores aceitos | default |
|---|---|---|---|
| `analysis_job_id` | **sim** | uuid de uma análise `COMPLETED` | — |
| `project_name` | **sim** | 3–40 chars, minúsculas, dígitos, `-` ou `_`, começa com letra | — |
| `target_provider` | **sim** | `gcp` · `aws` · `azure` · `oci` | — |
| `region` | **sim** | região do provedor, ex. `southamerica-east1` | — |
| `environment` | não | `dev` · `staging` · `prod` | `dev` |
| `network.mode` | não | `create` · `existing` | `create` |
| `network.cidr` | não | IPv4, prefixo **/24 ou maior**, sem bits de host | `10.0.0.0/16` |
| `network.public_access` | não | boolean | `false` |
| `availability.high_availability` | não | boolean | `false` |
| `tags` | não | objeto; chaves em `snake_case` | `{}` |

**Sobre o `cidr`:** tem que ser o endereço da rede, não de um host —
`10.20.0.1/16` é recusado, use `10.20.0.0/16`. E `/25` ou menor é recusado
porque não sobra espaço para dividir em subnets.

**Formatos aceitos no upload (passo 1):** `.csv` · `.tsv` · `.txt` · `.json` ·
`.ndjson` · `.xlsx` · `.xlsm` · `.gz` · `.zip`. Tamanho máximo definido por
`MAX_UPLOAD_SIZE_MB` (50 MB por padrão).

O front **não** envia billing, custo, shape de máquina nem credencial de nuvem.
Tudo isso o backend já tem — ou, no caso de credencial, nunca deve ter.

---

## 4. O que exibir da resposta do Terraform

O `GET /v1/terraform-generations/{id}` devolve três listas que **precisam
aparecer na tela**. Sem elas o cliente lê um `.tf` estimado como se fosse exato.

| campo | o que é | como exibir |
|---|---|---|
| `assumptions[]` | cada valor do pacote com a origem | tabela; destacar os `estimativa` e `sem_base` |
| `unsupported_items[]` | custo que **não** virou recurso (egresso, reserva, Kubernetes) | aviso — o pacote não cobre esse gasto |
| `warnings[]` | limites atingidos, ex. disco acima do máximo do provedor | aviso |
| `validation` | `{formatado, validado, erros[]}` | selo de "formatado" |
| `available_files[]` | nomes dentro do ZIP | lista antes do download |

Cada `assumption` tem `categoria`:

| categoria | rótulo sugerido | significado |
|---|---|---|
| `derivacao` | **Calculado** | sai da conta; existe uma resposta certa |
| `estimativa` | **Estimado** | sai de uma premissa declarada — conferir |
| `sem_base` | **Não deduzível** | virou variável em branco no `.tf` |
| `escolha_usuario` | **Informado por você** | veio do formulário |

---

## 5. Erros

Todas as rotas novas usam o mesmo envelope:

```json
{ "detail": { "codigo": "ANALISE_NAO_ENCONTRADA", "mensagem": "...", "retryable": false } }
```

**`retryable` é o que decide se o front tenta de novo.**

| HTTP | `codigo` | quando | front faz |
|---|---|---|---|
| `404` | `ANALISE_NAO_ENCONTRADA` | não existe **ou** é de outro usuário | mensagem; não repetir |
| `404` | `REPORT_NAO_ENCONTRADO` | idem, para relatório | mensagem; não repetir |
| `404` | `GERACAO_TERRAFORM_DESLIGADA` | feature flag desligada no ambiente | esconder o botão |
| `409` | `REPORT_NAO_PRONTO` | ingestão em andamento | **repetir** depois |
| `409` | `ANALISE_NAO_CONCLUIDA` | análise ainda rodando | **repetir** depois |
| `409` | `GERACAO_NAO_CONCLUIDA` | download antes de terminar | **repetir** depois |
| `409` | `PAYLOAD_AUSENTE` | análise sem payload gravado | refazer a análise |
| `409` | `ANALISE_SEM_SERVICOS` | acoplamento de análise antiga, sem a lista de serviços | manter a tela antiga; não repetir |
| `422` | — | corpo inválido (formato Pydantic padrão) | corrigir o formulário |

`404` para recurso de outro usuário é deliberado: um `403` confirmaria que ele
existe.

---

## 6. Flags de ambiente

Duas superfícies respondem `404` quando desligadas, **como se não existissem**:

| flag | afeta |
|---|---|
| `TERRAFORM_GENERATION_ENABLED` | passos 8, 9 e 10 |
| `AGENT_FLOW_ENABLED` | `/v1/agent/*` — o front não usa |

Se o `POST /v1/terraform-generations` responder `404` com
`GERACAO_TERRAFORM_DESLIGADA`, não é erro do front: a feature está desligada
naquele ambiente.

---

## 7. Polling

Não há webhook nem websocket. Os três passos assíncronos (2, 5, 9) são polling.

Sugestão: 2s no início, dobrando até 10s, desistindo em 10 minutos. A análise
chama um LLM (timeout de 300s) e a geração de Terraform costuma levar poucos
segundos.

Um job que morre por reinício do processo é encerrado como `FAILED` pelo reaper
do startup — então o polling não fica eterno esperando um `COMPLETED` que não vem.

---

## 8. O que mudou para a tela em 13/10/2026

**Acoplamento dos serviços (passo 5b).** A tela passa a ler
`GET /v1/analyses/{job_id}/service-couplings` em vez de montar a lista a partir
de `cross_cloud_equivalents`. Contrato completo em
[`endpoints/service-couplings.md`](endpoints/service-couplings.md). O essencial:

- contadores de `resumo` (`low`, `medium`, `high`, `a_confirmar`), nunca da
  contagem de linhas da tabela de equivalência;
- "Do total" = `fracao`; a soma fica ≤ 100%, o resto é `reconciliacao.fora_do_escopo`;
- badge = `nivel`; com `nivel` nulo e `nivel_sugerido` presente, "A confirmar ·
  provável <nível>", com o `motivo` de `sugestoes[]` no detalhe;
- pools de compute dentro da linha de máquinas virtuais (`pools[]`), com
  `regiao: null` = "região não informada";
- `entra_no_score: false` = gasto sem categoria identificada;
- com inventário, `dependencias`: "migra junto com …", custo do grupo, "depois
  de …", onda, e a exclusividade pelo motor do banco.

Some da tela: a linha "Bancos de dados (agregado)" e as repetições por destino.

**Preço por região (campos novos e opcionais no resultado).** Em cada
equivalente de pool: `preco_regiao` (onde o preço vale), `preco_regiao_aproximada`
(não havia preço da região; o rótulo cai para `reference`) e
`arquitetura_diferente` (só havia a outra arquitetura no destino). Em
`premissas.regioes`, onde cada pool foi cotado. Dois avisos novos com
`origem: "backend"`: "Região de origem não identificada" e "Preço de outra
região na cotação". Pools de bill agregado passam a ter região
(`E2/southamerica-east1`); `americas` é grupo de preço do GCP, não região.

**Payback.** Continua fora da tela. Só o envio do agente traz `payback`, agora
com `meses` calculado pelo backend (`calculado_pelo_backend: true`).
