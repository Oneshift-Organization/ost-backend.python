# Endpoints `/v1/inventories`

Inventário da infraestrutura do cliente: o que existe e o que está ligado a quê.
É daqui que saem as dependências, a ordem de migração em ondas, os riscos de
rede, a cobertura do billing e, no Terraform, as subredes, os grupos de
segurança e o posicionamento das VMs.

> Regras de negócio completas (RI1 a RI17), decisões e revisões por etapa:
> [`PLANO_INVENTARIO_API.md`](../../PLANO_INVENTARIO_API.md). Convenções gerais:
> [`api-conventions.md`](../api-conventions.md). Tabelas:
> [`data-model.md`](../data-model.md#inventário).

Toda rota exige `Authorization: Bearer <jwt>` (bypass em dev via
`AUTH_ENABLED=false`) e só existe com **`INVENTORY_ENABLED=true`**. Desligada,
toda rota responde `404 INVENTARIO_DESLIGADO`.

**A plataforma nunca recebe credencial de nuvem** (decisão D1). Quem exporta é o
cliente, com o comando oficial do provedor; a API recebe só o arquivo.

## Como o cliente exporta

| Origem | Comando | O que enviar |
|---|---|---|
| AWS | `aws configservice deliver-config-snapshot --delivery-channel-name <canal>` | Os `.json.gz` do bucket do canal, um por conta e região. Também aceita a saída de `aws configservice select-resource-config --expression "SELECT * WHERE resourceType LIKE 'AWS::%'"` (tags em lista, portas decimais e campos `{"value": …}` são lidos). Configurações que a AWS cria em toda conta (CodeDeploy, AppConfig, chaves `alias/aws/*`, frotas `instant` do autoscaling…) ficam fora e aparecem em `contagens` como `padrao_do_provedor:<tipo>`. |
| GCP | `gcloud asset export --project=<p> --content-type=resource --output-path=gs://<bucket>/inventario.json` | O NDJSON exportado (ou `gcloud asset list … --format=json`). |
| Azure | `az graph query -q "Resources \| project id, name, type, location, resourceGroup, subscriptionId, tags, properties, sku, kind, zones, managedBy" --first 1000` | As páginas em JSON. O Resource Graph não diz quando capturou: a data de captura é a do envio, marcada como estimada. |
| OCI e outras | — | O formato canônico `oneshift-inventory-v1` (ver o plano, §2). |

O formato é detectado pelo **conteúdo**, não pela extensão, como no billing.

## Fluxo típico

```
POST /v1/inventories (arquivos)                    → 202 { inventory_id, status: "queued" }
GET  /v1/inventories/{id}                          ← poll até "done" (ou "failed", com o motivo)
GET  /v1/inventories/{id}/topology                 ← unidades, ondas, riscos, ociosos
GET  /v1/inventories/{id}/correlation?report_id=   ← quanto do billing o inventário explica
PUT  /v1/inventories/{id}/declarations             ← o que o inventário não sabe (criticidade…)
POST /v1/analyses/start/{report_id} { "inventory_id": … }   ← análise com a topologia
POST /v1/terraform-generations { analysis_job_id, … }       ← pacote com rede e grupos
```

Erros seguem o envelope da API: `{"detail": {"codigo", "mensagem", "retryable"}}`.

---

## `POST /v1/inventories`

Envia um ou mais arquivos do mesmo inventário. Processa em segundo plano.

- `multipart/form-data`, campo `files` (repetido).
- Extensões: `.json`, `.ndjson`, `.jsonl`, `.gz`, `.zip`.
- Até `INVENTORY_MAX_FILES` arquivos (20) de até `MAX_UPLOAD_SIZE_MB` (50 MB) cada.

**202** `{"inventory_id", "status": "queued", "source_files", "created_at"}`

| Status | `codigo` | Quando |
|---|---|---|
| 404 | `INVENTARIO_DESLIGADO` | Flag desligada |
| 413 | — | Arquivo acima do limite |
| 415 | `EXTENSAO_NAO_ACEITA` | Extensão fora da lista |
| 422 | `ARQUIVOS_DEMAIS` | Mais arquivos que o limite |

Arquivo que não é inventário não derruba o envio na hora: o processamento termina
em `failed`, com o motivo legível em `error`.

## `GET /v1/inventories`

Lista os inventários do usuário, mais recentes primeiro. `limit` (1 a 200, 50) e
`offset`. Cada item: `inventory_id`, `status`, `provider`, `source_files`,
`resource_count`, `captured_at`, `created_at`.

## `GET /v1/inventories/{inventory_id}`

Estado e contagens. Além do resumo: `detected_formats` (arquivo → formato),
`captured_at_estimated`, `relation_count`, `rule_count`, `counts_by_kind`,
`counts` (o que foi descartado ou não modelado, com o motivo na chave),
`fingerprint` (SHA-256 do conteúdo normalizado), `declarations`,
`declarations_updated_at` e `error`.

## `GET /v1/inventories/{inventory_id}/resources`

Recursos normalizados, paginados pela chave. `kind` filtra pelo papel
(`instancia`, `banco`, `subrede`, `grupo_seguranca`…), `limit` até 500.
Cada item: `key` (ARN na AWS, nome do asset no GCP, id do ARM na Azure),
`kind`, `native_type`, `name`, `account`, `region`, `zone`, `state`,
`attributes`, `tags`.

## `GET /v1/inventories/{inventory_id}/topology`

A análise de topologia com as declarações vigentes: `resumo` (contagens), `ondas`
(em ordem; a onda 0 é a fundação de rede), `independentes`, `ferramentas`
(administração: migram por último), `grupos`, `unidades`, `dependencias`,
`dependencias_entre_ondas` (as portas que a VPN precisa liberar durante a
migração), `riscos`, `ociosos`, `premissas` e `fingerprint`. As listas saem
cortadas em `limites.maximo_por_lista`; `limites.cortadas` diz o total de cada.

**Limitação, dita na resposta:** as dependências vêm das regras de rede e do que
foi declarado. É o que a configuração permite, não o tráfego real.

| Status | `codigo` | Quando |
|---|---|---|
| 404 | `INVENTARIO_NAO_ENCONTRADO` | Não existe para este usuário |
| 409 | `INVENTARIO_NAO_PROCESSADO` | Ainda em `queued` ou `processing` (retryable) |

## `GET /v1/inventories/{inventory_id}/correlation?report_id=`

Quanto do custo do billing o inventário explica: `custo_da_base`,
`custo_com_recurso`, `custo_encontrado`, `fracao_encontrada`, as lacunas por
conta e região e por serviço (o que o inventário não cobre), `sem_cobranca`
(recursos do inventário que o billing não cobra), `ociosos` com o custo
(disco sem anexo, máquina parada), `alinhamento` no tempo (−30 a +45 dias do
período do billing) e `riscos` (`cobertura_baixa`, `inventario_desalinhado`).
Dinheiro na moeda do billing, sem conversão.

| Status | `codigo` | Quando |
|---|---|---|
| 404 | `REPORT_NAO_ENCONTRADO` | Billing não existe para este usuário |
| 409 | `REPORT_NAO_PRONTO` | Billing ainda processando (retryable) |
| 422 | `INVENTARIO_DE_OUTRA_NUVEM` | Inventário e billing de provedores diferentes |
| 422 | `INVENTARIO_NAO_CORRESPONDE` | Inventário de outra conta: nenhum recurso da fatura está nele (AWS e Azure: também nenhuma conta em comum). Vem com `contas_da_fatura` e `contas_do_inventario` |

## `PUT /v1/inventories/{inventory_id}/declarations`

Substitui as declarações: o que o inventário não tem como saber. Muda a
topologia calculada daqui em diante; análise já feita não muda (ela congelou a
topologia que usou).

```json
{
  "chave_aplicacao": null,
  "chave_ambiente": null,
  "criticidade": {"recursos": {"<chave>": "alta"}, "aplicacoes": {"checkout": "alta"}},
  "dependencias": [{"de": "aplicacao:relatorios", "para": "aplicacao:ingestao", "acoplamento": "forte"}],
  "ferramentas": ["aplicacao:monitoramento"]
}
```

`null` nas chaves de tag detecta a chave (`app`, `application`, `env`…); `""`
desliga o agrupamento por tag. Referência a unidade: a chave de um recurso ou
`aplicacao:<valor da tag>`. `422 DECLARACAO_INVALIDA` quando uma chave de recurso
citada não existe no inventário.

## `DELETE /v1/inventories/{inventory_id}`

Apaga o inventário e tudo que deriva dele (recursos, relações, regras). **204**.

---

## Na análise

`POST /v1/analyses/start/{report_id}` aceita `{"inventory_id": "<uuid>"}`, e
`GET /v1/analyses/{report_id}/request` aceita `?inventory_id=`. O inventário é
validado **antes** de criar o job (e antes da idempotência):

| Status | `codigo` | Quando |
|---|---|---|
| 404 | `INVENTARIO_DESLIGADO` | Flag desligada |
| 404 | `REPORT_NAO_ENCONTRADO` | Billing não existe para este usuário |
| 404 | `INVENTARIO_NAO_ENCONTRADO` | Inventário não existe para este usuário |
| 409 | `INVENTARIO_NAO_PROCESSADO` | Inventário ainda processando |
| 422 | `INVENTARIO_DE_OUTRA_NUVEM` | Provedores diferentes |
| 422 | `INVENTARIO_NAO_CORRESPONDE` | Inventário de outra conta: nenhum recurso da fatura está nele (AWS e Azure: também nenhuma conta em comum). Vem com `contas_da_fatura` e `contas_do_inventario` |

O payload da análise ganha a chave `topologia` e **nada mais muda** (custo,
economia e recomendação são os mesmos com e sem inventário). `topologia.resumo`
é o que o modelo recebe; `topologia.detalhe` (subredes, grupos com regras,
posicionamento, versão e rede dos bancos, discos) fica só no payload persistido,
para o Terraform. A sequência de migração da resposta vem das ondas calculadas,
não do modelo, e os riscos de rede altos e médios entram nas flags.

## No Terraform

Com a análise feita com inventário, `POST /v1/terraform-generations` gera, além
do de sempre (RI15 do plano):

- subredes espelhadas com endereçamento novo dentro da faixa pedida (reusar o
  CIDR da origem quebraria a VPN da migração);
- as VMs de cada pool repartidas pelas subredes de origem, na quantidade que a
  análise aprovou, com o disco raiz do tamanho do boot de origem;
- grupos de segurança com as regras de entrada traduzidas. Regra de risco alto
  (administração ou banco aberto para a internet) **não é replicada**;
- a versão do banco medida como default da variável; na AWS, o banco num grupo
  de subredes da VPC nova;
- no README, a seção "Topologia de origem" (ondas, portas entre ondas, riscos,
  cobertura); no `manifest.json`, o inventário de onde saiu (id e fingerprint).
