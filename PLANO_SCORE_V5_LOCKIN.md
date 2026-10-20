# PLANO_SCORE_V5_LOCKIN — lock-in pelo inventário e pela classe no score

> Para o autor do PR #32 (score v4). Escrito em 16/10/2026. Fecha os itens V4.4 e
> V4.5 do lote da autoridade ([`PLANO_AUTORIDADE_V3_E_PRECISAO.md`](PLANO_AUTORIDADE_V3_E_PRECISAO.md)
> §2.1), que ficaram fora da v4 porque mexem no seu score.

## 1. Em uma linha

A regra já está pronta e testada em
[`app/domain/services/lock_in_efetivo.py`](app/domain/services/lock_in_efetivo.py).
Falta o score usá-la, numa política nova (`score-7rs-v5`) e numa autoridade nova
(`agent-authority-v5`). **Nada do v4 é desfeito:** o fator dependências (10%) e a
hipótese de adaptação pela classe (rehost 80, replatform 60, refactor 30) continuam
como estão.

## 2. O que o produto decidiu

- **V4.5, a exclusividade do inventário conta no lock-in** (decidido em 14/10,
  confirmado em 16/10). O catálogo dá uma nota só por serviço ("RDS: médio"), que vale
  igual para Postgres e Aurora; o inventário mostra o que o cliente usa. A sua
  preocupação continua na regra:
  - nada muda em silêncio: o fator mostra a fonte;
  - o catálogo não é sobrescrito;
  - "migra junto" não vira lock-in.
- **V4.4, a classe entra como hipótese, no seu formato:**
  - provisória, deixa a nota parcial;
  - nunca gravada no campo, nunca muda `classe_migracao` nem a 7R;
  - e só quando os serviços curados do mesmo tipo concordam.

**Por que não é rollback do #32:** hoje a exclusividade é calculada e aparece na tela
de acoplamento, mas nenhum fator do score a lê. O fator dependências lê o "migra
junto" (`topologia/servicos_do_billing._nivel`: banco compartilhado, grupo grande),
que é outra coisa. Não há contagem dupla, e o seu fator fica como está.

## 3. A regra (já na develop: use, não reimplemente)

`lock_in_da_origem(workload, entrada_do_inventario)` devolve
`LockInEfetivo(nivel, fonte, provisorio, motivo)`:

| evidência da origem | resultado |
|---|---|
| catálogo (`source_metadata.vendor_lock_in_level`) | o nível do catálogo, `fonte: catalogo` |
| o inventário acha recurso mais exclusivo que o catálogo (Aurora, DynamoDB) | sobe, `fonte: inventario` |
| o inventário só acha recurso menos exclusivo (Postgres) | desce só com `cobertura ≥ 0,9` e correlação por ID |
| sem catálogo, o inventário acha recurso proprietário | preenche, `fonte: inventario` |
| sem catálogo, o inventário só acha motor aberto | preenche "baixo" só com `cobertura ≥ 0,9` |
| inventário correlacionado pelo tipo (`cost_estimated`) | sobe com `provisorio: true`; nunca desce |
| sem catálogo e sem inventário, classe unânime (`migration.lock_in` com `fonte: classe`) | `fonte: classe`, `provisorio: true` |
| nada | `nivel: None` (desconhecido; nunca vira baixo) |
| tipo inferido | `nivel: None`, como em `technical_evidence` |

- `entrada_do_inventario` é a entrada do serviço em `inventory_services(payload)` (a sua
  função, que já traz `cost_estimated`). O `nivel` dessa entrada é o "migra junto" e
  não é lido.
- Classe mista (curados do tipo discordam) não tem `migration.lock_in`, só
  `migration.lock_in_faixa`. Exemplo: banco relacional, com RDS e Cloud SQL médios e
  Azure SQL alto. Esse caso não forma hipótese.

## 4. O que você faz

Branch `feat/score-v5-lockin` a partir da develop; PR para a develop (nunca para a main).

| # | onde | o quê |
|---|---|---|
| S1 | `technical_assessment.assess` | o lado da origem do lock-in passa a ser `lock_in_da_origem(workload, inventory.get(nome))`. Por destino, vale o pior entre a origem e a metadata do alvo, como `technical_evidence` faz hoje. A regra das alternativas (`usable`) e "vale a restrição mais forte" ficam iguais |
| S2 | idem | a amostra do fator lock-in sai `provisional` quando o nível que decidiu o par veio da classe ou de inventário estimado. A nota fica `partial`, como na sua hipótese de adaptação |
| S3 | `evidence` do fator | por serviço, `lock_in_fonte` (`catalogo`, `inventario`, `classe`) e `lock_in_motivo` |
| S4 | 7R | sem mudança: `classify_strategy` segue no catálogo. O seu `test_inventario_na_analise` (estratégias iguais com e sem inventário) continua valendo |
| S5 | `acoplamento_por_servico._linha` | um caminho `score-7rs-v5`, como você fez no v4: o inventário vai ao `assess` (hoje vai `{}`), e o `nivel` e a `fonte` da linha passam a ser os de `lock_in_da_origem(...)`. Assim a tela mostra o nível que o score usou, e o `nivel_sugerido` fica só para a arquitetura |
| S6 | versões | `DecisionSupport.policy_version` → `score-7rs-v5`. `AUTHORITY_VERSION` → `agent-authority-v5`, porque o `impose_decision` recalcula o score no envio e a mesma entrada daria outra análise corrigida. `lock_in_efetivo.py` entra em `_ARQUIVOS_DA_AUTORIDADE`; registre o hash novo e mantenha a entrada v4 |
| S7 | testes | `assess` com cada fonte; provisório e nota parcial; 7R igual; a linha da tela igual ao que o score usou |
| S8 | docs | `docs/decision-support.md` (regra do fator lock-in) e `PLANO_INVENTARIO_API.md` (o inventário passa a mexer no lock-in) |

## 5. Critérios de aceite

1. Sem inventário, o score muda só nos serviços sem catálogo cuja classe é unânime, e
   esses saem provisórios.
2. Com inventário, o lock-in muda só onde há recurso exclusivo (sobe) ou cobertura
   ≥ 0,9 por ID (desce).
3. As estratégias 7R são iguais às do v4, com e sem inventário.
4. Por serviço, o `nivel` da tela de acoplamento é o nível da origem que o score usou.
5. CI verde (Tests e Gates).

## 6. O que fazemos do nosso lado

| item | situação |
|---|---|
| Nomes da metadata curada alinhados ao DE-PARA (10 de 40 não chegavam à análise, nem na origem nem no destino); o seed sincroniza banco já semeado | feito, `990bed9` |
| Regra pura `lock_in_efetivo` (27 testes) | feito, `16e82b2` |
| Classe só quando os curados concordam; a tela sem "migra junto" no nível sugerido | feito, `8eabbe2` |
| Curadoria dos tipos sem nota (§8) e a transferência de dados ("Bandwidth" da Azure) no DE-PARA | **feito**: aprovada em 16/10, no seed; efeito no §8.1 |
| Rótulos `inventario` e `classe` na tela (front) | depois do S5 |
| Antes e depois nos seis relatórios e nos mocks com inventário; nota ao MCP (autoridade v5) | depois do seu merge |

Efeito já medido do nosso lado, sem LLM:
- o provedor escolhido não mudou em nenhum dos seis relatórios;
- a cobertura do fator lock-in foi de 2% para 82% na Azure (mock) e de 9% para 65% na
  OCI (mock);
- a classe mista deixou de sugerir "alto" para o Azure Database for PostgreSQL e para o
  DATABASE da OCI.

## 7. Onde o v5 deve mexer (para conferir a sua medição)

Sem inventário, a classe unânime entra como hipótese em:
- OCI (mock): COMPUTE, médio, US$ 1.725;
- GCP BigQuery (mock): Networking, baixo, US$ 1.441;
- GCP: Filestore, Monitoring e DNS, baixo, cerca de US$ 115.

Na AWS nada muda: os 13 serviços sem nota não têm classe. Se a curadoria do §8 entrar
antes, a maior parte disso vira fato.

Com inventário (medido em 16/10, sem LLM):
- CUR (mock) + AWS Config: nada muda. RDS: Aurora dá médio, igual ao catálogo.
  ElastiCache: Redis aberto, mas cobertura de 76%, abaixo dos 90%.
- OCI (mock) + inventário: o DATABASE (US$ 1.000) passa de desconhecido para médio
  (Oracle, cobertura 100%).
- BigQuery (mock) + Asset Inventory e Cost Explorer (real) + inventário AWS: nenhum
  recurso exclusivo achado.

## 8. Anexo: curadoria (aprovada em 16/10)

Serviços dos seis relatórios que estavam sem lock-in curado, com o valor aprovado em
16/10. Estão em `seed_service_metadata.py` com o nome do DE-PARA.

| serviço (nome do DE-PARA) | tipo | custo/mês no relatório | lock-in | refatoração | por quê |
|---|---|---|---|---|---|
| AWS WAF | web_application_firewall | AWS 2.795 | médio | não | regras e grupos gerenciados próprios: reescreve a configuração, não a aplicação |
| Amazon MSK | stream_ingestion | AWS 2.503 | baixo | não | Kafka gerenciado; o protocolo é aberto (Event Hubs e OCI Streaming falam Kafka) |
| Amazon Translate | translation | AWS 708 | médio | sim | API e SDK próprios |
| Amazon Managed Workflows for Apache Airflow (MWAA) | managed_airflow | AWS 365 | baixo | não | Airflow aberto; os DAGs são portáveis |
| Amazon Simple Queue Service (SQS) | queueing | AWS 260 | médio | sim | API própria: muda o cliente |
| Amazon Elastic Container Registry (ECR) | container_registry | AWS 196 | baixo | não | imagem OCI padrão |
| Amazon Comprehend | natural_language_processing | AWS 191 | médio | sim | API e modelos próprios |
| AWS Directory Service | directory_services | AWS 128 | baixo | não | Active Directory e LDAP padrão |
| AWS Key Management Service (KMS) | key_management | AWS 16 | médio | sim | a chave não sai do serviço: recriptografar; SDK próprio |
| AWS Config | policy_enforcement | AWS 1 | médio | não | regras próprias: reescreve a política |
| AWS Secrets Manager | secrets_management | AWS 1 | médio | não | API própria, troca simples |
| AWS Security Hub | security_command_center | AWS 0 | médio | não | achados e integrações próprios |
| AWS Cost Explorer | cost_management | AWS 0 | baixo | não | ferramenta de custo, sem dado a migrar |
| Cloud Run | container_instances | GCP 94 | baixo | não | contêiner padrão |
| Filestore | file_storage | GCP 63 | baixo | não | NFS, como o EFS já curado |
| Artifact Registry | container_registry | GCP 57 | baixo | não | imagem OCI padrão |
| Cloud Monitoring | monitoring | GCP 51 | baixo | não | como o CloudWatch já curado |
| Cloud Scheduler | cron_scheduler | GCP 5 | baixo | não | cron |
| Cloud DNS | dns | GCP 1 | baixo | não | como o Route 53 já curado |
| Secret Manager | secrets_management | GCP 1 | médio | não | como o Secrets Manager |
| Cloud Logging | logging | GCP 0 | baixo | não | como o CloudWatch |
| Azure Database for PostgreSQL | managed_relational_database | Azure 814 | médio | sim | motor aberto gerenciado, como o RDS e o Cloud SQL já curados |
| Azure Database for MySQL | managed_relational_database | — | médio | sim | idem |
| Oracle Cloud Infrastructure Compute | virtual_server | OCI 1.725 | médio | não | como EC2, VMs da Azure e Compute Engine |
| MySQL Database Service | managed_relational_database | — | médio | sim | como o RDS |
| Oracle Database Cloud Service | managed_relational_database | OCI 1.000 | médio | sim | motor Oracle: licença e edição limitam o destino, mas ele roda na Azure, no GCP e na AWS (Oracle Database@). Coerente com a regra de exclusividade do inventário. Ver a pergunta abaixo |

**Pergunta do Oracle.** O DATABASE da OCI com uso Autonomous cai em "Oracle Database
Cloud Service" (`servico_pelo_uso`), que não tem nota. A metadata já curada "Oracle
Autonomous Database" (alto) não tem linha no DE-PARA e nunca chega à análise. A
sugestão é trocar aquela metadata por esta linha, com médio.

**Coerência entre o catálogo e o inventário.** A regra de exclusividade
(`topologia/exclusividade.py`) dá médio a Oracle e a SQL Server, pela licença. Com
inventário cobrindo 90% do custo por ID, a regra do lote v4 desce um "alto" do catálogo
para esse médio. Hoje isso alcançaria o Azure SQL Database (alto no catálogo). Se o
produto quiser o alto para PaaS, o caminho é uma regra de exclusividade própria (tipo
nativo do Azure SQL ou do Autonomous = alto), não deixar o catálogo e o inventário
discordarem.

### 8.1 Aprovada e aplicada (16/10)

O usuário aprovou a tabela, a troca do Oracle e a linha "Bandwidth" da Azure no DE-PARA.

- **Seed da metadata:**
  - as 25 linhas da tabela, com o motivo em `notes`;
  - o "Oracle Autonomous Database" é renomeado para "Oracle Database Cloud Service",
    com nota média (`RENOMEADOS`, que também corrige banco já semeado).
- **DE-PARA:** um tipo novo, `data_transfer`, com uma linha por nuvem:
  - AWS Data Transfer;
  - Bandwidth (Azure);
  - Network Service Tiers (GCP);
  - OCI Outbound Data Transfer.

  As quatro têm lock-in baixo, sem refatoração: tráfego cobrado por GB, sem dado a
  migrar. A cotação não muda: as linhas dessas faturas já são cotadas pela categoria
  de rede.
- **Para o develop:** rodar no pod, nesta ordem, `seed_service_metadata` e depois
  `seed_from_csv`.

Efeito nos seis relatórios, sem LLM:

| relatório | serviços sem nível na origem | nota do escolhido (OCI) | o que mais mudou |
|---|---|---|---|
| AWS Cost Explorer (real) | 13 → 0 de 24 | 87,50 → 89,78 | lock-in de Azure e GCP com cobertura 100% |
| GCP Cost Table (real) | 9 → 1 de 14 (Networking, mistura por desenho) | 90,68 → 93,92 | — |
| GCP BigQuery (mock) | 9 → 1 de 14 | 93,43 → 95,69 | — |
| AWS CUR (mock) | 13 → 0 de 24 | 85,21 → 87,81 | — |
| Azure (mock) | 2 → 0 de 5 | 92,42 → 95,61 | — |
| OCI São Paulo (mock) | 3 → 1 de 6 (NETWORK, mistura por desenho) | sem escolhido (igual) | lock-in da Azure 57,43 → 38,45: o DATABASE (Oracle, médio) contra o Azure SQL (alto) vale o pior dos dois |

O que não mudou:
- o provedor escolhido;
- o custo e a economia (`medir`, idêntico);
- as análises antigas, porque o payload é congelado.

**Por que a OCI sobe como destino:** o "Oracle Cloud Infrastructure Compute" ganhou nota.
Antes, o par EC2 → OCI caía na hipótese preliminar ("rehost"). Agora segue a regra do
catálogo, como Azure e GCP: o EC2 é médio, então "replatform".

**7R por serviço:**
- serviços que eram "a confirmar" ganharam estratégia (25 pares no bill AWS):
  - "refactor": SQS, KMS, Translate, Comprehend;
  - "replatform": WAF, Config, Secrets Manager, Security Hub;
  - "rehost": MSK, Directory Service, Cost Explorer;
- o compute com destino OCI passou de "rehost" para "replatform".

**Gate ajustado:** `test_classe_nao_e_inventada_sem_exemplo` usava o AWS WAF como
exemplo de tipo sem curado. Agora confere o invariante em todos os serviços dos dois
bills: classe só aparece em tipo com exemplo curado.
