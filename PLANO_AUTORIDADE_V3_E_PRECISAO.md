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

## 2. Lote da autoridade v3 (entregue como v4 em 16/10/2026)

> **Entregue como `agent-authority-v4`:** o PR #32 (score normalizado) já tinha
> usado a v3. O usuário decidiu soltar o lote agora, sem esperar o MCP, porque o
> #32 já força a nova preparação de snapshots. Itens e medição no §2.1; nota ao
> MCP em `MCP.PLAN/13-NOVIDADES-16-10-E-LOTE-V4.md`.

### 2.1 O que entrou, o que ficou de fora

| item | situação | efeito medido nos seis relatórios |
|---|---|---|
| V4.1 Contestação verificável | feito | não muda número; muda o rótulo de confiança quando o agente contesta. Pedidos de sync (`pedidos_de_sync`, `sync_prices --pedidos`). `preco_sugerido` opcional vira observação do agente no ciclo de vida, sem reviver o preço |
| V4.2 Provedor não avaliado | feito | sugestão `provedor_nao_avaliado` com o texto do agente e sem os números dele |
| V4.3 Classificação | feito, menos o que o fix A já cobriu (Filestore e nomes da OCI) | reserva de RDS e ElastiCache recotada como banco; MSK ao custo atual e no DE-PARA (`stream_ingestion` com a OCI Streaming); SKU "Storage" do Source Repository vai para objeto. AWS Cost Explorer: OCI +801/mês, Azure −275. CUR mock: OCI −338. Os outros iguais |
| V4.4 Herança de classe | **fora** | o score v4 do #32 lê o lock-in da metadata do catálogo e criou hipótese de classe com regra própria (rehost 80, replatform 60). Herdar classe mudaria essa hipótese e a estratégia 7R: combinar com o autor do #32 |
| V4.5 Bloco C do acoplamento | **fora** | o #32 já pôs a dependência do inventário no eixo técnico (10%) e decidiu explicitamente não promover a exclusividade a lock-in. O V4.5 faria o contrário: decisão de produto |
| V4.6 Pool coberto por compromisso | feito | pool que paga menos de 60% do próprio sob demanda num bill com compromisso de compute sai da diferença pool a pool, com o motivo. AWS Cost Explorer: C7G (88%), R7G, C8G, T3A, T4G, M5A, T2. Total igual |
| V4.7 Confiança pelo custo atual | feito (entrou no lote a pedido do usuário) | Azure mock baixa → média (0,6077); GCP real e BigQuery média; OCI média; AWS Cost Explorer baixa (0,4781); CUR mock média → baixa (0,5504). O banco gerenciado só tem taxa de referência e agora pesa pelo custo real |

Nenhum item mudou decisão. Gates de integração: 183 passaram no banco igual ao do
CI.

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

**Refazer o nível de dependência com o inventário (decidido em 14/10).** Hoje o
nível vem só do catálogo; o inventário acrescenta hipóteses e o indicador
"Recurso exclusivo", sem mudar o nível nem o score. Medido no mock CUR: AmazonRDS
fica "Média" com e sem inventário, embora 37% do custo seja Aurora (exclusivo da
AWS). Com a v3, o nível passa a ser refeito pelas três regras:

1. **Só a exclusividade mexe no nível.** O nível efetivo é o maior entre o do
   catálogo e o da exclusividade do inventário (`dependencias.exclusividade`),
   com a origem declarada (`fonte: inventario`).
2. **"Migra junto" não entra no nível.** Banco compartilhado entre aplicações e
   grupo grande são dependência entre serviços, não da nuvem: ficam no eixo de
   dependência (grupos, ordem, esforço), separados. Hoje a hipótese "provável
   alta" do RDS vem do banco compartilhado e mistura os dois eixos; a sugestão
   de fonte `dependencias` deixa de alimentar `nivel_sugerido`.
3. **O inventário sobe o nível; só desce com cobertura alta.** Rebaixar (motor
   padrão no lugar do proprietário, ex. Postgres e não Aurora) exige
   `cobertura` do serviço ≥ 0,9; abaixo disso, não achar o recurso exclusivo
   não prova que ele não existe.

Não precisa de fluxo novo: anexar o inventário já refaz a análise inteira, e a
análise nova sai com o nível refeito. Muda o score: vai na `policy_version` nova
e na `agent-authority-v3`, com o antes e depois nos 6 relatórios e nos 2 mocks
com inventário (a mudança esperada é concentrada em RDS e ElastiCache dos mocks AWS).

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

**Feito (16/10):** `domain/services/categoria_por_zona.py`; o agregador guarda
`AuxiliaryCosts.por_regiao`; cada categoria sai com `zonas` (fração, região da
origem e parte sem região) quando cai em mais de uma. Zona sem taxa para um
provedor usa a razão da parte maior; a confiança é a mais fraca entre as partes
que pesam (20% ou mais), para uma fatia pequena não rebaixar a categoria.

| relatório | decisão | confiança | economia antes → depois (US$/mês) |
|---|---|---|---|
| AWS Cost Explorer (real) | OCI = OCI | medium = medium | OCI 40.430,87 → 40.419,93 · Azure −8.573,36 → −8.628,91 · GCP −16.944,08 → −16.886,13 |
| GCP Cost Table (real) | OCI = OCI | medium = medium | OCI 2.762,07 → 2.779,59 · Azure 493,98 → 546,79 · AWS 439,89 → 401,41 |
| GCP BigQuery (mock) | OCI = OCI | medium = medium | OCI 2.760,47 → 2.777,98 · Azure 492,42 → 545,17 · AWS 438,31 → 399,82 |
| AWS CUR (mock) | OCI = OCI | medium = medium | OCI 37.370,40 → 37.352,99 · Azure −10.074,43 → −10.168,77 · GCP −20.291,44 → −20.230,12 |
| Azure (mock) | OCI = OCI | medium = medium | igual |
| OCI São Paulo (mock) | nenhum = nenhum | medium = medium | igual |

### P1b Burstable na origem para destino sem burstable

T3A, T4G e T2 do bill AWS saem 3–6x no GCP: a vCPU burstable tem base de
20–40% e é cotada como vCPU cheia (`PLANO_EMPACOTAMENTO` §5.2).

**Feito (16/10):** `Perfil.base_cpu` (família T da AWS por tamanho, 40% sem
tamanho; núcleo compartilhado do GCP; série B da Azure, abaixo). No packer, origem
burstable: destino sem crédito recebe vCPU × base, destino burstable recebe a
nominal, vence o mais barato; a memória não muda. Premissa `burstable_pela_base`
lista os pools.

| relatório | decisão | confiança | economia antes → depois (US$/mês) |
|---|---|---|---|
| AWS Cost Explorer (real) | OCI = OCI | medium = medium | OCI 40.419,93 → 41.192,50 · Azure −8.628,91 → −8.006,72 · GCP −16.886,13 → −15.307,92 |
| AWS CUR (mock) | OCI = OCI | medium = medium | OCI 37.352,99 → 38.357,15 · Azure −10.168,77 → −8.957,11 · GCP −20.230,12 → −17.575,37 |
| GCP Cost Table, GCP BigQuery, OCI (mocks e real) | iguais | iguais | iguais (sem família burstable na origem) |
| Azure (mock) | iguais | iguais | iguais (o pool `BMS` é burstable, mas a série B ainda não tinha base) |

**Série B da Azure (correção C):** a base vem da documentação de tamanhos Bv1 e
Bsv2, que desde 2024 dá a base da VM na escala 0–100%, ou seja, a fração de cada
vCPU. Bv1 por tamanho (B1ls 5%, B1s 10%, B1ms e B2s 20%, B2ms 30%, B4ms 22,5%,
B8ms a B20ms 17%); pool só da família usa a maior base dela (`BLS` 5%, `BS` 20%,
`BMS` 30%). Bsv2, Basv2 e Bpsv2 pela letra do tamanho: `ts` 20%, `ls` 30%, `s` 40%.
Série B de versão sem tabela (uma v3 futura) fica sem base e não reduz.

Análise de impacto: muda só o bill da Azure com série B cotado em destino sem
burstable; nada lê `base_cpu` além do packer e da premissa. O risco é reduzir
demais, e por isso a família sem tamanho usa a maior base dela e a versão
desconhecida não reduz.

| relatório | decisão | confiança | economia antes → depois (US$/mês) |
|---|---|---|---|
| Azure (mock) | OCI = OCI | medium → low | OCI 4.116,34 → 4.153,54 · AWS 378,77 → 469,39 · GCP −4,83 → 98,21 |
| os outros cinco | iguais | iguais | iguais |

O pool `BMS/brazilsouth` (8,15 vCPU, 32,6 GiB) saía em AWS `t3a.2xlarge`
(US$ 359,95), GCP `e2-standard-8` (316,56) e OCI `E3.Flex.4c32g` (110,11); pela
base de 30% sai em `r5a.xlarge` (269,33), `e2-highmem-4` (213,52) e
`E3.Flex.2c32g` (72,91).

A confiança caiu de propósito da regra, não por erro: ela mede a fração do valor
**cotado** que vem de preço oficial. O compute (oficial) ficou US$ 230,86 mais
barato e as categorias (taxa `reference`) não mudaram, então a fração oficial foi
de 0,6052 para 0,5991, abaixo do limiar de 0,60. O número final passou a depender
um pouco mais das taxas de referência, e é isso que a métrica diz. O que sobe essa
confiança é cotar as categorias com preço oficial (preço de serviços não-compute,
proposto como próximo bloco). Medir pela fração do custo **atual** coberto por preço
oficial deixaria o rótulo estável, mas muda o significado de `custo_total_cotado`
no payload do agente: é decisão de contrato, para o lote v3, se for o caso.

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

### P3 Ciclo de vida do preço (proposta do usuário, 14/10)

O preço oficial envelhece e o billing recente o renova: estados `VIVO → MADURO →
EM_DECOMPOSICAO → ADUBO`, score por fonte, idade e corroboração, revitalização
por billing com quórum entre usuários. Plano completo, com o encaixe no que já
existe (`cloud_price_points`, `preco_idade_dias`, `pricing_public_on_demand_cost`,
`pedidos_de_sync` da V3.1): [`PLANO_CICLO_DE_VIDA_DO_PRECO.md`](PLANO_CICLO_DE_VIDA_DO_PRECO.md).
As fases P3.0 a P3.3 não mudam número; a P3.4 (trocar preço por quórum) sobe a
versão de autoridade.

## 4. Dependências

- O lote v3 espera os **testes conjuntos com o MCP na v2** (que dependem de a
  infra ligar o snapshot no dev).
- P1 não depende de ninguém; P2 depende da escolha da fonte.
- Nada aqui toca o Terraform, exceto o efeito do V3.3 no disco fantasma, que
  vai junto com a sprint de Terraform.
