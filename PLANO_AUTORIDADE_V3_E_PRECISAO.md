# PLANO_AUTORIDADE_V3_E_PRECISAO — o lote que muda a análise corrigida, e a fila de precisão

> Escrito em 13/10/2026. Junta o que sobrou da fila depois do preço regional e
> do acoplamento: o que muda a análise corrigida vai num **lote único** (uma
> versão de autoridade a mais, não cinco), e o que só mexe em número de preço
> fica numa fila separada.

## 1. Por que um lote

`agent-authority-v2` entrou na develop em 13/10 (`a429c7f`), logo antes dos
testes conjuntos com o MCP. Cada mudança no que a autoridade corrige sobe a
versão, e todo snapshot preparado antes responde `409 SNAPSHOT_DESATUALIZADO`.
Subir a versão a cada item, durante os testes, faria o MCP preparar de novo a
cada dia sem ganho. Por isso, os itens abaixo são implementados em branches,
validados, e entram na develop **juntos**, depois que os testes conjuntos com a
v2 passarem.

## 2. Lote da autoridade v3

| # | item | o que muda | origem |
|---|---|---|---|
| V3.1 | Contestação de preço verificável | contestação contra preço oficial recente deixa de rebaixar | fila, item 10 |
| V3.2 | Provedor que o backend não avaliou vira sugestão | o agente compara um provedor fora dos cenários: hoje some em silêncio | fila, item 14 |
| V3.3 | Classificação de serviço | Cloud Filestore, MSK e os nomes de serviço da OCI | fila, item 5; achados do acoplamento |
| V3.4 | Herança de classe para serviço curado sem lock-in | 15 dos 24 serviços do bill AWS seguem "a confirmar" | revisão do bloco A do acoplamento |
| V3.5 | Bloco C do acoplamento | exclusividade e dependência do inventário entram no score | `PLANO_LOCKIN_INVENTARIO` §5 |
| V3.6 | Pool coberto por compromisso marcado na tabela de equivalência | o C7G aparece a 7–8x pool a pool; o total já está certo | `PLANO_EMPACOTAMENTO` §5.1 |

### V3.1 Contestação verificável

Hoje (`agent_result.processar_sugestoes`): contestação de preço com severidade
alta rebaixa a confiança um degrau, sem olhar o preço contestado.

Com a v3, a verificação é feita contra o **payload do snapshot** (reproduzível),
que já carrega, por equivalente, `preco_origem`, `preco_capturado_em`,
`preco_idade_dias`, `preco_regiao` e `confianca_preco`:

| o preço contestado (achado pela `referencia`, ex. `OCI/E5/sa-saopaulo-1`) | efeito |
|---|---|
| `official`, da região pedida, com idade ≤ 30 dias | **não rebaixa**; `efeito_backend: verificado_mantido`, com a data e a fonte |
| `official` mais velho que 30 dias, ou de outra região (`preco_regiao_aproximada`) | rebaixa, como hoje; registra pedido de sync |
| `reference` ou catálogo seed | rebaixa, como hoje; registra pedido de sync |
| referência não achada no payload | como hoje; `efeito_backend` diz que não foi verificável |

**A "fila de sync":** tabela `pedidos_de_sync` (provedor, região, tipo, sku,
motivo, snapshot, criado_em, atendido_em) e `scripts/sync_prices.py
--pedidos` atendendo o que estiver aberto. É a primeira vez que a contestação do
agente vira ação, e não só rebaixamento.

### V3.2 Provedor não avaliado

`impose_decision` descarta da `provider_comparison` o provedor que não está em
`decision_support.provider_scores`. Na v3 ele vira sugestão
`{tipo: "provedor_nao_avaliado", origem: "agente", efeito_backend: "informativo"}`
com o que o agente disse, sem score e sem número.

### V3.3 Classificação

| serviço | hoje | v3 |
|---|---|---|
| Cloud Filestore (GCP) | inferido `block_storage`; SKU em `storage_hdd`, o que gera cerca de 1.570 GB de disco HDD fantasma no Terraform | alias para `file_storage` (o catálogo já tem EFS, Azure Files, Filestore e OCI File Storage); SKU fora de `storage_hdd` |
| Source Repository, SKU "Storage" | `storage_hdd` (custo zero no bill real) | `object_storage`, como o Artifact Registry |
| Reserva de RDS e ElastiCache (`HeavyUsage:db.*`, `cache.*`) | `core`/`virtual_server` no enriquecimento | categoria do serviço de origem (banco, cache) — `PLANO_EMPACOTAMENTO` §5.3 |
| Amazon MSK | inferido `relational_database` no bill real e `network` no mock | o MSK não está no catálogo: curar a linha como `stream_ingestion` (o tipo de Kinesis, Event Hubs e Pub/Sub), com o OCI Streaming compatível com Kafka como equivalente, e o alias |
| OCI: `COMPUTE`, `DATABASE`, `BLOCK_STORAGE`, `OBJECT_STORAGE`, `NETWORK`, `LOAD_BALANCER` | 5 de 6 sem categoria no mock, fora do score | aliases revisados em `service_identity` |

O alias muda `service_type` e `type_source` do serviço, que o score lê: por isso
está no lote. A regra de SKU (Source Repository) é seed e iria sozinha, mas vai
junto para uma medição só de antes e depois.

### V3.4 Herança de classe

`learn_class_defaults` só entra quando o serviço não tem expectativa nenhuma.
Serviço curado com `requires_refactoring` e sem `vendor_lock_in_level` fica sem
nota. Na v3, a herança completa o campo que falta, marcada `fonte: "classe"`. Muda
`migration.classe_migracao`, que a estratégia 7R usa.

### V3.5 Bloco C do acoplamento

Ver `PLANO_LOCKIN_INVENTARIO.md` §5, bloco C: exclusividade do inventário e
dependências no eixo técnico (`policy_version` nova), tipo do recurso
confirmando o `service_type` inferido, ondas.

### Entrega do lote

1. Cada item numa branch, com testes e medição de antes e depois nos 6
   relatórios mais os 2 mocks com inventário.
2. Juntos na develop, `AUTHORITY_VERSION = agent-authority-v3` (e
   `policy_version` nova, se o V3.5 entrar), hash registrado.
3. Nota ao MCP no `MCP.PLAN` (12), como foi feito com a v2.

## 3. Fila de precisão de preço (não muda a versão de autoridade)

### P1 Custos auxiliares por zona

Hoje as categorias (disco, objeto, egress, banco) são cotadas na **zona
dominante** do bill: no GCP real, cerca de 3,5% do compute está em São Paulo, e
o disco inteiro é cotado no meio-oeste (`PLANO_PRECO_REGIONAL` R4).

- O agregador separa cada categoria por região da linha (o billing já traz).
- `estimate_by_cost_ratio` roda por zona e soma; a premissa lista a parte de
  cada zona.
- Sem região na linha, cai na zona dominante, com a fração declarada.
- Medir nos 6 relatórios: só deve mudar bill com custo relevante fora da zona
  dominante.

### P1b Burstable na origem para destino sem burstable

T3A, T4G e T2 do bill AWS saem 3–6x no GCP: a vCPU burstable tem base de
20–40% e é cotada como vCPU cheia (`PLANO_EMPACOTAMENTO` §5.2).

### P2 `perf_coef` (desempenho por vCPU entre gerações)

Bloqueado por **fonte de dados**: não há base pública e estável de desempenho
por vCPU entre gerações e provedores. Opções, em ordem de custo:

1. Tabela curada a partir de publicações oficiais de cada provedor (ganho
   declarado de geração para geração), marcada `reference`.
2. Benchmark próprio (CoreMark ou sysbench) em uma VM de cada família,
   reproduzível, com o resultado versionado.
3. Base comercial (SPEC CPU publicado), se houver licença.

Até decidir a fonte, o packer continua casando vCPU e RAM sem coeficiente. É
uma decisão de produto e de custo.

## 4. Dependências

- O lote v3 espera os **testes conjuntos com o MCP na v2** (que dependem de a
  infra ligar o snapshot no dev).
- P1 não depende de ninguém; P2 depende da escolha da fonte.
- Nada aqui toca o Terraform, exceto o efeito do V3.3 no disco fantasma, que
  vai junto com a sprint de Terraform.
