# PLANO_PRECO_NAO_COMPUTE — rede separada por tipo, cotada pela quantidade medida

> Pedido do usuário (16/10/2026): "como a gente não tem cotação na OCI, Azure e GCP
> de load balancer e outros recursos que claramente deveriam ter cotação". Correção
> geral, para as quatro nuvens; nada ajustado a uma fatura.

## 1. O que a medição mostrou

O problema não era só falta de preço. A categoria `network_egress` é um balde misto.
As regras de classificação (`seed_classification_rules`) mandam para ela:

- o egress de verdade;
- o balanceador (hora e LCU);
- o NAT (hora e GB processado);
- o IP público;
- a VPN;
- o tráfego entre zonas;
- endpoint, resolver de DNS, consulta de DNS, log entregue;
- até instância mínima do Cloud Run.

O balde inteiro é cotado pela **razão da taxa de egress**: custo × (egress do destino ÷
egress da origem). Hora de balanceador e GB de NAT viram "GB de internet".

| fatura (local) | no balde de rede | do que não é egress |
|---|---|---|
| AWS Cost Explorer (real) | NAT US$ 6.672, ELB 2.194, IPv4, VPN, endpoints, tráfego entre zonas | a maior parte |
| GCP Cost Table (real, em BRL) | túnel de VPN, regra de encaminhamento de balanceador, IP estático e externo | quase tudo |
| OCI (mock) | balanceador flexível | o balanceador |

Na OCI, o egress custa cerca de 1/10 do da AWS, e esses custos saíam a cerca de 10% do
valor. No GCP, saíam 33% acima, porque o egress do GCP é mais caro. Nenhum dos dois
números é preço de balanceador ou de NAT.

O segundo efeito é o que o usuário viu na tela. O preço de categoria só é anexado ao
serviço (o detalhe do AWSELB) quando o custo do serviço bate exatamente com o da
categoria. O AWSELB espalha linhas por hora, processamento, egress e tráfego entre
zonas, então nunca bate, e o serviço aparece sem cotação.

## 2. O que muda

### R1 — rede por tipo, cotada pela quantidade medida

- `domain/services/rede_por_uso.py`: uma tabela por nuvem (padrão no serviço e no uso
  → categoria e fator) tira cada linha do balde de rede para o tipo dela. Sem `if` por
  nuvem; o que não casa vai para `network_other`.
- Categorias novas e a unidade de cada uma:

| categoria | unidade | o que é |
|---|---|---|
| `load_balancer` | hora | hora de balanceador (ALB, NLB, regra de encaminhamento, Std LB, App Gateway, Flexible LB) |
| `load_balancer_data` | GB | processamento (LCU como 1 GB, dados processados, banda da OCI convertida) |
| `nat_gateway` | hora | hora de NAT |
| `nat_gateway_data` | GB | GB processado pelo NAT |
| `public_ip` | hora | IP público |
| `vpn` | hora | conexão de VPN site a site (2 túneis, como na AWS) |
| `inter_zone_transfer` | GB | tráfego entre zonas da mesma região |
| `network_other` | — | endpoint, DNS, resolver, log e o resto: **mantido ao custo atual** |

- **Cotação pela quantidade medida.** A fatura traz a quantidade de cada linha (horas,
  GB). O destino é cotado pela quantidade × a taxa dele na zona da linha, sem precisar
  da taxa da origem. Isso acerta a fatura de São Paulo, onde a origem paga o dobro de
  us-east; a razão de taxas da origem em us-east dobraria a quantidade. A linha sem
  quantidade usa o preço efetivo das linhas medidas da mesma categoria. Se nenhuma
  linha tiver quantidade, a cotação cai na razão de taxas, como as outras categorias.
- **O fator da tabela** traduz a unidade da linha para a da categoria:
  - regra adicional de balanceador do GCP e da Azure vale 0,4 de um balanceador
    (US$ 0,01 contra 0,025);
  - uptime do Cloud NAT é por VM, e 1/31,4 (0,0014 contra 0,044) vira hora de gateway;
  - túnel de VPN do GCP vale 1/2 conexão;
  - Mbps-hora do balanceador da OCI vale 0,45 GB.
- **Destino grátis vale zero.** Na OCI, NAT, VPN IPSec e IP público não têm cobrança.
  Hoje `estimate_by_cost_ratio` descarta taxa 0, e o destino ficaria com o custo atual.
  A taxa 0 passa a ser cotação zero no destino; a origem com taxa 0 continua sem cotação.
- **Taxas de referência** (`seed_category_rates`, us-east) vieram das APIs oficiais,
  consultadas em 16/10/2026: AWS (offer `AWSELB` e `AmazonVPC`), Azure (Retail Prices
  API), OCI (cetools). GCP pela tabela pública de preços de rede. NAT da AWS fica como
  referência (§4).

| categoria | AWS | Azure | GCP | OCI |
|---|---|---|---|---|
| `load_balancer` (h) | 0,0225 | 0,025 | 0,025 | 0,0113 |
| `load_balancer_data` (GB) | 0,008 | 0,005 | 0,008 | 0,0002 |
| `nat_gateway` (h) | 0,045 | 0,045 | 0,044 | 0 |
| `nat_gateway_data` (GB) | 0,045 | 0,045 | 0,045 | 0 |
| `public_ip` (h) | 0,005 | 0,005 | 0,005 | 0 |
| `vpn` (conexão-h) | 0,05 | — (gateway, sem taxa por conexão) | 0,10 | 0 |
| `inter_zone_transfer` (GB) | 0,01 | 0,01 | 0,01 | 0 |

### R2 — o serviço com o preço das linhas dele

O custo de cada serviço por categoria vai ao payload. O preço da categoria é repartido
pela parte de cada serviço, e o resto, o que não é de serviço elegível, vira a linha
agregada. O AWSELB passa a ter cotação no detalhe. Isso fica depois do R1, com análise
de impacto própria: mexe no `decision_support`, que não pode piorar.

### R3 — taxas oficiais por região

O `category_rate_client` ganha as categorias novas. O `sync_prices --providers
categories-live` passa a buscar:

| nuvem | fonte |
|---|---|
| AWS | `AWSELB` e `AmazonVPC` por região (arquivos pequenos) |
| Azure | Retail Prices; balanceador e NAT são globais |
| OCI | cetools |
| GCP | catálogo, com a chave que entrou no dev em 16/10 |

## 3. Análise de impacto

| quem consome | o que muda | risco | como fica coberto |
|---|---|---|---|
| `billing_aggregator` | as linhas do balde de rede viram 8 baldes; a quantidade é acumulada por região | perder custo na troca | conservação: a soma dos baldes continua igual ao total (`reconciliation`, teste) |
| `AuxiliaryCosts` | 8 campos, mais quantidade e custo medido por região | quem soma os baldes à mão esquecer um campo | `reconciliation` e `any_positive` passam a somar os novos; um teste de conservação com fatura que tem todos os tipos |
| cotação das categorias | rede por quantidade; egress continua pela razão | mudar o egress de verdade | o egress segue o mesmo caminho, só fica menor |
| resumo executivo e economia | os números mudam onde havia balanceador, NAT, IP, VPN ou tráfego entre zonas | decisão trocar | medido nos seis relatórios e registrado aqui |
| confiança da análise | a fatia de rede passa a ter a confiança da taxa de cada tipo | cair no dev, onde o egress era oficial | R3 junto do R1; destino grátis custa zero e não pesa |
| payload do LLM e do agente (`custos_auxiliares`) | chaves novas (`balanceador`, `nat`, …); `egresso_rede` fica menor | o agente somar errado | é aditivo; a autoridade do backend manda no número; nota ao MCP |
| `equivalence_pricing` (anexar preço ao serviço) | categorias novas, primeiro como linha agregada | linha agregada a mais | a conciliação por provedor segue; R2 troca pela parte de cada serviço |
| Terraform (`_registrar_lacunas`) | cita os serviços de rede pagos que não viram recurso pelo billing | caracterização mudar | testes de caracterização atualizados de propósito |
| análises antigas | nada; o payload fica congelado | — | — |
| classificação (`sku_classification_rules`) | nada: a separação é feita na agregação | — | não precisa re-seed nem re-enriquecer |

## 4. Fora do lote, e por quê

- **NAT da AWS por região:** o preço oficial está no offer do EC2, que tem 480 MB por
  região. Fica como referência de us-east; a origem AWS não precisa dele, porque a
  cotação é pela quantidade.
- **VPN como destino na Azure:** a cobrança é por gateway, não por conexão. Sem taxa,
  o destino Azure mantém o custo atual da VPN.
- **DNS, endpoint, resolver e log** ficam ao custo atual: valores pequenos, com modelos
  que não se traduzem um a um.
- **Instância mínima do Cloud Run** classificada como rede é erro da regra de
  classificação. Aqui ela só deixa de ser cotada como egress.

## 5. Resultado (16/10)

**Feito:** R1 e R3. R2 (o preço no serviço) é o próximo passo.

Fica igual:
- os testes de unidade (1730) e os gates de integração no banco igual ao do CI
  (181 passaram, 6 pulos permitidos);
- a conservação do custo;
- as análises antigas.

Seis relatórios (banco local com taxas de referência e oficiais), economia em US$/mês:

| relatório | decisão | confiança | economia antes → depois |
|---|---|---|---|
| AWS Cost Explorer (real) | OCI = OCI | medium → low | OCI 41.192,50 → 41.307,66 · Azure −8.006,72 → −7.974,58 · GCP −15.307,92 → −11.443,80 |
| GCP Cost Table (real) | OCI = OCI | medium = medium | OCI 2.779,59 → 2.446,18 · Azure 546,79 → 202,51 · AWS 401,41 → 472,04 |
| GCP BigQuery (mock) | OCI = OCI | medium = medium | OCI 2.777,98 → 2.444,43 · Azure 545,17 → 195,23 · AWS 399,82 → 462,51 |
| AWS CUR (mock) | OCI = OCI | medium = medium | OCI 38.357,15 → 38.269,13 · Azure −8.957,11 → −8.926,47 · GCP −17.575,37 → −13.968,90 |
| Azure (mock) | iguais | iguais | iguais (só egress de verdade) |
| OCI São Paulo (mock) | — | medium = medium | GCP −5.567,48 → −5.476,89 · AWS −6.525,55 → −6.475,25 · Azure −6.628,43 → −6.543,89 |

O que mudou, por quê:

- **GCP como destino, faturas AWS (+US$ 3,6 a 3,9 mil):** a rede saía 33% acima,
  porque o egress do GCP é mais caro. Agora NAT, balanceador e tráfego entre zonas
  saem pela taxa de cada um.
- **OCI e Azure como destino, faturas GCP (−US$ 333 e −344):**
  - antes, VPN, regra de encaminhamento e IP saíam por 7% (OCI) do custo;
  - agora o balanceador da OCI custa a taxa dele, e VPN e IP são grátis lá;
  - na Azure, a VPN fica ao custo atual (cobrança por gateway);
  - a decisão não muda.
- **Confiança do AWS Cost Explorer (0,5863, abaixo de 0,60):**
  - antes, o balde inteiro de rede (US$ 12,8 mil) saía com a taxa **oficial** de
    egress: oficial, mas do serviço errado, e o rótulo inflava a confiança;
  - agora cada tipo tem a taxa certa, e duas só existem como referência:
    - tráfego entre zonas no GCP: o catálogo não tem preço em us-east4, e a SKU de
      São Paulo vem sem faixa;
    - hora do Cloud NAT: o catálogo cobra por VM, e a referência usa o teto do gateway;
  - a rede oficial também ficou mais barata (NAT grátis na OCI), e pesa menos;
  - para subir de novo: fonte oficial para essas duas taxas, ou a métrica pelo custo
    atual, que é decisão de contrato para o lote v3.

Exemplos (AWS Cost Explorer, origem → Azure / GCP / OCI):

| item | origem | Azure | GCP | OCI |
|---|---|---|---|---|
| dados do NAT | 5.989,44 | 5.989,44 | 5.989,44 | 0 |
| hora do NAT | 682,99 | 468,72 | 458,30 | 0 |
| tráfego entre zonas | 3.061,98 | 3.061,99 | 3.061,99 | 0 |
| hora do balanceador | 357,79 | 395,48 | 395,48 | 178,75 |
| processamento | 541,04 | 404,08 | 646,54 | 17,96 |
| rede sem cotação (`network_other`) | 797,70 | custo atual | custo atual | custo atual |

### R2 — o preço chega ao serviço (16/10)

O payload leva `custos_auxiliares.por_servico`: o custo de cada serviço da fatura em
cada categoria. `equivalence_pricing` reparte a cotação de cada categoria pela parte
de cada serviço. Ganha preço o serviço identificado (curado ou por apelido) com ao
menos uma parte cotada no destino. A parte sem tarifa lá, ou fora das categorias,
fica ao custo atual, como no total do cenário, e a evidência diz isso
(`partes`, "Mantido ao custo atual"). O resto de cada categoria segue na linha
agregada. Snapshot antigo, sem `por_servico`, segue o caminho de antes.

| relatório | serviços com preço | linhas agregadas | conciliação |
|---|---|---|---|
| AWS Cost Explorer (real) | 0 → 11 de 39 | 27 → 21 | fecha nos 3 destinos |
| GCP Cost Table (real) | 0 → 7 de 20 | 29 → 23 | fecha |
| GCP BigQuery (mock) | 0 → 8 de 20 | 29 → 15 | fecha |
| AWS CUR (mock) | 0 → 6 de 39 | 35 → 21 | fecha |
| Azure (mock) | 1 → 2 de 4 | 12 → 3 | fecha |
| OCI (mock) | 3 → 4 de 5 | 6 → 3 | fecha |

O AWSELB do AWS Cost Explorer (origem US$ 2.194,26) passa a mostrar o custo no
destino: Azure 2.059,89, GCP 2.689,09, OCI 296,36. Antes, o detalhe dizia "Sem
tarifa ou consumo compatível". O resumo executivo não muda: só a distribuição das
linhas.

**Análise de impacto do R2:**
- muda só a projeção (`cross_cloud_equivalents`, `decision_support`), não o número
  do cenário;
- a conciliação fecha por construção: a parte mantida entra igual no coberto e no
  cotado;
- a trava de autoridade teve o hash da v3 trocado sem subir a versão, porque o
  caminho antigo é o mesmo.

**Para o dev:** depois do deploy, rodar no pod
`python - --providers categories,categories-live < scripts/sync_prices.py`. A
referência traz os zeros da OCI; a oficial traz as taxas por região.

## 6. R4 — Cloud Run, plano de controle do Kubernetes, segredos, chaves e CDN (16/10, noite)

Pedido do usuário: "Cloud Run: a instância mínima está classificada como rede" e
"preço de segredo, CDN e plano de controle do Kubernetes, que hoje ficam ao custo
atual". Correção geral, para as quatro nuvens.

### 6.1 O que a medição mostrou

| caso | onde cai hoje | efeito |
|---|---|---|
| CPU e memória do Cloud Run, inclusive a instância mínima, e as Cloud Run Functions (GCP real: R$ 8,5 mil/mês) | `network_egress`, pela regra `*cloud run*` e `*services min instance*`; desde o R1, em `rede_outros` | parece rede na tela e no payload; "Services CPU" (sem regra) cai em não classificado |
| Plano de controle (EKS por cluster, cluster do GKE, Uptime SLA do AKS, cluster Enhanced do OKE) | `gke_control_plane`, ao custo atual | nunca é cotado; o suporte estendido (US$ 0,50/h) se mistura com a hora do cluster |
| Segredos (Secrets Manager, Secret Manager, Key Vault) e chaves (KMS, Cloud KMS, Vault) | `unclassified`, `storage_hdd` (requisição do KMS e do Secrets Manager no bill antigo, "Secret version replica storage" do GCP pelo `*storage*`) | custo órfão, ou disco HDD que não existe |
| CDN (CloudFront, Cloud CDN, Front Door) | egress de internet; requisição da CloudFront em não classificado | cotada como egress comum; requisição fora |

### 6.2 O que muda

- **Classificação na agregação** (`reclassificacao_por_uso`, como a V4.3): vale para
  fatura já processada, sem reenriquecer, e vê o nome do serviço. É o que separa a
  requisição da CloudFront (`US-Requests-Tier2-HTTPS`) da do S3.
  - Cloud Run, Cloud Run Functions, Lambda (GB-segundo), Azure Functions e Container
    Apps, OCI Functions: `serverless`.
  - Secrets Manager, KMS, Secret Manager, Cloud KMS, Key Vault, Vault da OCI: `secrets`.
  - Requisições da CloudFront e consultas ao cache do Cloud CDN: rede.
- **Regras de SKU do GCP:** `*services min instance*` e `*cloud run*` deixam de ser
  rede. A fatura nova já sai certa do enriquecimento.
- **Categoria pelo uso** (`domain/services/categoria_por_uso.py`, irmão do
  `rede_por_uso`): cada balde vira categorias com unidade, cotadas pela quantidade
  medida.

| categoria | unidade | AWS | GCP | Azure | OCI |
|---|---|---|---|---|---|
| plano de controle (`gke_control_plane`) | cluster-hora | 0,10 | 0,10 | 0,10 (Standard, com SLA) | 0,10 (Enhanced) |
| segredo (`secrets`) | segredo-mês | 0,40 | 0,06 | 0 (só operação) | 0 |
| operação de segredo (`secrets_operations`) | 10 mil | 0,05 | 0,03 | 0,03 | 0 |
| chave (`key_management`) | chave-mês | 1,00 | 0,06 (software, o padrão) | 0 (software: só operação) | 0 (20 versões grátis; acima, 0,5334) |
| operação de chave (`key_management_operations`) | 10 mil | 0,03 | 0,03 | 0,03 | 0 |
| CDN (`cdn`, no balde de rede) | GB | 0,085 | 0,08 | 0,0825 (Front Door Standard) | sem CDN própria: ao custo atual |
| requisição de CDN (`cdn_requests`) | 10 mil | 0,01 (HTTPS) | 0,0075 | 0,009 | — |

Valores das APIs oficiais em 16/10/2026: AWS offer files (`AmazonEKS`, `AWSSecretsManager`,
`awskms`, `AmazonCloudFront`), Azure Retail Prices, catálogo do GCP, OCI cetools. Primeira
faixa, América do Norte e Europa.

Ficam ao custo atual, declarados:
- suporte estendido e LTS do Kubernetes, nós virtuais, plano provisionado;
- rotação, renovação de certificado, cofre privado e HSM dedicado;
- computação serverless. A cotação de Cloud Run contra Fargate, Container Apps e
  Container Instances fica para depois: o modelo de cobrança (vCPU-segundo ativo e
  ocioso) não se traduz um a um.

### 6.3 Análise de impacto

| quem consome | o que muda | risco | como fica coberto |
|---|---|---|---|
| `billing_aggregator` | os baldes `gke_control_plane`, `secrets` e `serverless` se dividem pelo uso; o resto vai para `AuxiliaryCosts.por_uso` | perder custo | conservação: `reconciliation` soma `por_uso`; teste com linha de cada tipo |
| `AuxiliaryCosts` | campo novo `por_uso` (categoria → custo); `gke_control_plane` fica só com a hora do cluster | quem soma à mão esquecer o mapa | `any_positive` e `reconciliation` leem o mapa |
| cotação das categorias | 7 categorias novas pela quantidade medida | mudar número onde havia plano de controle, segredo, chave ou CDN | medido nos seis relatórios |
| payload (`custos_auxiliares`) | chaves novas: `kubernetes_outros`, `segredos`, `segredos_operacoes`, `chaves_kms`, `chaves_kms_operacoes`, `seguranca_outros`, `computacao_serverless`, `cdn`, `cdn_requisicoes`, e os `equivalentes_` de cada uma; `gke_plano_controle` ganha equivalentes | o agente somar errado | aditivo; a autoridade manda no número; nota ao MCP |
| `equivalence_pricing` (trava da autoridade) | as chaves novas entram na tabela de categorias | mudar a análise corrigida de snapshot antigo | snapshot antigo não tem as chaves: o caminho é o mesmo. Troca o hash da v4, sem subir a versão, como no R2 |
| Terraform (`_registrar_lacunas`) | o aviso de plano de controle olha também `kubernetes_outros`; serverless, segredo e CDN entram na lista do que não vira recurso | caracterização mudar | caracterização atualizada de propósito, se mudar |
| confiança da análise | o custo que passa a ser cotado pesa pela taxa (referência) | rebaixar onde o número era oficial | as taxas são de referência conferidas; a V4.7 pesa pelo custo atual |
| análises antigas | nada; o payload é congelado | — | — |

### 6.4 Resultado (16/10, noite)

**Feito:**
- `categoria_por_uso` (plano de controle, segredos, chaves, serverless);
- CDN no `rede_por_uso`;
- correção na agregação (`reclassificacao_por_uso`) e regras de SKU do Cloud Run;
- taxas oficiais no `categories-live`:
  - plano de controle nas quatro nuvens;
  - segredo, chave e CDN na AWS, no GCP e na Azure;
  - a OCI não cobra segredo nem tem CDN, e fica com a referência.

**Achado no caminho:** o `categories-live` caía no primeiro 429 da Azure, a mesma
falha do A3 no develop. O cliente das categorias agora usa a espera da coleta de compute
(`get_json_com_espera`). Na primeira rodada local já passou: 238 taxas.

**Primeira medição: a confiança caiu.** Com as taxas novas só como referência, o Azure
(mock) foi de média para baixa: a fração oficial foi de 0,6077 para 0,5942. O plano de
controle do AKS (US$ 148,80) passou a ser cotado, mas com taxa de referência. Com as
taxas oficiais, a confiança voltou a média. Por isso o sync oficial entrou no mesmo
bloco.

Seis relatórios, banco local com as taxas oficiais, economia em US$/mês. Antes: a develop
com a curadoria de 16/10.

| relatório | decisão | confiança | economia antes → depois | cobertura do escolhido |
|---|---|---|---|---|
| AWS Cost Explorer (real) | OCI = OCI | low = low | OCI 42.108,48 → 42.125,90 · Azure −8.249,85 → −8.232,56 · GCP −11.393,75 → −11.377,62 | 0,9023 → 0,9081 |
| GCP Cost Table (real) | OCI = OCI | medium = medium | OCI 2.446,18 → 2.450,12 · AWS 472,04 → 470,26 · Azure 202,51 → 206,73 | 0,73 → 0,9648 |
| GCP BigQuery (mock) | OCI = OCI | medium = medium | OCI 2.444,43 → 2.448,38 · AWS 462,51 → 460,74 · Azure 195,23 → 199,46 | 0,7297 → 0,9644 |
| AWS CUR (mock) | OCI = OCI | low = low | OCI 37.931,17 → 37.948,67 · Azure −8.810,31 → −8.793,02 · GCP −13.990,01 → −13.973,87 | 0,9006 → 0,9064 |
| Azure (mock) | OCI = OCI | medium = medium | iguais | 0,9778 → 1,0 |
| OCI São Paulo (mock) | — | medium = medium | iguais | 1,0 = 1,0 |

O que mudou, e por quê:
- **Cobertura do GCP (0,73 → 0,96):** o plano de controle do GKE (zonal) e a instância
  mínima do Cloud Run eram a maior parte do que ficava sem cotação.
  - O plano de controle agora é cotado pela hora do cluster.
  - O Cloud Run sai da rede e entra como computação serverless, ao custo atual.
- **Economia:** muda pouco. A hora do cluster custa US$ 0,10 nas quatro nuvens. O efeito é
  o crédito do GKE (faixa gratuita): ele abate a origem e não segue para o destino.
- **Score:** sobe até 1 ponto no GCP, porque a cobertura das categorias cotadas
  subiu; nos outros, centésimos.

Ficam iguais:
- a decisão;
- a conservação do custo;
- os testes de unidade (1978);
- os gates de integração no banco igual ao do CI (186 passaram, 6 pulos permitidos).

**Para o develop:** depois do deploy, rodar no pod:
1. `seed_classification_rules`, para o Cloud Run de fatura nova;
2. `python - --providers categories,categories-live < scripts/sync_prices.py`.

Fatura já processada não precisa reenriquecer: a correção está na agregação.
