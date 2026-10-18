# PLANO_EQUIVALENTES_E_TARIFAS — tela "desconhecido", tarifa pela quantidade e curadoria

> Pedido do usuário (18/10/2026): "porque não acha equivalentes em RDS, S3 e uma porrada
> de serviço que tem equivalentes" e, em seguida, "efetue essas 3 manutenções, antes
> pondere o que podem causar; se for coerente aplique; ao final faça a análise do /docs e
> efetue a curadoria". Correção geral, para as quatro nuvens; nada ajustado a uma fatura.

## 1. O que a medição mostrou (develop, análises de 16 a 18/10)

| achado | tamanho | causa |
|---|---|---|
| A tela mostra "desconhecido" no tipo de equivalência, na portabilidade e no risco | **1.033 de 1.033 linhas** da tabela de equivalentes, inclusive RDS e S3, que têm preço | nenhum seed preenche `equivalence_type`, `portability` e `migration_risk`; o front marca toda linha como "atenção" porque o risco nunca vem |
| O equivalente existe no DE-PARA, mas não há tarifa para a categoria | fatura Azure real: 196 mil de 411 mil sem preço em todos os destinos; AWS (Cost Explorer): 5,2 mil | o motor só cota compute, disco, objeto, banco, rede, segredos, chaves e CDN |
| O nome da fatura não bate com o do DE-PARA | Azure: "Azure OpenAI", "API Management", "ExpressRoute", "Azure Data Factory v2", "Azure Firewall" (51 mil); OCI real: `AUTONOMOUS_DATABASE`, `GOLDENGATE`, `GENERATIVE_AI`, `STREAMING`, `WAF`, `CACHE`... | o nome no DE-PARA é outro ("Azure OpenAI Service", "OCI GoldenGate"); a fila de curadoria existe e não foi rodada |
| "Functions" da Azure vira Step Functions | 4,7 mil na fatura Azure | o DE-PARA tem "Azure Functions" duas vezes: como função (`function_as_a_service`) e como orquestração (`step_functions`) |
| A OCI não tem PostgreSQL no DE-PARA de banco | RDS PostgreSQL aparece como "MySQL Database Service" na OCI | falta a linha "OCI Database with PostgreSQL" em `managed_relational_database` (o preço já sai pelo motor, lote B) |
| Consulta de DNS da Azure cai como tráfego de rede | 2,2 mil | linha `network_egress`; vai para "rede sem cotação" |

## 2. Manutenção 1: os três campos da tela

**Regra** (pura, `domain/services/qualidade_da_equivalencia.py`), a partir da evidência que
a tela já mostra (refatoração e lock-in, de `technical_evidence`):

| campo | valor |
|---|---|
| `migration_risk` | `high` se refatoração exigida ou lock-in alto; `medium` se lock-in médio, ou baixo com refatoração não confirmada; `low` só com lock-in baixo **e** refatoração dispensada; `unknown` sem lock-in conhecido ou com tipo inferido |
| `portability` | lock-in baixo → `portable`; médio → `semi_portable`; alto → `non_portable`; sem lock-in → `unknown` |
| `equivalence_type` | pool de máquinas → `hardware_match`; serviço curado com o mesmo tipo do destino → `functional_match`; resto → `unknown` |

Pool: a máquina virtual vai para máquina virtual (rehost); a refatoração só fica "não
confirmada" quando o destino troca a arquitetura (ARM ↔ x86).

**Onde:** os campos vão na `metadata` de cada destino **na preparação do payload**
(`run_analysis_pipeline._decision_workloads` e os equivalentes dos pools). A projeção da
tabela (`equivalence_projection`) já lê `metadata.equivalence_type` e não muda.

**Impacto:**
- tela: o risco e a portabilidade aparecem; linha com risco baixo ou médio deixa de ser
  "atenção" e vira "viável". Nada vira "não viável": o backend não escreve `none` nem `critical`;
- score: nenhum. A nota não lê esses três campos (`technical_assessment` lê refatoração e
  lock-in, que não mudam);
- agente (MCP): a autoridade não muda, porque a projeção não muda. Snapshot antigo continua
  "desconhecido"; payload novo traz os valores. Os três campos são `JULGAMENTO` na autoridade:
  o agente pode contestar, como antes. Nota ao MCP: valores novos, sem campo novo;
- risco: "médio" para lock-in baixo com refatoração não confirmada é o limite conservador, não
  um fato. Mitigação: a regra fica escrita na premissa e no detalhe da linha.

## 3. Manutenção 2: tarifa pela quantidade medida

Mesma máquina do R4 (`categoria_por_uso`): a linha vai para um balde pelo nome do serviço
(`reclassificacao_por_uso`), a regra do uso dá a categoria e o fator da unidade, e o destino
é cotado pela quantidade medida vezes a taxa dele. O que não casa com regra fica no balde, ao
custo atual, declarado.

### 3.1 Entram (unidade comparável e tarifa oficial nas quatro nuvens)

Primeira faixa paga, preço de lista, coletado em 18/10/2026. Confiança `reference` (seed,
sem região), como as taxas do banco.

| categoria (chave no payload) | unidade | AWS | Azure | GCP | OCI |
|---|---|---|---|---|---|
| `logs_ingestao` | GB | 0,50 (Standard) | 2,30 (Analytics Logs) | 0,50 | 0,05 ¹ |
| `logs_ingestao_basica` | GB | 0,25 (Infrequent Access) | 0,50 (Basic Logs) | 0,50 ² | 0,05 ¹ |
| `logs_retencao` | GB-mês | 0,03 | 0,10 | 0,01 | 0,05 |
| `dns_consultas` | 10 mil consultas | 0,004 | 0,004 | 0,004 | 0,0085 |
| `dns_zonas` | zona-mês | 0,50 | 0,50 | 0,20 | 0 ³ |
| `waf_requisicoes` | 10 mil requisições | 0,006 | 0,006 (Front Door WAF) | 0,0075 | 0,006 |
| `waf_politicas` | política-mês | 5 | 5 | 5 | 5 |
| `waf_regras` | regra-mês | 1 | 1 | 1 | 0 ³ |
| `registro_imagens` | GB-mês | 0,10 | 0,10 | 0,10 | 0,0255 ⁴ |
| `metricas_amostras` | 10 mil amostras | 0,0009 | 0,00011 | 0,0006 | — ⁵ |

¹ A OCI cobra o armazenamento do log (GB-mês, 10 GB grátis), não a ingestão. Como destino, com
a retenção padrão dela (30 dias), 1 GB ingerido ≈ 1 GB-mês (premissa declarada). Como origem, o
log da OCI fica ao custo atual: sem a retenção do cliente, o GB-mês não volta a GB ingerido.
² A GCP não tem camada básica. ³ Sem cobrança na lista oficial. ⁴ O Container Registry da OCI é
grátis; a imagem paga o Object Storage. ⁵ A OCI não tem Prometheus gerenciado: sem cotação. A
primeira versão cotava o ponto do Monitoring dela como amostra, e a fatura OCI real mostrou o
erro: o ponto custa 1/36 da amostra da AWS, e 558 dólares de Monitoring viravam 20 mil na AWS
(corrigido em 18/10, mesmo dia).

Fontes: AWS Price List bulk (`AmazonCloudWatch`, `AmazonRoute53`, `awswaf`, `AmazonECR`,
`AmazonPrometheus`, us-east-1); Azure Retail Prices API (eastus e global); Cloud Billing
Catalog API (`5490-F7B7-8DF6` Logging, `FA26-5236-B8B5` DNS, `E505-1604-58F8` Cloud Armor,
`149C-F9EC-3994` Artifact Registry, `58CD-E7C3-72CA` Monitoring); OCI cetools (`B92593`,
`B88525`, `B94277`, `B94579`, `B90925`).

### 3.2 Ficam ao custo atual, declarados (sem par um a um)

- Databricks (DBU contra máquina do EMR/Dataproc), IA por token (modelos diferentes),
  Sentinel e Defender (pacotes de recursos diferentes), API Management, AI Search, Event Hubs
  e Service Bus (unidade de capacidade contra requisição), App Service (plano), backup (taxa
  por instância protegida mais GB), tradução e linguagem;
- serverless (decisão do R4);
- o resto de cada balde novo: Logs Insights (GB varrido), métricas customizadas do CloudWatch
  (métrica-mês), alarme, dashboard, Bot Control e Fraud Control do WAF, taxa diária do ACR.

### 3.3 Impacto

- **Custo do destino:** muda onde esses serviços pesam. Fatura Azure real: Log Analytics
  (42,8 mil) cotado sai uns 12 mil na AWS, 19 mil na GCP e 4 mil na OCI; o Prometheus
  gerenciado (4,2 mil) sai **mais caro** na AWS (~23 mil) e na GCP (~16 mil). Consulta de
  DNS na OCI custa o dobro (0,85 por milhão).
- **Cobertura de preços** sobe (menos "mantido ao custo atual"), e com ela o eixo de cobertura
  da nota. **Score:** muda pelo financeiro e pela cobertura, por cálculo, não por regra nova.
  A M13 (nota alta pela conta) segue pendente de decisão; este lote não mexe na fórmula.
- **`nao_classificado` e "rede sem cotação"** diminuem: as linhas vão para os baldes novos.
- **Terraform:** as categorias novas não viram recurso; o `.tf` ganha o aviso, como
  segredos e CDN. A contagem de "SKUs não classificados" pode mudar (caracterização
  regenerada de propósito, se mudar).
- **Artifact Registry** deixa de ser cotado como objeto (0,023 na AWS) e passa a registro
  (0,10): a cotação antiga subestimava o destino.
- **Agente (MCP):** chaves novas em `custos_auxiliares` e os `equivalentes_*` delas;
  `equivalence_pricing._CATEGORIES` ganha as categorias. Como no R4, a trava troca o hash sem
  subir a versão: as chaves só existem em payload preparado depois.
- **Risco:** quantidade da origem lida errado (unidade do export) cota errado. Mitigação:
  sem quantidade conversível, a categoria cai na razão de taxas (como a rede), e o teste trava
  a unidade de cada nuvem.

## 4. Manutenção 3: DE-PARA e curadoria

**DE-PARA (CSV, 857 → 858 linhas):**
- a linha `application_services,step_functions,azure,Azure Functions` passa a
  "Azure Durable Functions" (a orquestração da Azure). O "Functions" da fatura volta a ser
  função: Lambda, Cloud Run functions, Oracle Functions;
- nova linha `database,managed_relational_database,oci,OCI Database with PostgreSQL`, com a
  nota curada igual à do PostgreSQL da Azure.

**Curadoria (develop, pela fila `/v1/admin/service-name-suggestions`):** apelido para nome que
é o mesmo serviço com outra grafia. Nome genérico ("Networking"), licença ("Virtual Machines
Licenses") e produto sem par no catálogo ficam pendentes ou são recusados, com o motivo.

**Impacto:** o serviço apelidado ganha tipo, equivalentes e lock-in; a nota muda nas análises
novas (as antigas são congeladas). O custo total não muda.

## 5. Ordem e verificação

1. Manutenção 1 e o DE-PARA; manutenção 2; testes, caracterização do Terraform e gates.
2. Merge na develop; deploy; seed do DE-PARA, da metadata e das taxas no develop e no
   `ost_dev`.
3. Foto antes e depois das faturas do develop (sem IA): custo, economia, cobertura, nota e
   linhas sem preço.
4. Curadoria no develop.

## 6. Preço de cada opção no destino (tela A, 18/10)

**Problema medido (develop, código atual):** 50 destinos têm mais de um equivalente (RDS para
a Azure: SQL Database, MySQL ou PostgreSQL). Em todos, as opções saem com o mesmo preço,
porque a cotação é do serviço e não da opção; em 29 nenhuma tem preço. A tela ainda soma as
opções: o RDS da c31d09cc aparecia com 3× o preço na OCI e na Azure (`estimateIn` ignora
`included_in_estimate`, que o backend marca em uma linha por destino).

**O que muda:**
- `build_migration_context` cota o banco também em cada motor aberto (MySQL e PostgreSQL),
  com as mesmas partes, zonas e alta disponibilidade da cotação usada, e grava `por_motor`
  em cada cotação de `equivalentes_banco_dados`. A cotação usada no total não muda (motor
  medido, ou o mais caro quando a fatura não diz).
- `service-couplings` ganha `opcoes_por_destino`: cada equivalente com motor, preço próprio,
  se é o preço que entrou no total e o motivo quando não tem cotação. No banco relacional, o
  preço da opção é o do serviço no destino trocando a parte de banco pela cotação no motor
  dela; serviço que serve aos dois motores (Cloud SQL, RDS, Aurora) vira uma opção por motor.
  Motor proprietário (Oracle, SQL Server) não tem taxa no catálogo: sai sem cotação, exceto
  quando é a única opção do destino (fica o preço do serviço, como hoje). Fora do banco, as
  opções levam o preço do serviço, marcado como cotação do serviço: o catálogo não diferencia
  o produto (KMS ou HSM).
- `por_motor` não vai para o modelo (como os metadados de qualidade): o modelo narra o total.
- Front: a tela A (lista por nuvem que abre as opções) e `estimateIn` somando só a linha que
  entra no total.

**O que não muda:** custo por provedor, economia, nota, recomendação, a tabela de
equivalentes (a projeção travada não é tocada), o Terraform e o snapshot do agente (campo
novo, aditivo, só na rota de acoplamento).

**Riscos e mitigação:**
- O painel mostra o preço de cada motor e o total da nuvem segue a premissa: a opção cujo
  preço entrou no total vem marcada, e a tela diz a premissa quando o motor não é conhecido.
- Análise antiga, sem `por_motor`: as opções ficam com o preço do serviço, marcado como
  cotação do serviço. Nada é inventado; uma análise nova traz o preço por motor.
- RDS e Aurora saem com o mesmo preço no mesmo motor: o catálogo não tem tarifa por produto.
  A tela diz "preço pelo motor".

## 7. DE-PARA: Application Gateway, Cosmos DB e AWS Config (18/10)

| caso | correção | efeito nas análises novas |
|---|---|---|
| Application Gateway (Azure, US$ 2,4 mil no develop) sem destino | linha `networking,load_balancer,azure,Azure Application Gateway` e apelido fixo "Application Gateway" (é o nome de toda fatura da Azure, como "Load Balancer"; o apelido fixo vale em todos os ambientes). A sugestão pendente no develop é aprovada para a linha nova | vira balanceador: Elastic Load Balancing, Cloud Load Balancing, OCI Load Balancing; entra no score pela nota do tipo |
| Cosmos DB sem destino na OCI | o Cosmos DB tem três linhas (SQL distribuído, chave-valor, documento) e vencia a primeira em ordem alfabética, SQL distribuído, que a OCI não tem. Passa a valer o tipo curado (`TIPO_PREFERIDO`, ao lado dos apelidos fixos; `service_identity` é regra travada do agente e fica como está) quando o classificador não indica: documento | AWS: Amazon DocumentDB (era Aurora DSQL); GCP: Firestore ou Datastore (era Spanner); OCI: Oracle NoSQL Database Cloud Service ou Autonomous JSON Database (era nenhum). Muda a nota de quem tem Cosmos DB; o custo total não muda |
| AWS Config sem destino na OCI | linha `management_tools,policy_enforcement,oci,Oracle Cloud Guard` | AWS Config → Oracle Cloud Guard. Efeito colateral: uma fatura da OCI cujo nome resolva para "Oracle Cloud Guard" passa de `trusted_advisor` para `policy_enforcement` (AWS Config, Azure Policy, Organization Policy). Hoje nenhum apelido da OCI leva a esse nome |

A regra geral "vence o tipo com par em mais nuvens" foi medida e descartada: mudaria 8 dos 79
serviços com mais de um tipo, alguns para pior (GuardDuty viraria central de segurança,
BigQuery deixaria de ser data lake).
