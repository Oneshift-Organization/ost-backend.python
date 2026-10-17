# PLANO_PRECISAO_OCI — régua oficial, defeitos de preço e DE-PARA da OCI

> Criado em 17/10/2026. Queixa do usuário: "a OCI é a nuvem com a tabela menos bem
> mapeada; a discrepância de preço dela sempre fica descomunal; é a que menos encontra
> equivalentes". Decisões do usuário em 17/10: régua agora; P1 (defeitos objetivos,
> com o banco) e D (DE-PARA) no mesmo lote, com uma troca de versão do contrato; tela
> e o resto das premissas para as melhorias.

## 1. O que os dados mostraram

- **"Menos equivalentes" é verdade, e não mexe no preço.** Serviços sem equivalente
  OCI na Portabilidade: 60 de 158 (38%), contra 22% a 25% nas outras nuvens (11
  faturas do develop). O DE-PARA tem 102 linhas da OCI contra 231 a 256 das outras. O
  DE-PARA alimenta a Portabilidade, o lock-in e a narrativa; o custo sai por categoria.
- **A economia da OCI é, em boa parte, real.** Na fatura da tela (`fatura-aws-2026-09.csv`,
  US$ 377,73) a conta manual com preço oficial dá ~US$ 211 a 224 na OCI; o motor deu
  US$ 205,98. O total bate porque dois erros se anulam: compute ~US$ 16 caro demais e
  banco ~US$ 20 a 33 barato demais.
- **Composição da economia da OCI em 7 faturas do develop:** compute 34%, banco 28%,
  egress 11%, objetos 8%, SSD 8%, zeros 7%, HDD 3%.

## 2. Régua (pré-conferência com fonte oficial, 17/10)

Fonte de preço: lista publicada da Oracle (`oracle.com/cloud/price-list`, lida pelo
Chrome em 17/10) e a API pública de preços (`apexapps.oracle.com/.../cetools/api/v1/products`).
Fonte de regra: `docs.oracle.com` (Compute Shapes, Burstable Instances, Block Volume
Performance) e a página de preço de rede (`oracle.com/cloud/networking/virtual-cloud-network/pricing`).

| regra oficial | motor antes | item |
|---|---|---|
| A1: 1 OCPU = 1 vCPU, US$ 0,01/OCPU-h + US$ 0,0015/GB-h; 3.000 OCPU-h e 18.000 GB-h grátis por mês | a faixa grátis vem primeiro na API; o sync lia zero e **descartava a A1** | P1.1 |
| **A2 e A4: 1 OCPU = 2 vCPU** (A2 US$ 0,014/OCPU = US$ 0,007/vCPU) | toda família A com 1 vCPU por OCPU: ARM cotado pelo dobro | P1.1 |
| Flex: de 1 a 64 GB por OCPU | grade fixa de 4, 8 ou 16 GB por OCPU | P1.1 |
| Block Volume Balanced = US$ 0,0255/GB + 10 VPU × US$ 0,0017 = US$ 0,0425/GB | SSD a US$ 0,0255 (sem VPU) | P1.2 |
| Egress: **10 TB/mês grátis**, depois US$ 0,0085/GB (NA/EU/UK) | cobrado desde o primeiro GB | P1.5 |
| Tráfego dentro da região e VPN site-to-site: US$ 0 | zero | ✅ |
| NAT Gateway e IP público: sem item na lista de preços | zero | ✅ |
| Vault: chave de software grátis; HSM com 20 versões grátis | zero | ✅ |
| OKE Basic grátis; Enhanced US$ 0,10/h | Enhanced | ✅ |
| MySQL HeatWave: US$ 0,0366/ECPU-h + US$ 0,04/GB-mês; Database with PostgreSQL: US$ 0,098/OCPU-h + US$ 0,072/GB-mês | **taxa de referência à mão** (OCI 30 × AWS 62 por vCPU-mês) aplicada sobre todo o custo de banco | P1.6 |
| E3/E4/E5 com burst: cobra a linha de base (12,5% ou 50%) | não modelado | melhoria |

Fatura da tela, linha a linha (OCI oficial × motor): t4g.xlarge 23,57 × 32,13;
r7g.large 24,11 × 32,88; t3.micro 19,08 × 17,64; NAT 0 × 0; ELB 8,14 × 8,14; IP 0 × 0;
EBS 3,19 × 2,15; RDS 41,14 (MySQL) a ~54 (Postgres) × 21,35; EKS 72 × 72.

## 3. Lote P1 — defeitos objetivos (uma troca de versão do contrato)

Cada item: o que muda, em quem, e para que lado. Correção geral, nunca para uma fatura.

### P1.1 Compute da OCI no sync
- **Muda:** o sync lê a primeira faixa **paga** (a A1 entra); vCPU por OCPU pela
  família (A1 = 1; A2, A4 e x86 = 2); grade de RAM com mais razões por OCPU dentro do
  limite oficial.
- **Impacto:** só OCI, que **desce** no ARM (A1 e A2 pela metade do preço por vCPU) e
  deixa de sobrar RAM. O nome do SKU no Terraform continua `VM.Standard.<FAM>.Flex.<OCPU>c<GB>g`.
- **Ambiente:** exige novo sync de compute da OCI em dev e prod.

### P1.2 SSD da OCI com VPU
- **Muda:** `storage_ssd` da OCI = armazenamento + 10 VPU (Balanced); `storage_hdd`
  segue Lower Cost.
- **Impacto:** só OCI, que **sobe** no SSD (~67% sobre o valor antigo).

### P1.3 RAM da origem nas instâncias pequenas
- **Muda:** tabela exata das famílias burstable da AWS (t2, t3, t3a, t4g: nano 0,5 GiB,
  micro 1, small 2, medium 4, large 8...), no lugar da RAM pela letra.
- **Impacto:** **todos os destinos** descem nos pools com instância pequena; a origem não muda.

### P1.4 RAM da Azure pelo sufixo
- **Muda:** sufixo `l` = 2 GiB por vCPU (Dls, Dpls, Bls); série B v2 com `t` = 0,5 GiB
  por vCPU (B2ts_v2 e B2pts_v2 = 1 GiB).
- **Impacto:** só Azure, que **sobe** onde usava essas séries; tamanhos do Terraform da
  Azure ficam reais.

### P1.5 Egress pela quantidade, com franquia oficial
- **Muda:** o egress passa a ser cotado pelos GB medidos, como a rede por tipo, com a
  franquia mensal de cada provedor (OCI 10 TB; AWS e Azure 100 GB; GCP Premium 0)
  descontada uma vez por mês, não por região.
- **Impacto:** OCI **desce** muito em faturas com egress abaixo de 10 TB; AWS e Azure
  descem pouco; GCP igual.

### P1.6 Banco pela vCPU-hora da instância e pelo GB
- **Muda:** o custo de banco se divide em instância (vCPU-hora da classe, lida do tipo
  da instância) e armazenamento (GB-mês). Cada parte é cotada pela taxa oficial do
  destino, por motor quando a fatura diz o motor, e pela maior das taxas quando não diz
  (premissa declarada).
- **Impacto:** **as 4 nuvens** mudam, porque o método é comum. A OCI **sobe** (deixa de
  sair pela metade); as outras ficam mais perto do preço real da classe.

## 4. Lote D — DE-PARA da OCI

- **Muda:** linhas novas no DE-PARA para os serviços que a OCI tem: Load Balancer, NAT
  Gateway, OCI Cache (Redis), Streaming (Kafka), NoSQL, API Gateway, Network Firewall,
  FastConnect, Site-to-Site VPN, Bastion, Data Integration, Generative AI, OpenSearch,
  Container Instances. O que a OCI não tem fica sem linha (CDN própria).
- **Impacto:** não mexe no preço. Melhora a Portabilidade e o lock-in da OCI e acaba com a
  contradição "recomendado, mas 38% sem equivalente".

## 4b. Resultado (17/10, antes × depois nas faturas locais)

Mesmo banco e o mesmo sync nos dois lados (código da develop × branch); análise com LLM
em mock. US$/mês:

| fatura | atual | OCI | Azure | GCP | AWS | recomendada |
|---|---|---|---|---|---|---|
| fatura-aws-2026-09 (a da tela) | 378 | 206 → 224 | 310 → 399 | 400 → 423 | origem | OCI |
| aws_cost_explorer_full | 92.103 | 54.483 → 58.618 | 101.494 → 108.759 | 106.756 → 126.358 | origem | OCI |
| mock_aws_cur2 | 92.103 | 54.155 → 71.259 | 100.896 → 129.472 | 106.077 → 132.938 | origem | OCI |
| gcp_cost_table_full | 4.890 | 2.463 → 2.607 | 4.676 → 5.273 | origem | 4.412 → 4.859 | OCI |
| mock_azure | 6.711 | 2.557 → 2.828 | origem | 6.613 → 6.356 | 6.241 → 6.071 | OCI |

- Na fatura da tela a OCI fica em US$ 224, dentro da conta manual da régua (211 a 224).
- A OCI segue recomendada em todas; a economia dela fica menor e real.
- A Azure sobe onde usava série B `t`/`l` e D `l` (a RAM era de mentira).
- **O banco agora segue a regra do compute:** destino on-demand contra o custo efetivo
  da origem. Onde a origem tem RDS com reserva (o mock CUR: US$ 0,035 por vCPU-hora
  efetivo), todos os destinos sobem; a reserva da origem não vai junto na migração.
- O plano de migração passou a usar a razão do custo total de cada categoria (não a
  das taxas), para o banco e o egress saírem iguais à análise.

## 4c. Lote B — banco por partes (decidido em 17/10, depois da régua do mock CUR)

**Régua do banco do mock CUR** (US$ 30.035/mês na origem; conta manual com preço e
especificação oficiais, sem o motor): OCI ~US$ 24.000–24.500. O motor dava 38.508; com a
correção da taxa de reserva (horas em dobro, `78aa0cf`), 32.753 — ainda ~34% acima. As
causas e a correção de cada uma:

| # | causa | correção | quem muda |
|---|---|---|---|
| B1 | ElastiCache cotado como banco por vCPU-hora | cache pela memória do nó (GB-hora), na taxa de cache de cada nuvem, por faixa de tamanho do nó (OCI Cache: US$ 0,0194 até 10 GB por nó, 0,0136 acima) | as 4 nuvens como destino de cache; OCI desce |
| B2 | storage, IOPS, backup e cobranças da AWS pela razão de taxas de storage | o banco vira partes: **armazenamento** pelos GB medidos × taxa de storage de banco do destino; **backup** pelos GB × taxa de backup; **sem equivalente** (IOPS provisionado, throughput, créditos de CPU, extended support, requisições de IO do Aurora) a zero no destino, declarado; **outros** ao custo da origem | as 4 nuvens; a parte "sem equivalente" deixa de pesar em todos os destinos |
| B3 | PostgreSQL da OCI com memória somada (0,055/vCPU) | a calculadora oficial lista só OCPU (US$ 0,098 = 2 vCPU) e storage: **0,049/vCPU** | só OCI, desce |
| B4 | alta disponibilidade como o dobro em todo destino | réplicas por nuvem e motor: OCI MySQL = **3 instâncias**, cada uma com compute e storage (docs oficiais); OCI PostgreSQL = nós × compute e storage uma vez (premissa: storage otimizado compartilhado); demais = 2 | OCI MySQL com HA sobe 50%; OCI PostgreSQL com HA desce no storage |

**Análise de impacto (escrita antes do código):**

- **Números:** o banco de todos os destinos muda. Origens com muito IOPS provisionado,
  extended support ou créditos de CPU ficam mais baratas em qualquer destino (essas
  cobranças não migram). ElastiCache sai mais barato na OCI e no GCP.
- **Classificação:** pelo tipo de uso, IO antes de storage — o `Aurora:StorageIOUsage`
  é requisição de IO, não GB (o mock rotulava como GB-mês).
- **Linhas que não se encaixam** (proxy, backtrack, serverless do Aurora) ficam ao custo
  da origem, sem fingir cotação.
- **Payload:** `custos_auxiliares.banco_dados` continua um número só; o equivalente de
  cada provedor ganha `partes` (instâncias, cache, armazenamento, backup, sem
  equivalente, outros) — campo novo, aditivo; nota ao MCP.
- **Plano de migração:** usa a razão do custo total da categoria; acompanha sozinho.
- **Terraform:** o tamanho do banco não sai deste custo; não muda.
- **Ambientes:** taxas novas no seed (storage, backup e cache por nuvem, com fonte e
  data) — `sync_prices --providers categories` no develop.

**Resultado (17/10, develop × lote B nas faturas locais, US$/mês):**

| fatura | atual | OCI | Azure | GCP |
|---|---|---|---|---|
| a da tela | 378 | 224 → 217 | 399 → 399 | 423 → 423 |
| Cost Explorer completo | 92.103 | 58.618 → 60.258 | 108.759 → 114.228 | 126.358 → 119.967 |
| mock CUR | 92.103 | 65.504 → 56.734 | 120.159 → 114.603 | 125.687 → 120.408 |

- **Régua do banco do mock CUR:** o motor dá US$ 23.983 para a OCI; a conta manual com
  preço oficial dá US$ 23.983. Partes: instâncias 18.318, cache 2.510, armazenamento
  2.098 (RDS e Aurora), backup 886, sem equivalente 0, outros 172.
- **Cost Explorer:** a taxa da reserva vem sem horas e o uso coberto, a custo zero, traz
  as horas; o motor descartava as linhas a custo zero e perdia as horas cobertas (144 mil
  vCPU-hora contra 373 mil do CUR, mesmos dados). Agora `HeavyUsage` é sempre taxa da
  reserva (sem hora), o uso coberto conta, e o storage sem unidade é medido pelo nome,
  sob a trava de preço plausível. A diferença que resta entre os dois formatos é a
  premissa do motor desconhecido (o Cost Explorer não diz o motor: vale a maior taxa,
  com as 3 instâncias do MySQL da OCI em alta disponibilidade).
- A OCI segue recomendada em todas.

## 5. Para as melhorias

- **Tela:** separar "economia por preço oficial" de "economia estimada por premissa",
  com a fonte de cada linha (inclusive dos zeros).
- **Fonte e data dos zeros** (NAT, IP, segredos) exibidas, sem mudar o valor.
- **E3/E4/E5 com burst** como destino de origem burstable.
- **Sync de banco por API** nas 4 nuvens (o P1.6 usa as taxas oficiais registradas com
  fonte e data).
- **Faturas de origem GCP** com pools sem região (6 sem região e 3 "americas" no develop).
