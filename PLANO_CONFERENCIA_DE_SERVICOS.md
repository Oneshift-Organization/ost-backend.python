# PLANO_CONFERENCIA_DE_SERVICOS — o que cada serviço vira na análise, nas quatro nuvens

Pedido do usuário (23/10): "logo terei outra apresentação, mas pública: qualquer pessoa,
com qualquer billing, de qualquer cloud vai subir o billing. Faça uma conferência de
recursos e serviços, o que está mapeado corretamente, o que não está. O que falta, adiciona."

## 1. Método

Duas faturas sintéticas por nuvem, no formato real de cada export, com nomes, unidades e
preços tirados dos catálogos oficiais (23/10):

- **varredura do catálogo:** todo serviço do catálogo, até 4 medidores variados cada,
  US$ 10 por linha. Mostra a cauda longa.
  - Azure: 174 serviços e 335 linhas (Retail Prices API, eastus e global);
  - AWS: 256 serviços e 426 linhas (Price List bulk, us-east-1, com o código de produto do
    CUR 2.0);
  - GCP: 58 serviços e 226 linhas (Cloud Billing Catalog);
  - OCI: 106 categorias e 275 linhas (cetools).
- **fatura típica:** os serviços e medidores que mais pesam numa conta real, com peso de
  custo realista (US$ 2,9 mil a 7,7 mil).
  - AWS: 38 serviços, mais Savings Plan, RI, crédito e imposto;
  - Azure: 37 serviços;
  - GCP: 32 serviços;
  - OCI: 23 serviços.
  - Nomes conferidos no catálogo; o que não existe lá fica de fora, sem nome inventado.

Cada fatura passa pela pipeline real no develop (parser, enriquecimento, agregação,
análise, IA em mock). Para cada linha: o balde da classificação, a categoria final e os
destinos que a cotam. Para cada serviço: se o DE-PARA o reconhece. As ferramentas ficam fora
do repositório, em `Testes/conferencia/` (geradores, catálogos, executor no pod, analisador).

**Ressalva da OCI:** o `product/service` do cost report só foi confirmado para 7 códigos,
num relatório real (COMPUTE, BLOCK_STORAGE, OBJECTSTORE, NETWORK, ORALB, TELEMETRY,
OKE_CONTROL_PLANE). Os outros códigos da fatura sintética são inferidos.

## 2. Linha de base (develop `3fbfaf0`)

| fatura típica | serviços reconhecidos | custo classificado | custo cotado | não classificado |
|---|---|---|---|---|
| AWS | 89% | 76% | 74% | US$ 1.839 (24 SKUs) |
| Azure | 84% | 79% | 76% | US$ 1.312 (16 SKUs) |
| GCP | 88% | 81% | 75% | US$ 883 (14 SKUs) |
| OCI | 74% | 75% | 73% | US$ 742 (12 SKUs) |

Varredura do catálogo (cauda longa, peso igual por medidor): o classificado fica entre 21%
(OCI) e 57% (GCP). A maior parte desse resto são serviços de nicho (Orbital, PlayFab,
Chime, telefonia).

### 2.1 Defeitos achados

| # | defeito | efeito | exemplo |
|---|---|---|---|
| D1 | A camada L3 da classificação trata qualquer unidade "gibibyte" como RAM, e o código de região no meio do tipo de uso (`USW2`) parece família de máquina | transferência entre regiões vira memória e entra no pool de máquinas | `USE1-USW2-AWS-Out-ABytes-T2` → `ram` → pool |
| D2 | As regras da OCI casam o começo da descrição (`block volume*`, `fastconnect*`, `database*`); a lista oficial da Oracle escreve "Storage - Block Volume - Storage", "OCI - FastConnect 1 Gbps", "Oracle Base Database Service - …" | disco da OCI, FastConnect e parte do banco caem em não classificado | BLOCK_STORAGE 0% classificado |
| D3 | DynamoDB só é banco quando o tipo de uso começa com `dynamodb`; o CUR real traz `USE1-ReadCapacityUnit-Hrs`, `WriteRequestUnits`, `TimedStorage-ByteHrs` | DynamoDB não classificado, e o armazenamento dele vira objeto | DynamoDB 12% classificado |
| D4 | Banco do GCP sem o nome do produto no SKU (AlloyDB "Instance vCPU Cores", Bigtable "Server Node") | não classificado | AlloyDB, Bigtable 0% |
| D5 | Instância do OpenSearch (`ESInstance:r6g.large`) não traz "opensearch" no tipo de uso | não classificado, apesar da regra pretender banco | AmazonES 11% |
| D6 | Contêiner por segundo fora do R5: Container Instances da Azure e pods do GKE Autopilot | não classificado | ACI, Autopilot |
| D7 | Serviço reconhecido pelo DE-PARA, sem regra de linha, vai para "não classificado", a mesma gaveta do que ninguém reconhece | a tela diz "não classificado" para Redshift, Bedrock, SQS, API Gateway, App Service, Synapse… | US$ 1,6 mil na AWS típica |

## 3. O que muda

- **C1 (D1):** na L3, RAM só por memória-hora ("gibibyte hour", "gb hours") ou pelo token
  ram/memory no descritor. Volume de dados (GB) não é RAM. Junto, a AWS ganha
  `*-out-abytes*` e `*-in-abytes*` como rede.
- **C2 (D2):** regras da OCI sem âncora no começo: `*block volume*`, `*object storage*`,
  `*archive storage*`, `*file storage*`, `*fastconnect*`, `*load balancer*`,
  `*container engine*`, `*kubernetes engine*`, `*database*`. Valem a forma curta e a da
  lista oficial.
- **C3 (D3, D4, D5):** reclassificação pelo nome do serviço, na agregação, como no R4:
  - DynamoDB, OpenSearch (`AmazonES`), AlloyDB, Bigtable, Spanner, Firestore e
    Memorystore vão para banco;
  - a transferência de dados deles segue como rede.
- **C4 (D6):** Container Instances (vCPU e memória por duração) e pods do Autopilot
  (mCPU e memória pedidos) entram nas categorias de contêiner serverless do R5, cotados
  pela quantidade.
- **C5 (D7):** a linha sem regra de um serviço que o DE-PARA reconhece (curado ou apelido)
  vai para `reconhecidos_sem_cotacao`, ao custo atual, com o serviço em `por_servico`.
  "Não classificado" fica só para o que ninguém reconhece. É uma regra só, guiada pelo
  DE-PARA, sem lista de nomes por nuvem.
- **C6 (reconhecimento):**
  - Prefixo de marca: quando o nome exato não está no DE-PARA, a busca tenta o nome sem a
    marca e com cada uma das outras ("Event Grid" e "Azure Event Grid"; "Cloud Dataflow"
    e "Dataflow"; `AmazonBedrock` e "AWS Bedrock"; `QUEUE` e "OCI Queue").
  - Conferido contra todos os nomes dos quatro catálogos: 53 nomes novos reconhecidos, nenhum
    casado errado.
  - Os 20 apelidos aprovados pela curadoria em 18/10, que só existiam no banco do develop
    e da produção, passam para `PROVIDER_SERVICE_ALIASES`: um ambiente novo nasce com eles.
  - Os códigos da OCI confirmados no relatório real (`ORALB`, `OBJECTSTORE`,
    `OKE_CONTROL_PLANE`, `TELEMETRY`) e, marcados como inferidos, os da lista de preços.
  - Plano de suporte (AWS e GCP) ganha balde próprio, `suporte`, ao custo atual.
- **C7:** Cache com Redis e OpenSearch da OCI vão para banco, como o ElastiCache e o
  OpenSearch da AWS já iam.

### 3.1 Análise de impacto

| quem consome | o que muda | risco | como fica coberto |
|---|---|---|---|
| `sku_matcher` (C1) | linha com volume (GB) e família aparente deixa de virar RAM | RAM do GCP sem o token "ram" perder classificação | o SKU de RAM do GCP diz "Ram" e a unidade é "gibibyte hour"; teste com as duas formas |
| regras de SKU (C1, C2) | seed novo, rodado no pod | ordem de prioridade mudar classificação | os padrões novos só alargam o que já casava; teste de cada forma; seed idempotente |
| `reclassificacao_por_uso` (C3) | 7 serviços vão para banco pelo nome | levar linha de rede junto | a transferência de dados fica de fora pelo padrão; teste por nuvem |
| cotação de banco (C3) | DynamoDB, OpenSearch e os bancos do GCP passam a ser cotados pela razão de taxa de banco | número de NoSQL cotado com taxa de relacional | é o que a regra `dynamodb*` já pretendia; vai declarado na premissa; medido na fatura típica |
| `categoria_por_uso` (C4) | regras de ACI e Autopilot nas categorias do R5 | quantidade errada | unidade da regra na Azure (como no R5); no GCP, mCPU-hora vira vCPU-hora pelo fator 1/1000 |
| `billing_aggregator` (C5) | parâmetro novo, opcional: os serviços reconhecidos; a linha sem regra deles vai para `por_uso["recognized_unquoted"]` | perder custo | a conservação soma `por_uso`; sem o parâmetro, o comportamento é o de hoje |
| payload (C5) | chave nova `reconhecidos_sem_cotacao`; `nao_classificado` diminui | o agente ler "não classificado" como cobertura | aditivo, ao custo atual, sem `equivalentes_`; a cobertura não muda; nota ao MCP |
| Terraform (C5) | aviso dos serviços reconhecidos sem recurso | caracterização mudar | aviso novo só quando a chave tem custo |
| cobertura, decisão e score | C1 a C4 mudam números onde havia o defeito; C5 não muda número nenhum | decisão mudar | medido nas faturas típicas, nos mocks e nas faturas reais |
| `service_identity` e `_match` (C6) | nome sem match exato tenta a variante de marca | casar com o serviço errado | só quando o nome exato falha; conferido contra os quatro catálogos (53 novos, nenhum errado); o resto mínimo de 3 letras evita "q" |
| hash da autoridade v5.1 | `service_identity.py` está na lista; ganhou uma função que a autoridade não usa | — | hash trocado sem subir a versão, como no R2, R4 e R5 |
| análises antigas | nada; o payload é congelado | — | — |

### 3.2 Fora deste lote, e por quê

- **Cotação de mensageria, API Gateway, IA, data warehouse e identidade.** A unidade não se
  traduz uma a uma: SQS por requisição contra Pub/Sub por TiB; Redshift por nó contra
  BigQuery por TiB lido; tokens de modelos diferentes. Esses serviços ficam reconhecidos,
  ao custo atual (C5). API Gateway por milhão de chamadas é o próximo candidato.
- **Azure App Service como capacidade de compute.** O plano (P1v3 = 2 vCPU e 8 GiB) pode
  virar vCPU-hora, mas pede a tabela de tamanhos no parser. Fica ao custo atual (C5).
- **Códigos de serviço da OCI não confirmados.** O reconhecimento depende de um cost report
  real com esses serviços.

## 4. Resultado (23/10)

Medido no pod do develop, com a IA em mock e o usuário de teste. "Antes" é o develop
`3fbfaf0`; "depois" carrega os módulos da branch no mesmo processo, com as regras de SKU e
as taxas do seed em memória (mesmo banco e mesmos preços nas duas rodadas).

### 4.1 Fatura típica

| nuvem | serviços reconhecidos | custo classificado | custo cotado | não classificado |
|---|---|---|---|---|
| AWS | 89% → 92% | 76% → 100% | 74% → 78% | US$ 1.839 → 15 (Firehose) |
| Azure | 84% → 97% | 79% → 99% | 76% → 77% | US$ 1.312 → 49 (SignalR) |
| GCP | 88% → 94% | 81% → 100% | 75% → 83% | US$ 883 → 20 (Maps) |
| OCI | 74% → 100% | 75% → 100% | 73% → 89% | US$ 742 → 0 |

"Classificado" conta o que tem categoria com nome, cotada ou ao custo atual. A diferença
para "cotado" é o que fica reconhecido sem cotação (§6) e as categorias declaradas ao
custo atual (Functions, arquivos, suporte).

### 4.2 Varredura do catálogo (cauda longa)

| nuvem | reconhecidos | classificado | cotado |
|---|---|---|---|
| AWS (256 serviços) | 45% → 50% | 27% → 63% | 18% → 21% |
| Azure (174) | 33% → 44% | 24% → 52% | 16% → 17% |
| GCP (58) | 71% → 76% | 57% → 84% | 28% → 50% |
| OCI (32 códigos) | 56% → 97% | 21% → 62% | 16% → 33% |

O resto da cauda são serviços sem par nas outras nuvens (telefonia, satélite, jogos,
saúde) e que o DE-PARA não lista.

### 4.3 Regressão: faturas reais e mocks

AWS Cost Explorer (real), GCP Cost Table (real), fatura AWS de 09/2026 (real), Azure de
23/10 (real) e os quatro mocks:
- decisão, cobertura, economia, confiança, score e pools **iguais nas oito**;
- muda só o rótulo: o que era "não classificado" sem nome aparece em
  `reconhecidos_sem_cotacao`. No AWS Cost Explorer real são US$ 3.923,99: MSK, Translate,
  MWAA, SQS, requisições do S3, Comprehend e Directory Service.

Testes de unidade: 2.632 (54 novos em `test_conferencia_de_servicos.py`).

## 5. Scripts de exportação

`scripts/exportar_fatura/` (README no diretório). Um por nuvem, no estilo do da Azure:
rodam no shell do console, só leem, conferem o arquivo antes de entregar e explicam a
permissão que falta.

- **AWS:** o CUR 2.0 do mês, se a conta tem o export (junta as partes e pega a entrega mais
  recente). Senão, a fatura detalhada pela API do Cost Explorer: conta × serviço × tipo
  de uso, custo amortizado, sem imposto, crédito e reembolso, no formato que o parser da
  fatura detalhada lê.
- **GCP:** o mês do export do Cloud Billing para o BigQuery (prefere o detalhado), achado
  nos projetos que a conta enxerga. Sem export, explica como ativar e aponta o Cost table.
- **OCI:** os cost reports criados no mês e nos 5 dias seguintes, juntos num arquivo só
  (união das colunas de tag), só com as linhas de uso do mês.

Testados com a CLI de cada nuvem trocada por uma falsa, que devolve respostas no formato
documentado das APIs (paginação, mais de uma conta, entrega versionada, colunas de tag
diferentes, linha fora do mês). O arquivo gerado passa pelo detector e pelo parser certo.
Não rodaram contra conta real.

## 6. Fica para o próximo lote

Na fatura típica, reconhecido e ao custo atual, por custo:

| nuvem | serviços |
|---|---|
| AWS | Redshift, Amazon MQ, MSK, Bedrock, SageMaker, requisição do S3, API Gateway, Transit Gateway, Cognito, SQS, Config, CloudTrail, SES, Step Functions, SNS |
| Azure | Synapse, App Service, Databricks, Foundry Models (Azure OpenAI), AI Search, API Management, Backup, Application Insights, Event Hubs, Service Bus, Logic Apps, Event Grid |
| GCP | Composer, análise do BigQuery, Dataflow, Vertex AI, Gemini API, Pub/Sub, operações do Cloud Storage, Cloud Build, Identity Platform |
| OCI | Generative AI, API Gateway, Streaming, Queue, Notifications |

Candidatos, na ordem do ganho:
1. **Azure App Service** como capacidade de compute (tamanho do plano em vCPU e GiB).
2. **API Gateway** por milhão de chamadas (as quatro nuvens cobram assim).
3. **Requisição de armazenamento de objetos** (S3, Blob, Cloud Storage, Object Storage) por
   10 mil operações.
4. **Arquivos** (EFS, Azure Files, Filestore, File Storage) por GB-mês; hoje ao custo atual.
5. **Mensageria** pela requisição onde a unidade bate (SQS, Service Bus, OCI Queue).

Data warehouse, IA generativa e analytics seguem ao custo atual: a unidade não se traduz
(nó contra TiB lido; tokens de modelos diferentes).
