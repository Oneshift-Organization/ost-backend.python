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

`scripts/clouds/`, uma pasta por nuvem com o script e um `MANUAL.md` de passo a passo (visão geral em `LEIA-ME.md`). Um por nuvem, no estilo do da Azure:
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

## 7. Lote 2 — App Service, API Gateway, requisição de objeto e arquivos (23/10)

Pedido do usuário: "faça e teste" os quatro primeiros do §6. Correção geral, com
taxas das quatro nuvens, conferidas nas fontes oficiais em 23/10.

### 7.1 O que muda

- **L1, App Service como capacidade de compute.**
  - O plano (`P1 v3 App` em "Premium v3 Plan") é cobrado por instância-hora, e a
    Microsoft publica o tamanho de cada plano. Exemplos: B1 tem 1 vCPU e 1,75 GiB; P1v3,
    2 vCPU e 8 GiB; I1v2, 2 vCPU e 8 GiB.
  - O parser da Azure grava `instance_type` `AppService_P1v3` e a família `APPSERVICE_PV3`.
    O `azure_shape` dá o tamanho pela tabela publicada; a capacidade entra no pool como
    estimada, não como medida.
  - O destino é cotado pelas máquinas do mesmo tamanho (Elastic Beanstalk, App Engine
    flexível e OCI cobram a máquina).
  - Ficam ao custo atual: o que não é o plano (IP SSL, domínio, certificado, ambiente
    isolado) e os planos sem tamanho publicado (Free, Shared, Premium v4).
- **L2, API Gateway por chamada** (`api_requests`, por 10 mil):

  | nuvem | taxa por 10 mil | o quê |
  |---|---|---|
  | AWS | US$ 0,035 | REST API |
  | Azure | US$ 0,035 | API Management Consumption |
  | GCP | US$ 0,03 | API Gateway; cobra como Service Control |
  | OCI | US$ 0,03 | API Gateway |

  - Como origem, contam a chamada do REST e do HTTP API da AWS, a do Consumption e do
    v2 da Azure e a da OCI.
  - A unidade do APIM clássico (Developer, Basic, Standard, Premium por hora), o cache e
    o WebSocket ficam ao custo atual.
- **L3, requisição de armazenamento de objetos** (por 10 mil):

  | categoria | AWS (S3 Standard) | Azure (Blob Hot) | GCP (Standard regional) | OCI |
  |---|---|---|---|---|
  | escrita (`object_requests_write`; Tier1, Write, Class A) | US$ 0,05 | US$ 0,05 | US$ 0,05 | US$ 0,0034 |
  | leitura (`object_requests_read`; Tier2, Read, Class B) | US$ 0,004 | US$ 0,004 | US$ 0,004 | US$ 0,0034 |

  - A OCI cobra uma taxa só por requisição. Como origem, ela não separa escrita de
    leitura e fica ao custo atual.
- **L4, arquivos por GB-mês** (`file_storage_capacity`), o tier de uso geral em SSD:

  | AWS | Azure | GCP | OCI |
  |---|---|---|---|
  | US$ 0,30 (EFS Standard) | US$ 0,16 (Files Premium LRS) | US$ 0,30 (Filestore Basic SSD) | US$ 0,30 (File Storage) |

  - Como origem: EFS Standard e One Zone, Azure Files (Premium provisionado e Hot, Cool e
    Transaction Optimized armazenado), capacidade do Filestore e File Storage da OCI.
  - IA e Archive do EFS, operações e snapshots ficam ao custo atual, em
    `armazenamento_arquivos`.

Fontes:
- AWS Price List bulk us-east-1: `AmazonApiGateway`, `AmazonS3`, `AmazonEFS`.
- Azure Retail Prices eastus: API Management, Storage, Azure App Service.
- Catálogo do GCP: Cloud Storage, Filestore. A página de preços do API Gateway: o SKU não
  está no catálogo, a cobrança sai como Service Control.
- OCI cetools: B92072, B91627, B89057.
- Tamanhos do App Service: página de preços da Microsoft.

Premissas, declaradas:
- **App Service:** o plano Windows embute a licença. Cotado como capacidade, o destino
  sai sem ela, como já acontece com a VM Windows da Azure.
- **API Gateway:** a chamada do HTTP API da AWS (US$ 1 por milhão) é cotada no destino
  pela taxa do produto gerenciado equivalente (US$ 3 a 3,50).
- **Sem camada gratuita no destino**, como nas outras categorias.

### 7.2 Análise de impacto

| quem consome | o que muda | risco | como fica coberto |
|---|---|---|---|
| parser da Azure (L1) | linha do plano do App Service ganha `instance_type`, família e `capacity_unit` | marcar como plano o que não é | o padrão exige o nome do medidor de plano (`P1 v3 App`); o resto fica como está |
| `azure_shape` (L1) | tabela de tamanhos dos planos | tamanho errado | tabela da página de preços; teste de cada tier |
| pools e cotação de compute (L1) | App Service entra no pool e é cotado por máquinas do mesmo tamanho | economia de plano Windows otimista | premissa declarada; medido na fatura típica |
| Terraform (L1) | o pool do App Service vira máquina no pacote | criar VM onde o cliente queria PaaS | aviso: o App Service entra como máquinas; o PaaS do destino se cria à parte |
| `reclassificacao_por_uso` e `categoria_por_uso` (L2 a L4) | baldes `api_gateway` e `object_requests`; regras novas em `file_storage` | levar linha de outro serviço | padrão pelo nome do serviço e do medidor; teste por nuvem |
| taxas (`seed_category_rates`) | 16 taxas novas, referência | confiança cair | medido; o sync oficial fica para depois, se a confiança cair |
| payload | chaves novas: `api_requisicoes`, `api_outros`, `objeto_requisicoes_escrita`, `objeto_requisicoes_leitura`, `objeto_requisicoes_outros`, `arquivos_capacidade`, e os `equivalentes_`; diminuem `reconhecidos_sem_cotacao` e `armazenamento_arquivos` | o agente somar errado | aditivo; a autoridade manda no número; nota ao MCP |
| `equivalence_pricing` | rótulos novos na tabela de categorias | hash da autoridade | troca o hash sem subir a versão, como antes |
| decisão e cobertura | o custo cotado sobe onde há esses serviços | decisão mudar | medido nas faturas típicas e na regressão |

### 7.3 Resultado (23/10)

Medido como no §4: develop `6d26e59` contra a branch carregada no mesmo processo.

| fatura típica | custo cotado antes | depois | o que passou a ser cotado |
|---|---|---|---|
| AWS | 78% | 79% | chamadas do API Gateway, requisições do S3, EFS |
| Azure | 77% | 82% | planos do App Service (P1v3, B1) como máquinas, operações de Blob, Azure Files |
| GCP | 83% | 88% | Filestore, operações do Cloud Storage |
| OCI | 89% | 91% | API Gateway, File Storage |

O que segue ao custo atual é o declarado: a unidade do APIM clássico e a requisição da OCI
como origem.

Regressão (faturas reais e mocks):
- decisão igual nas oito;
- cobertura sobe de 0,3 a 1,3 ponto onde há requisição de objeto, arquivo ou API;
- economia:
  - OCI no AWS Cost Explorer real: US$ 35.964 → 36.162 (requisição do S3 cotada pela taxa
    da OCI);
  - Azure no GCP Cost Table: −102 → −69 (Filestore cotado pelo Azure Files);
  - o resto, centavos;
- score: +0,1 a +0,4.

Testes de unidade: 2.672.

Achado de método: com saída grande, o stream do `kubectl exec` às vezes corta no meio
(o JSON termina sem fechar). Não é erro da análise: a mesma rodada, repetida, sai inteira.

## 8. Lote 3 — Functions, link dedicado e endpoint privado; correção do lote 2 (24/10)

Pedido do usuário: Functions por GB-segundo e por chamada, e link dedicado e endpoint
privado, "já na develop e na main". Taxas conferidas nas fontes oficiais em 24/10.

### 8.1 Defeito do lote 2, achado na medição deste lote

- **A chamada do API Gateway da OCI saía 1 milhão de vezes maior.**
  - O parser da OCI já multiplica a quantidade pelo número da unidade ("1,000,000 API
    Calls Per Month" → chamadas). A regra do §7 multiplicava de novo, por 100, como se
    viesse em milhões.
  - Na fatura típica da OCI, US$ 15 de chamadas viravam US$ 15 a 17,5 milhões por mês
    em cada destino. Está na produção desde a promoção de 23/10.
  - O teste do §7 congelou a premissa errada (quantidade "3" com unidade "x").
- **Por que a conferência não pegou.** "Cotado" só dizia se a linha tinha preço no
  destino, não o tamanho. A conferência agora mede também o preço implícito na origem
  (custo ÷ quantidade) contra a taxa de referência da própria nuvem
  (`Testes/conferencia/magnitude.py`). Quantidade na escala errada aparece como preço
  implícito longe da referência.
- **Classe fria cotada pela taxa da classe padrão.** A regra de requisição de objeto do
  §7 levava qualquer classe:
  - Archive, Cool e Cold do Blob;
  - Nearline, Coldline, Archive e Durable Reduced do Cloud Storage;
  - Standard-IA, Glacier e as requisições do Backup e do FSx no S3.

  A escrita dessas classes custa de 2 a 7 vezes a da classe padrão, e o destino saía
  barato demais. Ficam ao custo atual, em `objeto_requisicoes_outros`.

Correção: na OCI, a unidade da linha diz a escala ("API Calls Per Month" é chamada
unitária). Cada regra de requisição de objeto passa a valer só para a classe padrão:
S3 Standard e Intelligent-Tiering, Blob Hot e Cloud Storage Standard.

### 8.2 O que muda

- **F, Functions** (balde `functions`). Lambda, Azure Functions (Consumption e Flex sob
  demanda), Cloud Run functions (1ª e 2ª geração) e Oracle Functions:

  | categoria | unidade | AWS | Azure | GCP | OCI |
  |---|---|---|---|---|---|
  | `function_compute` (memória × tempo) | GiB-hora | US$ 0,06 | 0,0576 | 0,0594 | 0,051 |
  | `function_requests` | 10 mil chamadas | 0,002 | 0,002 | 0,004 | 0,002 |
  | `function_cpu` (CPU cobrada à parte) | vCPU-hora | 0 | 0 | 0 | 0 |

  - O GB-segundo da AWS, da Azure e da OCI já inclui a CPU. O GCP cobra CPU e memória à
    parte. A taxa dele no destino é a de uma função de 1 GiB com a CPU padrão (0,583
    vCPU): 0,0000025 + 0,583 × 0,000024 por GiB-segundo.
  - Como origem, a linha de CPU do GCP entra a zero em todos os destinos: o GB-segundo
    do destino já a cobre. Premissa declarada: a função usa a CPU padrão do tamanho.
  - Ficam ao custo atual, em `functions_other`:
    - concorrência provisionada, Lambda@Edge e SnapStart;
    - o plano Premium e o Always Ready da Azure;
    - instância mínima do GCP;
    - o resto de cada serviço.
  - A Lambda deixa o balde `serverless` (Cloud Run e Container Apps continuam nele).
- **L, link dedicado** (balde `dedicated_link`). Direct Connect, ExpressRoute, Cloud
  Interconnect e FastConnect:

  | categoria | unidade | AWS | Azure | GCP | OCI |
  |---|---|---|---|---|---|
  | `dedicated_link_1g` | hora | US$ 0,30 | 0,7873 | 0,2778 | 0,2125 |
  | `dedicated_link_10g` | hora | 2,25 | 6,5275 | 2,428 | 1,275 |
  | `dedicated_link_100g` | hora | 22,50 | 70,3632 | 24,28 | 10,75 |
  | `dedicated_link_data` (saída pelo link) | GB | 0,02 | 0,025 | 0,02 | 0 |
  | `dedicated_link_gateway` (gateway ou anexo que acompanha o link) | hora | 0 | 0 | 0 | 0 |

  - O que entra em cada taxa:
    - **AWS:** a porta dedicada.
    - **Azure:** o circuito Standard Metered (1 e 10 Gbps) ou a porta do ExpressRoute
      Direct (100 Gbps), mais o gateway que aguenta a velocidade: Standard para 1 Gbps
      e Ultra Performance para os outros.
    - **GCP:** o anexo do Partner Interconnect (1 Gbps), ou o circuito dedicado mais um
      anexo VLAN (10 e 100 Gbps).
    - **OCI:** a porta do FastConnect. A OCI não cobra o dado que passa pelo link.
  - Como origem, o gateway do ExpressRoute e o anexo VLAN do Dedicated Interconnect
    entram a zero em todos os destinos: a taxa da porta do destino já cobre o que o link
    precisa.
  - A taxa do parceiro de conectividade (operadora, colocation) fica fora nas quatro.
  - Ficam ao custo atual, em `dedicated_link_other`:
    - circuitos Unlimited, Premium, Local e Global Reach;
    - portas Metro e de outras velocidades (50 Mbps a 5 Gbps, 400 Gbps);
    - Cross-Cloud Interconnect e a versão MPS;
    - a porta de tarifa fixa da AWS.
  - A saída pelo link deixa o egress: o ExpressRoute e o Interconnect caíam na taxa de
    internet (US$ 0,087 a 0,12 por GB, contra 0,02 a 0,025).
- **E, endpoint privado** (balde `private_endpoint`). VPC Endpoint de interface,
  Private Endpoint da Azure, Private Service Connect:

  | categoria | unidade | AWS | Azure | GCP | OCI |
  |---|---|---|---|---|---|
  | `private_endpoint_hours` | hora | US$ 0,01 | 0,01 | 0,01 | 0 |
  | `private_endpoint_data` | GB | 0,01 | 0,01 | 0,01 | 0 |

  - A lista de preços da Oracle não tem item de endpoint privado.
  - Ficam ao custo atual, em `private_endpoint_other`: endpoint de Gateway Load
    Balancer, de recurso e de serviço da AWS, e o Fixed Private Endpoint da Azure.

Fontes:
- AWS Price List bulk us-east-1: `AWSLambda`, `AWSDirectConnect`, `AmazonVPC`.
- Azure Retail Prices: Functions e Private Link (eastus e global); ExpressRoute (Zone 1,
  consultado em 24/10).
- Catálogo do GCP: Cloud Run Functions, Cloud Interconnect, Private Service Connect.
- OCI cetools: B88325, B88326, B93126 (FastConnect), B90617 e B90618 (Functions).

Premissas, declaradas:
- **Sem camada gratuita no destino**, como nas outras categorias. Na Lambda, ela vale
  US$ 6,67 por mês.
- **Function de 1 GiB com a CPU padrão** na taxa do GCP e na linha de CPU a zero.
- **Link sem a taxa do parceiro** e com um gateway ou anexo por link.

### 8.3 Análise de impacto

| quem consome | o que muda | risco | como fica coberto |
|---|---|---|---|
| `reclassificacao_por_uso` | baldes `functions`, `dedicated_link` e `private_endpoint`; a Lambda, as Functions da Azure e da OCI e as Cloud Run functions saem de `serverless` | levar linha de outro serviço (Cloud Run, endpoint do Container Apps, SQS) | padrão pelo nome do serviço; teste por nuvem e dos vizinhos |
| `categoria_por_uso` | 13 categorias novas; na Azure, pela unidade da regra; na OCI, pelo rótulo da unidade | escala da quantidade | preço implícito na origem igual à taxa de referência, por categoria, na fatura típica |
| `rede_por_uso` | rótulos novos da OCI (GB memory-seconds, invocações, chamadas de API) | nenhum para as categorias de rede | só converte o que tem a dimensão da categoria |
| correção do §7 | a chamada da OCI cai 1 milhão de vezes; a classe fria vai para `objeto_requisicoes_outros` | cobertura cair um pouco | medido; o custo cotado fica certo |
| egress | a saída pelo ExpressRoute e pelo Interconnect sai do `egresso_rede` | egress cair | o mesmo custo, cotado pela taxa do link |
| taxas (`seed_category_rates`) | 40 taxas novas, referência, e a data de 24/10 | confiança cair | medido; rodar o seed das categorias no develop e na produção |
| payload | chaves novas (`funcoes_*`, `link_dedicado_*`, `endpoint_privado_*` e os `equivalentes_`); `computacao_serverless` e `rede_outros` diminuem | o agente somar errado | aditivo; a autoridade manda no número; nota ao MCP |
| `equivalence_pricing` | rótulos novos | hash da autoridade | troca o hash sem subir a versão, como antes |
| Terraform | aviso de que Functions, link e endpoint não viram recurso | criar recurso errado | o pacote não cria; o aviso diz o que criar à parte |
| decisão e cobertura | o custo cotado sobe onde há esses serviços; na OCI como origem, a decisão volta a existir | decisão mudar | medido nas faturas típicas e na regressão |

### 8.4 Resultado (24/10)

Medido como no §4: develop (`894d0bc`, o mesmo código da produção) contra a branch
carregada no mesmo processo. As faturas típicas ganharam as linhas deste lote; o S3 do
gerador saía com o preço do SQS (mesmo UsageType), e o Blob e o Cloud Storage com a
classe fria.

| fatura típica | custo cotado antes | depois | destinos antes → depois |
|---|---|---|---|
| AWS | 77,0% | 81,3% | OCI 4.979 → 4.858; GCP 8.510 → 8.501; Azure 8.720 → 9.078 |
| Azure | 72,9% | 84,1% | OCI 3.709 → 3.116; AWS 5.360 → 4.875; GCP 5.377 → 4.880 |
| GCP | 82,5% | 89,2% | OCI 3.550 → 3.467; AWS 5.711 → 5.656; Azure 5.788 → 6.094 |
| OCI | 91,0% | 96,8% | GCP, AWS e Azure: 15 a 17,5 milhões → 6.606 a 6.712 |

- O preço implícito na origem bate com a taxa de referência da própria nuvem em todas
  as categorias. A exceção é a memória das Functions do GCP: a CPU está na outra linha,
  cotada a zero, e as duas linhas somadas (US$ 99) batem com o destino (US$ 100).
- No GCP, o dado do Interconnect saiu do egress de internet: o egress cotado no destino
  caiu de US$ 153 para 63. O Private Service Connect saiu de "não classificado".
- Na Azure, o link de 1 Gbps de origem (circuito 436 + gateway 264) vira a porta do
  destino (AWS 219). Como destino, a Azure custa o circuito mais o gateway (575).

Regressão (faturas reais e mocks):
- decisão igual nas oito;
- AWS Cost Explorer real e mock do CUR: cobertura +0,16 ponto (Lambda cotada); economia
  na OCI +US$ 146; score +0,07;
- as outras seis: iguais.

Testes de unidade: todos passando. Novo teste de ponta a ponta da OCI: o arquivo passa
pelo parser real e a quantidade chega na unidade da categoria (o teste do §7 usava uma
unidade inventada e não pegou a escala).
