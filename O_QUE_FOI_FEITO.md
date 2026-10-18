# O que foi feito (18/10/2026)

Pedidos do dia: as três manutenções da tabela de equivalentes (com a análise de impacto
antes do código), a curadoria pelo `/docs` e, por último, o Terraform por escopo. Este
arquivo registra o que mudou, como foi testado e se o efeito foi real.

## Parte 1 — Tabela de equivalentes, tarifas e curadoria

### 1.1 O que mudou

| manutenção | o que era | o que ficou | onde |
|---|---|---|---|
| Tipo, portabilidade e risco | "desconhecido" em 1.033 de 1.033 linhas, inclusive RDS e S3 | calculados da evidência de refatoração e lock-in: pool → `hardware_match`; mesmo tipo curado → `functional_match`; risco e portabilidade pelo lock-in | `domain/services/qualidade_da_equivalencia.py`, `run_analysis_pipeline._decision_workloads` |
| Tarifa pela quantidade | logs, DNS, WAF, registro de imagens e métricas sem preço em nenhum destino | cotados pela quantidade medida, com taxa oficial das quatro nuvens (AWS Price List, Azure Retail API, Cloud Billing Catalog, OCI cetools, 18/10) | `categoria_por_uso`, `reclassificacao_por_uso`, `seed_category_rates` |
| DE-PARA | "Functions" da Azure virava Step Functions; a OCI não tinha PostgreSQL em banco gerenciado | "Azure Durable Functions" na orquestração; "OCI Database with PostgreSQL" em `managed_relational_database` (858 linhas) | `cloud_services_mapping.csv`, `seed_service_metadata` |
| Curadoria (develop) | 27 nomes de fatura sem linha no DE-PARA | 19 apelidos aprovados, 4 recusados, 4 pendentes | fila `/v1/admin/service-name-suggestions` |
| Correção no mesmo dia | o ponto do Monitoring da OCI era cotado como amostra de Prometheus (US$ 558 viravam US$ 20 mil na AWS) e o log da OCI, em GB-mês, como GB ingerido | os dois, como origem, ficam ao custo atual; a OCI sai da categoria de amostra | `fix(oci)` |

Ambientes: develop e `ost_dev` com DE-PARA, metadata e taxas aplicados; a taxa de amostra da
OCI marcada como ausente. CI verde nos dois merges (Tests e Build, Push & Deploy).

### 1.2 Como foi testado

1. Suítes: unitária inteira verde; integração no banco igual ao da CI (`ost_ci_gates`):
   203 passaram, 2 pulados (exigem preço oficial, ausente nesse banco, como na CI).
   `terraform validate` dos pacotes com topologia: 74 passaram.
2. Teste novo, **código antigo × código novo sobre os mesmos dados do develop** (o código da
   develop anterior copiado para o pod, mesmo banco): cinco faturas (Azure real, OCI real, AWS
   Cost Explorer, AWS real, GCP Cost Table), sem IA.
3. Foto de antes de tudo (código e banco antigos) para medir também o efeito da curadoria.

### 1.3 Quebrou algo do comportamento antigo?

**Não.** Em todas as faturas:

| verificação | resultado |
|---|---|
| gasto total da origem | igual |
| custo de cada pool em cada destino (compute) | igual em 41 de 41 pools |
| soma das categorias (o custo só muda de balde) | igual, diferença de 1 centavo de arredondamento |
| linha que tinha preço e perdeu | **0** |
| linha que sumiu da tabela | **0** |
| provedor recomendado | o mesmo em todas |

Mudaram de preço 15 linhas, todas explicadas: CloudWatch, Cloud Logging e Artifact Registry
passaram a ser cotados pela quantidade (antes, pela razão de taxas de outra categoria). Dois
casos ficaram mais caros no destino, e o novo valor é o certo pela regra que vale para tudo
(destino a preço de lista sobre a quantidade medida):
- Artifact Registry: a fatura mede 1.675 GiB-mês; o cliente paga 39% da lista na GCP; no ECR,
  pela lista, são US$ 167 (antes saía US$ 84, cotado como objeto);
- log entregue da GCP: 293 GiB que a franquia grátis zera na origem; o destino é cotado sem a
  franquia dele (premissa conservadora que já existia).

### 1.4 A assertividade subiu ou caiu?

**Subiu.** Cobertura de classificação (o quanto do custo tem categoria certa):

| fatura | antes | depois | alerta de cobertura baixa (< 95%) |
|---|---|---|---|
| Azure real | 63,5% | 73,7% | continua |
| AWS Cost Explorer | 91,8% | 95,7% | **some** |
| AWS real | 94,8% | 95,3% | **some** |
| GCP Cost Table | 98,7% | 99,8% | — |
| OCI real | 72,5% | 77,0% | continua |

Custo "não classificado": Azure 172,4 mil → 124,3 mil; OCI 81,5 mil → 68,0 mil; AWS Cost
Explorer 7,5 mil → 3,9 mil. Logs entregues da CloudWatch deixaram de ser cotados como egress.

### 1.5 A conexão entre os recursos aumentou?

**Aumentou.** "Conexão" aqui é o par serviço da origem → serviço do destino.

| fatura | serviços sem nenhum destino (antes → agora) | conexões com destino | conexões com destino e preço | conexões com risco conhecido |
|---|---|---|---|---|
| Azure real (36 serviços) | 8 → 3 | 108 → 131 | 55 → 69 | 0 → 49 |
| OCI real (21 serviços) | 11 → 1 | 50 → 87 | 34 → 40 | 0 → 44 |
| AWS Cost Explorer (39) | 3 → 2 | 218 → 223 | 131 → 141 | 0 → 165 |
| GCP Cost Table (20) | 3 → 2 | 97 → 104 | 70 → 74 | 0 → 63 |
| AWS real (11) | 0 → 0 | 57 → 58 | 42 → 49 | 0 → 46 |

Na Azure real, o custo de serviços sem preço em nenhum destino caiu de US$ 196,3 mil para
US$ 145,8 mil, e a cobertura de preços subiu de 57% para 67%.

### 1.6 O que ainda não está resolvido

- **A nota subiu onde não deveria.** Na fatura OCI real, a curadoria levou a nota dos destinos
  de ~28 para ~57, com todos eles 30% mais caros: a identificação e a evidência técnica
  melhoraram, e a fórmula soma isso como mérito. É o problema da M13 (criticidade 0, decisão
  com o responsável pelo MCP), agora mais visível. Na Azure real, a OCI foi de 85 para 89.
- Pendentes na curadoria: Kiro, Application Gateway e Azure Bastion (o DE-PARA não tem a
  linha) e "EC2 - Other" (nome genérico do Cost Explorer).
- Sem preço por falta de par um a um (declarado): Databricks, IA por token, Sentinel,
  Defender, API Management, Event Hubs, App Service, backup, serverless.
- No develop, a curadoria pelo Swagger dá 403 para todos: falta a variável `CURADORES` no
  deploy (repositório do infra). Esta curadoria foi feita pela mesma lógica da rota, no pod.
- Defeitos D1 a D4 do E2E (memória da série B da Azure, critério do empacotamento, confiança
  do banco, métrica de tipo inferido) continuam abertos.

## Parte 2 — Terraform de um serviço, de uma aplicação ou de tudo

### 2.1 Como estava

- **Tudo** e **aplicação** já existiam no backend desde o T5 (`escopo` no
  `POST /v1/terraform-generations`), mas um serviço escolhido levava as **aplicações
  inteiras** que o usam. Migrar só um serviço não era possível.
- A tela que permitia escolher (plano de migração) saiu do front em 17/10, e a aba
  Migração nova do front ainda diz "Em construção". O front **nunca teve** tela de gerar
  Terraform: a escolha precisa ser mandada pela aba nova (a do Luiz).
- **Defeito achado no teste de mesa, que vinha do T5:** em qualquer recorte (aplicação ou
  serviço), o pacote perdia os grupos de banco medidos e criava um banco genérico, **fora
  da rede nova e com endpoint público**. A relação de rede do banco quebrava em silêncio,
  só com um aviso genérico.

### 2.2 O que mudou

| peça | o que faz |
|---|---|
| `somente_os_servicos` (plano e Terraform) | o escopo é só o serviço: os recursos dele nas unidades que o usam, sem o resto da aplicação nem o grupo. Combinado com aplicação ou grupo, 422 com o motivo |
| `fronteira` | cada dependência que o recorte corta, pelas regras de rede do inventário no nível do recurso: sentido (o que migra chama o que fica, ou o contrário), natureza, portas, se é forte, e o texto do que quebra e do que fazer. Inclui recurso sem linha na fatura (o serviço pelo tipo) |
| avisos do pacote | no topo de `warnings`: o resumo, o CIDR do destino a liberar na origem, o banco fora da AWS sem regra espelhada (porta a liberar), e as primeiras oito ligações. Serviço escolhido que o pacote não cria vai em `unsupported_items` |
| README | seção "O que fica do outro lado (fronteira)", com todas as ligações e as fortes marcadas |
| recorte dos bancos | mantém os grupos de banco medidos na quantidade do escopo: o banco fica na subrede privada de dados, como no pacote da conta inteira |

Como a tela deve pedir: tudo = sem `escopo`; uma aplicação = `{"aplicacoes": ["checkout"]}`;
um serviço sozinho = `{"servicos": ["AmazonRDS"], "somente_os_servicos": true}`. O mesmo
corpo vale para a prévia (`POST …/migration-plans`).

### 2.3 Teste de mesa (fatura mock CUR + inventário mock da AWS)

O inventário mock tem, de propósito: auth, checkout e pagamentos dividindo banco; o
checkout chamando o catálogo; cada VM no próprio RDS; e um monitoramento que lê todos os
bancos.

| escolha | o pacote | fronteira | conferido contra o inventário |
|---|---|---|---|
| só o RDS | 8 bancos na subrede privada de dados, nenhuma VM | 21 ligações, todas fortes: 14 das VMs e do EKS de cada aplicação no próprio banco (checkout e pagamentos também no do auth) e 7 do monitoramento, uma por banco | sim: é o desenho do mock |
| só o EC2 | as VMs, nenhum banco | 46 ligações (40 fortes): cada VM no banco que fica, cada balanceador que fica encaminhando para a VM que migra | sim |
| aplicação checkout | VMs e bancos, como antes, agora com os 8 bancos na subrede de dados (antes: 1 banco genérico com endpoint público) | — (a aplicação leva o que vai junto) | sim |

O pacote "só o RDS" para a OCI e a GCP avisa, no segundo item de `warnings`, que o banco
fica na subrede de dados **sem** a regra de entrada espelhada (só a AWS anexa grupo ao
banco) e que é preciso liberar `tcp/5432` a partir da rede de origem. Isso é um limite que
continua (2.5), agora dito em voz alta.

### 2.4 `terraform validate`

Rodado com o Terraform 1.9.8 (`terraform init` com os providers e `terraform validate`):

| pacote | destinos | resultado |
|---|---|---|
| só a VM, payloads congelados das quatro origens | AWS→GCP, Azure→AWS, GCP→OCI, OCI→Azure | **válido nos 4** |
| só o RDS, pela API, com o inventário mock | GCP, Azure, OCI | **válido nos 3** |
| só o EC2, pela API | GCP, OCI | **válido nos 2** ¹ |
| aplicação checkout, pela API | OCI | **válido** |
| pacotes da conta inteira com topologia (testes que já existiam) | 12 pares | **válidos** |

¹ A Azure fica fora desse caso porque o banco de teste igual ao da CI não tem preço de VM
da Azure: nenhum pool tem SKU dela, e nem o pacote da conta inteira cria VM lá. As quatro
nuvens com VM estão cobertas pelos payloads congelados.

### 2.5 Comportamento antigo e o que continua aberto

- Regressão: suíte unitária inteira verde; integração no banco da CI com 213 passando e os
  mesmos 2 pulados de sempre. Sem `somente_os_servicos`, o plano e o pacote fazem o mesmo de
  antes (teste dedicado), com uma diferença de propósito: o recorte agora mantém os bancos na
  rede privada (antes caíam num banco genérico com endpoint público).
- **Ainda aberto:** fora da AWS, o banco do pacote não recebe a regra de entrada espelhada
  (é o desenho do espelho desde o RI15). O pacote avisa a porta a liberar, mas não cria a
  regra. Criar grupo de segurança para banco gerenciado na GCP, Azure e OCI é o próximo passo
  natural.
- **Ainda aberto:** na OCI, o banco PostgreSQL ou MySQL sai como Autonomous Database (motor
  Oracle), com o aviso de troca de motor que já existia.
- **Tela:** o backend aceita as três escolhas. Quem faz a aba Migração no front precisa mandar
  o corpo (2.2); não mexi no front, porque essa aba é do Luiz e está em construção.

## Resumo

| pedido | resultado |
|---|---|
| Três manutenções da tabela de equivalentes | feitas; nenhuma regressão; assertividade e conexão entre recursos subiram (Parte 1) |
| Curadoria pelo `/docs` | 19 aprovados, 4 recusados, 4 pendentes; o console do develop precisa da variável `CURADORES` |
| Terraform de um serviço, de uma aplicação ou de tudo | as três escolhas funcionam no backend, com a fronteira de rede dita na resposta, nos avisos e no README; pacotes válidos no `terraform validate`; um defeito de rede do banco, que vinha do T5, corrigido |
| O que continua | nota alta pela fórmula (M13), defeitos D1 a D4, regra de entrada do banco fora da AWS, a tela da aba Migração |

## Parte 3 — Depois do print de 18/10 ("Equivalente não mapeado" no RDS)

**Causa:** o RDS tem três equivalentes na Azure (SQL Database, MySQL e PostgreSQL) e três
na OCI (Oracle, MySQL e PostgreSQL). Sem um único equivalente, a rota de acoplamento
devolvia vazio, e a tela dizia "Equivalente não mapeado" com o preço ao lado. A fatura do
print não diz o motor (`InstanceUsage:db.t3.medium`), e a análise não tinha inventário.

**Correção (backend, develop `967c4d6`):**
- motor conhecido (export detalhado, ou a fatura com todo o banco de motor medido): o
  destino fica só com o equivalente do motor (RDS PostgreSQL → Azure Database for
  PostgreSQL e OCI Database with PostgreSQL); destino sem nenhum do motor no nome (Cloud
  SQL) fica com todos;
- motor desconhecido: a tela recebe as opções. A análise do print, sem refazer, já mostra
  "Azure SQL Database, Azure Database for MySQL ou Azure Database for PostgreSQL" e
  "Oracle Database Cloud Service, MySQL Database Service ou OCI Database with PostgreSQL".

**Tela de Terraform (front, develop `c357674`):** o diálogo "Gerar Terraform" da aba
Migração (PR #22, de 18/10) mandava os serviços sem `somente_os_servicos`, e o backend
levava as aplicações inteiras: o pacote tinha mais do que a lista dizia. Agora o pacote é
exatamente a lista, e os avisos da fronteira aparecem no resultado ("Antes de aplicar",
os cinco primeiros; o resto no README). Conferido com `tsc` num contêiner Node (o Node
local da máquina está quebrado).

Testes: suíte unitária e integração (213) verdes; CI do backend verde.
