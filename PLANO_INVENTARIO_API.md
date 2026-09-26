# Plano: inventário via API do provedor (topologia, dependências e precisão)

**Base:** decisão de 26/09/2026. O billing detalhado (CUR/BigQuery) responde o
DE-PARA de custo e capacidade, e já está pronto ([`PLANO_CUR_BIGQUERY.md`](PLANO_CUR_BIGQUERY.md)).
A API do provedor responde **o que existe e como está ligado**: subrede, grupo
de segurança, disco anexado, banco com versão, o que está exposto. É o que o
Terraform e o plano de migração precisam e o billing nunca teve.

**Dado real:** ainda não temos. Usamos mocks de inventário gerados a partir dos
**mesmos recursos dos mocks de billing**, com os mesmos IDs. Os mocks são
tratados como código: um script determinístico gera os arquivos e os
invariantes têm teste.

**Regras que continuam valendo:**

- O inventário é **opcional e aditivo**. Sem inventário, billing, análise e
  Terraform produzem exatamente o que produzem hoje.
- Com inventário, a análise **não muda custo, economia nem recomendação**
  (RI14). O inventário acrescenta estrutura, risco e fatos medidos para o
  Terraform. Cotar recurso a recurso a partir do inventário é outro plano.
- A plataforma **não recebe, não guarda e não usa credencial de nuvem** nesta
  entrega (decisão D1).

> ## Status
>
> | etapa | estado |
> |---|---|
> | I0 — versão preservada (`baseline/pre-inventario-topologia`) + este plano | ✅ |
> | I1 — domínio da topologia + algoritmos de grafo | ⏳ |
> | I2 — normalizadores (AWS Config, GCP Asset Inventory, Azure Resource Graph, canônico) | ⏳ |
> | I3 — mocks de inventário coerentes com os de billing | ⏳ |
> | I4 — análise de topologia (unidades, dependências, ondas, criticidade, riscos) | ⏳ |
> | I5 — persistência + API `/v1/inventories` | ⏳ |
> | I6 — correlação billing × inventário | ⏳ |
> | I7 — integração com a análise | ⏳ |
> | I8 — Terraform consumindo a topologia | ⏳ |
> | I9 — E2E pela API + auditoria de coerência | ⏳ |
> | I10 — coleta ativa pela plataforma (bloqueada pela decisão D1) | ⛔ |

---

## 0. Vocabulário

| termo | significado aqui |
|---|---|
| **recurso** | uma coisa que existe na nuvem de origem: VM, disco, subrede, grupo de segurança, banco… |
| **relação** | ligação declarada na configuração: "o disco está anexado à VM", "a VM está na subrede" |
| **dependência** | "A precisa falar com B para funcionar", deduzida de regra de rede ou declarada pelo cliente |
| **unidade** | o menor conjunto que migra junto: a VM com seus discos, interfaces e IPs (ou a aplicação inteira, quando há tag de aplicação) |
| **grupo de movimentação** | unidades com acoplamento forte (a aplicação e o banco exclusivo dela, o balanceador e as VMs atrás dele) |
| **onda** | lote de grupos que pode migrar ao mesmo tempo sem quebrar dependência |
| **fundação** | rede, subredes, grupos de segurança e gateways: precisam existir no destino antes de qualquer onda |
| **criticidade declarada** | o que o cliente disse (pela API ou por uma tag de criticidade explícita) |
| **criticidade efetiva** | a declarada, mais a herdada de quem depende do recurso |

---

## 1. Pré-requisitos

### Já atendidos

| # | pré-requisito | onde |
|---|---|---|
| P1 | billing detalhado com `resource_id` por linha nas 4 nuvens | E1–E10 do `PLANO_CUR_BIGQUERY.md` |
| P2 | mocks de billing determinísticos, com os recursos acessíveis ao gerador | `scripts/gerar_mocks_billing.py` |
| P3 | inventário medido pelo billing por pool (`InventarioDoPool.recursos`) e por banco (`BancoMedido.resource_id`) | `domain/entities/inventario_medido.py` |
| P4 | Postgres local para os testes de integração | podman `ost-pg` (ver `docs/dev-setup.md`) |
| P5 | `terraform` 1.9.8 + cache de providers para validar os pacotes | `TF_PLUGIN_CACHE_DIR` |

### Decisões

| # | decisão | adotado nesta entrega | bloqueia |
|---|---|---|---|
| **D1** | **Quem chama a API do provedor.** (a) O cliente roda o comando oficial de exportação na conta dele e sobe o arquivo. (b) Papel delegado: a plataforma assume um papel só-leitura criado pelo cliente (AWS AssumeRole com ExternalId, impersonação de service account no GCP, Lighthouse ou app multi-tenant na Azure, política cross-tenancy na OCI). (c) O cliente entrega uma chave, que a plataforma guarda cifrada. | **(a)**. É a mesma API do provedor (Config, Asset Inventory, Resource Graph): muda quem executa. Funciona hoje, sem revisão de segurança do cliente e sem credencial em trânsito. | só a I10 |
| D2 | Escala de criticidade | `alta`, `media`, `baixa`, com vocabulário de tag fechado (RI10) | nada (ajustável) |
| D3 | Ordem das ondas | **dependências primeiro** (quem é chamado migra antes de quem chama), com acoplamento forte migrando junto (RI7–RI8) | nada (ajustável) |

**Recomendação para a I10:** (b). Não guarda segredo do cliente, e o cliente
revoga apagando o papel. (c) só faz sentido se o cliente exigir, e aí o
`cloud_creds_fernet_key` do `config.py` é o ponto de partida, somado a rotação,
auditoria de cada consulta e política mínima documentada.

**Permissão mínima** que o cliente precisa para gerar os arquivos (só leitura):
AWS `config:DeliverConfigSnapshot` + leitura do bucket do canal (ou
`config:SelectResourceConfig`); GCP `roles/cloudasset.viewer`; Azure `Reader`
nas assinaturas; OCI `inspect all-resources` e `read` nos serviços exportados.

---

## 2. Fontes verificadas

O inventário vem da mesma API do provedor, exportada pelo próprio cliente.
Cada formato foi conferido na documentação oficial:

### AWS Config — snapshot de configuração

- **Comando:** `aws configservice deliver-config-snapshot --delivery-channel-name <canal>`,
  que entrega um `.json.gz` no bucket S3 do canal, um por conta e região.
  Exige o Config gravando nessas contas e regiões (Control Tower e Security Hub
  já ligam).
  [Exemplo oficial](https://docs.aws.amazon.com/config/latest/developerguide/example-s3-snapshot.html) ·
  [consultas de relação](https://docs.aws.amazon.com/config/latest/developerguide/examplerelationshipqueries.html)
- **Estrutura:** `{"fileVersion", "configSnapshotId", "configurationItems": [...]}`.
  Cada item tem `resourceId`, `resourceType` (`AWS::EC2::Instance`), `arn`,
  `awsRegion`, `accountId`, `availabilityZone` (e a grafia antiga
  `availibilityZone`, que o exemplo oficial usa), `resourceName`, `tags`
  (objeto), `configurationItemStatus`, `configurationItemCaptureTime`,
  `configuration` (objeto — ou **string JSON** quando vem da API
  `get-resource-config-history`) e `relationships` (`resourceId`,
  `resourceType`, `name` — ou `relationshipName` na API).
- **Relações** vêm de duas formas: da lista `relationships` ("Is contained in
  Subnet", "Attached to Instance") e dos campos de `configuration`
  (`subnetId`, `securityGroups`, `blockDeviceMappings`, `attachments`). Os
  campos de configuração são a fonte primária: são estruturados. O texto do
  `name` varia e não é usado para decidir o tipo da relação (RI3).
- **Regras de rede:** `AWS::EC2::SecurityGroup` → `configuration.ipPermissions`
  (e `ipPermissionsEgress`) com `ipProtocol` (`-1` = todos), `fromPort`,
  `toPort`, `ipRanges`/`ipv4Ranges[].cidrIp`, `ipv6Ranges[].cidrIpv6`,
  `prefixListIds` e `userIdGroupPairs[].groupId` (referência a outro grupo,
  com `userId` quando é de outra conta).
- **Alternativa aceita:** saída de `aws configservice select-resource-config`
  (`{"Results": ["<item em JSON>", ...]}`).
- **Não vem no Config:** alvos registrados em target group de ALB/NLB (só no
  `DescribeTargetHealth`) e snapshots de EBS. A ligação balanceador → aplicação
  aparece pela regra de grupo de segurança (a aplicação libera o grupo do
  balanceador), que é o padrão da AWS.

### GCP — Cloud Asset Inventory

- **Comando:** `gcloud asset export --project=<p> --content-type=resource --output-path=gs://<bucket>/inventario.json`
  (NDJSON, um asset por linha) ou `gcloud asset list --project=<p> --content-type=resource --format=json`
  (array JSON).
  [Exportação](https://docs.cloud.google.com/asset-inventory/docs/export-cloud-storage) ·
  [Asset](https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/Asset)
- **Estrutura:** `name` (`//compute.googleapis.com/projects/p/zones/z/instances/vm-1`),
  `assetType`, `ancestors`, `updateTime`, `resource.{version, discoveryName,
  parent, data, location}`. O export para Cloud Storage usa os nomes do proto
  (`asset_type`, `update_time`), e o `gcloud asset list` usa camelCase.
  **Aceitamos os dois**: o normalizador lê as duas grafias só nas chaves de
  topo. O `resource.data` é sempre o recurso REST do serviço, em camelCase.
- **Relações** vêm de dentro do `data`: `networkInterfaces[].subnetwork` e
  `.network`, `networkInterfaces[].accessConfigs[].natIP` (IP externo),
  `disks[].source` e `disks[].boot`, `tags.items` e `serviceAccounts[].email`
  (alvos de firewall), `users` (disco → VM), `labels` (nó de GKE),
  `settings.ipConfiguration.privateNetwork` (Cloud SQL). São self-links
  (`https://www.googleapis.com/compute/v1/projects/...`), convertidos para o
  nome do asset.
- **Regras de rede:** `compute.googleapis.com/Firewall` → `network`,
  `direction`, `priority`, `sourceRanges`, `sourceTags`,
  `sourceServiceAccounts`, `targetTags`, `targetServiceAccounts`, `allowed[]`,
  `denied[]`, `disabled`.
- **Armadilha de correlação:** no billing export, `resource.global_name` usa o
  **ID numérico** da VM (`.../instances/1234567890`), e o asset usa o **nome**
  (`.../instances/vm-1`). O normalizador gera os dois apelidos (RI13). O mock de
  billing já reproduz o ID numérico.
- **Não vem no asset:** quais VMs estão atrás de um balanceador. O tráfego do
  balanceador do Google chega das faixas do próprio Google, e a associação de
  instance group não-gerenciado não está no `data`. Balanceador GCP aparece
  como exposição, sem dependência inferida; o cliente declara se quiser.

### Azure — Resource Graph

- **Comando:** `az graph query -q "Resources | project id, name, type, location, resourceGroup, subscriptionId, tags, properties, sku, kind, zones, managedBy" --first 1000`,
  paginando com `--skip-token`. Saída: `{"count", "data": [...], "skip_token", "total_records"}`.
  Aceitamos também a lista pura. Cada página vira um arquivo.
  [Paginação](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/work-with-data)
- **Estrutura:** a `properties` de cada tipo é a do ARM:
  VM (`hardwareProfile.vmSize`, `storageProfile.osDisk.managedDisk.id`,
  `storageProfile.dataDisks[].managedDisk.id`, `networkProfile.networkInterfaces[].id`,
  `extended.instanceView.powerState.code`), NIC (`ipConfigurations[].properties.subnet.id`,
  `.privateIPAddress`, `.publicIPAddress.id`, `.applicationSecurityGroups[].id`,
  `networkSecurityGroup.id`, `virtualMachine.id`), VNet
  (`addressSpace.addressPrefixes`, `subnets[]` embutidas com `addressPrefix` e
  `networkSecurityGroup.id`), NSG (`securityRules[]`: `direction`, `access`,
  `protocol`, `priority`, `sourceAddressPrefix(es)`, `sourceApplicationSecurityGroups`,
  `destinationPortRange(s)`, `destinationApplicationSecurityGroups`), disco
  (`diskSizeGB`, `diskState`, `osType`, `sku.name`, `managedBy`), PostgreSQL e
  MySQL Flexible (`version`, `sku.name`, `sku.tier`, `storage.storageSizeGB`,
  `highAvailability.mode`, `network.delegatedSubnetResourceId`,
  `network.publicNetworkAccess`), Load Balancer (`frontendIPConfigurations`,
  `backendAddressPools[].properties.backendIPConfigurations[].id`).
- **IDs:** o ARM id em minúsculas, igual ao que o parser de billing grava. Um id
  de sub-recurso (`.../networkInterfaces/nic1/ipConfigurations/ip1`) é reduzido
  ao recurso-pai.

### OCI e qualquer outra origem — formato canônico `oneshift-inventory-v1`

A busca da OCI (`oci search resource structured-search`) devolve resumos sem
anexos nem regras, o que não basta para topologia. Para a OCI, e para qualquer
origem sem formato nativo (CMDB, script do cliente, outra nuvem), aceitamos um
JSON documentado:

```json
{
  "formato": "oneshift-inventory-v1",
  "provedor": "oci",
  "capturado_em": "2026-03-31T12:00:00Z",
  "recursos": [
    {"id": "ocid1.instance.oc1..a", "tipo": "instancia", "tipo_nativo": "Instance",
     "nome": "app-1", "conta": "ocid1.tenancy.oc1..t", "regiao": "sa-saopaulo-1",
     "zona": "AD-1", "estado": "ativo",
     "atributos": {"tipo_maquina": "VM.Standard.E4.Flex", "vcpus": 4, "memoria_gib": 64,
                   "ip_privado": "10.0.1.5"},
     "tags": {"app": "checkout"}}
  ],
  "relacoes": [{"de": "ocid1.instance.oc1..a", "para": "ocid1.subnet.oc1..s", "tipo": "contido_em"}]
}
```

As regras de um grupo vão em `atributos.regras` do próprio grupo, no formato da
RI4. O tipo e a relação usam o vocabulário canônico da RI2 e da RI3; valor fora
do vocabulário é recusado com a posição do erro.

### O que nenhuma dessas fontes dá

- **Tráfego real.** A regra de rede diz quem *pode* falar com quem, não quem
  fala. Dependência real exige VPC Flow Logs, fora do escopo.
- **Criticidade, SLA, janela de manutenção.** Só o cliente sabe (RI10).
- **Dependências por DNS, fila ou API externa.** Só por declaração (RI5).

### O que isto fecha das lacunas da E10

| lacuna da [auditoria E10](docs/auditoria-e2e-2026-09-26-exports-detalhados.md) | com o inventário |
|---|---|
| Banco da Azure sem classe | **fecha**: `sku.name` e `sku.tier` do Flexible Server (RI15.6) |
| OCI sem shape por instância | **fecha para o Terraform**: OCPU e memória por instância no posicionamento; a capacidade do pool continua a do billing (neutralidade) |
| Azure: RAM estimada | **não fecha pelo Resource Graph**, que só traz `vmSize`. A RAM continua deduzida do nome. Fecha pelo formato canônico com `memoria_gib` |
| Versão do banco em branco no Terraform (AWS, GCP, Azure) | **fecha** (RI15.6) |
| Disco raiz criado duas vezes no pacote | **fecha** (RI15.7) |

---

## 3. Requisitos

### Funcionais

| # | requisito |
|---|---|
| RF1 | Receber um ou mais arquivos de inventário (JSON, NDJSON, `.gz`, `.zip`) numa requisição e detectar o formato **pelo conteúdo** |
| RF2 | Normalizar para o modelo canônico: recursos, relações e regras de rede, sem perder o que não foi modelado (contado por tipo nativo) |
| RF3 | Processar em segundo plano, com estado `QUEUED → PROCESSING → DONE / FAILED` e erro legível |
| RF4 | Listar inventários, consultar um, listar recursos paginados com filtro por tipo, e apagar |
| RF5 | Calcular a topologia: unidades, dependências, grupos de movimentação, ondas, dependências entre ondas, criticidade efetiva, riscos e recursos ociosos |
| RF6 | Receber declarações do cliente: criticidade (por recurso ou aplicação), chave de tag da aplicação e do ambiente, dependências que a configuração não mostra |
| RF7 | Correlacionar com um billing do mesmo provedor: cobertura de custo, lacunas de escopo e recursos sem cobrança |
| RF8 | Usar o inventário numa análise, quando o cliente pedir, congelando a topologia no payload da análise |
| RF9 | Usar a topologia congelada no Terraform: subredes espelhadas, grupos de segurança traduzidos, posicionamento de VMs e bancos, versão de banco medida, discos de boot e discos sem anexo tratados |

### Não funcionais

| # | requisito |
|---|---|
| RNF1 | **Determinismo:** mesma entrada e mesmas declarações produzem a mesma topologia, byte a byte, com `fingerprint` SHA-256 |
| RNF2 | **Isolamento:** `user_id` em toda tabela e consulta; recurso de outro usuário responde 404 |
| RNF3 | **Sem credencial:** nenhum campo de entrada aceita chave, token ou senha |
| RNF4 | **Limites e memória:** até 20 arquivos de até `max_upload_size_mb` (50 MB) por envio, gravados em disco em blocos (sem ler o arquivo inteiro na memória da requisição). JSON de documento único até 100 MB descompactado (é lido inteiro); NDJSON lido em streaming. Até 1 GB descompactado por envio e 200 mil recursos por inventário. Passar do limite dá `FAILED` com o motivo, não queda do processo |
| RNF5 | **Custo computacional:** normalização O(n); grafo O(V+E) com teto de arestas por regra (RI5) |
| RNF6 | **Dados sensíveis:** o arquivo bruto é apagado depois da ingestão; nada do inventário bruto vai ao LLM, só o resumo agregado; log sem conteúdo |
| RNF7 | **Compatibilidade:** contratos atuais intactos; campos novos são opcionais |
| RNF8 | **Interruptor:** `INVENTORY_ENABLED` (default `false`), no padrão do `TERRAFORM_GENERATION_ENABLED`: desligado, as rotas respondem 404 e `inventory_id` na análise é recusado |

---

## 4. Funcionalidades (API)

Todas em `/v1/inventories`, `kebab-case`, sem verbo na URL, envelope de erro
`{codigo, mensagem, retryable}` igual ao das rotas de Terraform.

| método e rota | o que faz | respostas |
|---|---|---|
| `POST /v1/inventories` | multipart com `files` (1 a 20). Cria o inventário `QUEUED` e processa em BackgroundTask | 202 `{inventory_id, status}` · 413 · 415 · 422 |
| `GET /v1/inventories` | lista os do usuário, mais recentes primeiro | 200 |
| `GET /v1/inventories/{id}` | estado, formato detectado, provedor, `capturado_em`, contagens por tipo, tipos nativos não modelados, arquivos, erro, `fingerprint` | 200 · 404 |
| `GET /v1/inventories/{id}/resources?kind=&limit=&offset=` | recursos normalizados, paginados | 200 · 404 · 409 (não processado) |
| `GET /v1/inventories/{id}/topology` | a análise da RI5–RI12, calculada na hora com as declarações vigentes. Listas longas saem cortadas em 500 itens, com o total | 200 · 404 · 409 |
| `PUT /v1/inventories/{id}/declarations` | substitui as declarações. Idempotente. Chave de recurso que não existe no inventário → 422 com a lista | 200 · 404 · 409 · 422 |
| `GET /v1/inventories/{id}/correlation?report_id=` | cobertura contra um billing do mesmo provedor (RI13) | 200 · 404 · 409 · 422 |
| `DELETE /v1/inventories/{id}` | apaga o inventário e tudo que deriva dele | 204 · 404 |

**Declarações** (corpo do `PUT`):

```json
{
  "chave_aplicacao": "app",
  "chave_ambiente": null,
  "criticidade": {
    "recursos": {"arn:aws:rds:us-east-1:222222222222:db:pagamentos-1a2b3c": "alta"},
    "aplicacoes": {"checkout": "alta", "relatorios": "baixa"}
  },
  "dependencias": [
    {"de": "aplicacao:checkout", "para": "aplicacao:pagamentos",
     "acoplamento": "fraco", "motivo": "chamada HTTP por DNS interno"}
  ],
  "ferramentas": ["aplicacao:monitoramento"]
}
```

`null` = detectar automaticamente; `""` = desligar. `de`/`para` aceitam a chave
de um recurso ou `aplicacao:<valor da tag>`. Dependência entre aplicações vale
entre unidades do mesmo escopo (conta, região, ambiente): o `checkout` de dev
não passa a depender do `pagamentos` de produção. `ferramentas` (lista no
mesmo formato) marca unidades como ferramenta de administração (RI7). Limites:
5.000 entradas por lista, 300 caracteres por `motivo`.

E duas extensões de rotas existentes, ambas opcionais:

- `POST /v1/analyses/start/{report_id}` aceita `inventory_id` no corpo, junto
  do `decision_context` que já existe (RI14).
- `GET /v1/analyses/{report_id}/request?inventory_id=` recalcula o payload com
  a topologia, antes de rodar a análise.

O `POST /v1/terraform-generations` **não muda de contrato**: usa a topologia que
a análise congelou (RI15).

---

## 5. Regras de negócio

### RI1 — Identidade, deduplicação e estado

- **Chave canônica** do recurso:
  - AWS: o `arn`. Sem ARN: `aws:<conta>:<região>:<resourceType>:<resourceId>`.
  - GCP: o `name` do asset.
  - Azure: o `id` do ARM em minúsculas.
  - Canônico: o `id` informado, sem espaços nas pontas.
- **Resolução de referência na AWS.** A relação do Config e os campos de
  configuração citam `resourceId` e tipo, sem conta nem região. Resolve-se na
  mesma conta e região do item que cita; depois, pelo par (tipo, id) quando ele
  é único no inventário. Referência a grupo de outra conta usa o `userId` da
  regra.
- **O mesmo recurso em dois arquivos** (duas páginas, dois snapshots) fica com
  a captura mais recente. Em empate, fica o do arquivo que vem depois na ordem
  do nome do arquivo, para ser determinístico. As duplicatas são contadas.
- **Item removido** (AWS `ResourceDeleted` e `ResourceDeletedNotRecorded`) não
  entra: o recurso não existe mais.
- **Estado normalizado:** `ativo`, `parado`, `disponivel` (disco ou IP sem
  anexo), `desconhecido`. Tabela por provedor no normalizador.
- **Referência para fora do inventário** (a VM aponta para uma subrede que não
  veio no arquivo) é descartada e contada por tipo. Não se cria recurso
  fantasma.

### RI2 — Tipos canônicos

| tipo | AWS | GCP | Azure | papel |
|---|---|---|---|---|
| `instancia` | `EC2::Instance` | `compute/Instance` | `compute/virtualmachines` | unidade |
| `banco` | `RDS::DBInstance` | `sqladmin/Instance` | `dbforpostgresql/flexibleservers`, `dbformysql/flexibleservers`, `sql/servers/databases` | unidade |
| `cache` | `ElastiCache::CacheCluster` | `redis/Instance` | `cache/redis` | unidade |
| `balanceador` | `ElasticLoadBalancingV2::LoadBalancer`, `ElasticLoadBalancing::LoadBalancer` | `compute/ForwardingRule`, `compute/GlobalForwardingRule` | `network/loadbalancers`, `network/applicationgateways` | unidade |
| `bucket` | `S3::Bucket` | `storage/Bucket` | `storage/storageaccounts` | unidade |
| `cluster_kubernetes` | `EKS::Cluster` | `container/Cluster` | `containerservice/managedclusters` | unidade |
| `disco` | `EC2::Volume` | `compute/Disk` | `compute/disks` | anexo |
| `interface_rede` | `EC2::NetworkInterface` | (embutida na VM) | `network/networkinterfaces` | anexo |
| `ip_publico` | `EC2::EIP` | `compute/Address` (externo) | `network/publicipaddresses` | anexo |
| `snapshot` | — | `compute/Snapshot` | `compute/snapshots` | backup |
| `rede` | `EC2::VPC` | `compute/Network` | `network/virtualnetworks` | fundação |
| `subrede` | `EC2::Subnet` | `compute/Subnetwork` | (embutida na VNet) | fundação |
| `grupo_seguranca` | `EC2::SecurityGroup` | (sintético, por alvo de firewall) | `network/networksecuritygroups`, `network/applicationsecuritygroups` | fundação |
| `gateway` | `EC2::NatGateway`, `InternetGateway`, `VPNGateway`, `TransitGateway` | `compute/Router`, `compute/VpnGateway` | `network/natgateways`, `network/virtualnetworkgateways` | fundação |
| `outro` | o resto (inclusive `RDS::DBCluster`) | o resto | o resto | contado; serve de conector (RI6), fora das ondas |

### RI3 — Relações canônicas

Direção sempre **A → B**:

| relação | de → para | efeito |
|---|---|---|
| `contido_em` | subrede → rede; instância, interface, banco, cache, balanceador → subrede | posicionamento |
| `protegido_por` | instância, interface, banco, cache, balanceador, subrede → grupo de segurança | quem o grupo protege |
| `anexado_a` | disco, interface → instância; IP → instância, interface, balanceador | junta na mesma unidade |
| `membro_de` | instância de banco → cluster de banco; nó → cluster Kubernetes | junta na mesma unidade |
| `encaminha_para` | balanceador → instância ou interface | dependência forte |
| `snapshot_de` | snapshot → disco | backup |

- **Campos de configuração primeiro.** Cada normalizador deriva as relações do
  que é estruturado: `subnetId`, `securityGroups`, `blockDeviceMappings`,
  `attachments`, `dBClusterIdentifier` (→ `membro_de`), a tag
  `eks:cluster-name` e o label `goog-k8s-cluster-name` (nó → cluster).
- **Relação declarada pelo provedor** (a lista `relationships` da AWS) só
  complementa. O tipo sai do **par de tipos canônicos**, orientado pela tabela
  (disco + instância = disco `anexado_a` instância, venha de qual lado vier),
  nunca do texto do provedor. Par fora da tabela é descartado e contado.
- Relação repetida (mesmo de, para e tipo) fica uma só.

### RI4 — Regras de rede e membros de um grupo

Regra canônica: `grupo` (a quem ela se aplica), `direcao` (`entrada`/`saida`),
`acao` (`permitir`/`negar`), `protocolo` (`tcp`, `udp`, `icmp`, `todos` ou o
número), `portas` (`[inicio, fim]` ou nulo para todas), `origem` e, opcional,
`destino`. A origem e o destino são **seletores**: `cidr`, `grupo`, `qualquer`
(internet) ou `servico` (rótulo do provedor, como `VirtualNetwork`).

- **AWS:** uma regra canônica por origem de cada `ipPermissions`. `ipProtocol`
  `-1` = `todos`; ICMP ignora portas; `userIdGroupPairs` vira seletor `grupo`;
  `prefixListIds` vira `servico`. NACL não entra (é filtro de subrede sem
  estado, raramente usado para segmentar aplicação), e isso é dito.
- **GCP:** firewall não é grupo, é regra com alvo. Cada tag alvo vira um
  **grupo sintético** `gcp:<rede>:tag:<tag>`, e cada service account alvo,
  `gcp:<rede>:sa:<email>`. Os membros são as VMs da rede com aquela tag ou
  service account. Firewall sem alvo vale para a rede inteira: grupo
  `gcp:<rede>:todas`, marcado como amplo. `sourceTags` e
  `sourceServiceAccounts` viram seletor `grupo`; várias origens na mesma regra
  são alternativas (uma regra canônica por origem). Regra `disabled` é ignorada
  e contada.
- **Azure:** o NSG é o grupo. Protege a NIC ou a subrede. O ASG é um grupo sem
  regras, usado como seletor (`sourceApplicationSecurityGroups`) e para
  estreitar o alvo (`destinationApplicationSecurityGroups` vira `destino`). As
  regras padrão (`defaultSecurityRules`) não entram: existem igual em qualquer
  VNet. `*` e `Internet` viram `qualquer`; `VirtualNetwork` vira `servico`.
  **Aproximação declarada:** a Azure avalia NSG de subrede e NSG de NIC em
  conjunto (os dois precisam permitir) e com prioridade entre permitir e negar;
  o modelo trata cada NSG separado e ignora `negar` na inferência. Pode
  inferir uma dependência que outra regra bloqueia. Nunca o contrário.
- **Membros efetivos** de um grupo: o recurso protegido diretamente, o recurso
  cuja interface é protegida e o recurso dentro de uma subrede protegida
  (direto ou pela interface). Só conta recurso de papel unidade (RI2).

### RI5 — Dependências

Para cada regra de **entrada que permite**, no grupo `G`:

1. **Alvos** = membros de `G`, estreitados pelo `destino` quando houver. Se `G`
   é amplo (GCP sem alvo), não gera dependência e vira achado `regra_ampla`.
2. **Origens**, pelo seletor:
   - `grupo H`, com `H ≠ G` → membros de `H`. **`H = G`** (o grupo libera os
     próprios membros, como o *default* da AWS) não gera dependência: vira
     achado `grupo_interno`. Sem essa regra, todo recurso do grupo padrão
     dependeria de todos os outros, o que é falso na prática e explode o grafo.
   - `cidr` → pelas subredes do inventário que o CIDR **toca**:
     - nenhuma → achado `origem_externa` (escritório, parceiro);
     - **uma**, e o CIDR contém a subrede inteira → os recursos dessa subrede;
     - **uma**, e o CIDR é um pedaço dela → os recursos cujo IP privado está no
       CIDR. Sem IP conhecido, nada, e achado `origem_nao_identificada`;
     - **duas ou mais** → achado `regra_ampla`. Regra do tipo "a VPC inteira
       acessa o banco" é configuração preguiçosa comum. Tratá-la como
       dependência juntaria toda aplicação da VPC ao banco num grupo só.
   - `qualquer` e `servico` → não geram dependência. A internet é exposição
     (RI11), não dependência.
3. Cada par origem → alvo em **unidades diferentes** vira a dependência
   `unidade(origem) → unidade(alvo)`, com a regra como evidência.
4. **Natureza:** `dados` quando o alvo é banco ou cache **ou a porta é de
   banco** (a lista da RI11: um PostgreSQL instalado numa VM é banco do mesmo
   jeito); `administrativo` quando a porta é de administração (22, 3389, 5985,
   5986); `encaminhamento` quando a origem é balanceador; `servico` no resto.
   `dados` e `encaminhamento` são acoplamento **forte** (RI7), `servico` é
   **fraco**, e `administrativo` não é acoplamento (RI7).
5. **Teto:** uma regra que geraria mais de 5.000 pares de unidades não gera
   nenhum e vira `regra_ampla`. Protege o grafo de uma regra genérica.

Também geram dependência: `encaminha_para` (natureza `encaminhamento`) e as
**declaradas** pelo cliente (`declaracoes.dependencias`, acoplamento
informado, default fraco). Entre as mesmas duas unidades fica uma dependência
só, com o acoplamento mais forte e até cinco evidências.

### RI6 — Unidades

1. Recursos de unidade, de anexo e `outro` entram num union-find. `anexado_a` e
   `membro_de` juntam. Um disco anexado a duas VMs junta as duas (disco
   compartilhado é acoplamento). O cluster de banco (`outro`) junta as
   instâncias dele.
2. **Unidade** é o conjunto que tem pelo menos um recurso de papel unidade. O
   resto (disco sozinho, IP sozinho, cluster vazio) não é unidade: disco e IP
   sozinhos são ociosos (RI12). O representante do conjunto é a menor chave,
   para ser determinístico.
3. **Tag de aplicação.** Recursos com o mesmo valor da tag de aplicação, na
   mesma **conta, região e ambiente**, viram uma unidade. A chave da tag:
   - vem das declarações (`chave_aplicacao`; `""` desliga o agrupamento);
   - ou é detectada entre `app`, `application`, `aplicacao`, `service`,
     `servico`, `workload`, `system`, `sistema` (sem diferenciar maiúsculas):
     a que aparece em mais recursos de unidade, desde que em pelo menos 50%
     deles. Empate: a ordem da lista.
   - O ambiente segue a mesma regra, com `env`, `environment`, `ambiente`,
     `stage`.
   - A escolha e a cobertura saem na resposta, como premissa.
4. Snapshot não é unidade: é backup, listado à parte. Fundação não é unidade.

### RI7 — Grupos de movimentação

Dependência **forte** junta unidades num grupo (union-find), porque acesso a
banco entre nuvens é latência em cada consulta, e balanceador em uma nuvem com
as máquinas em outra não funciona. Antes de juntar, três filtros, nesta ordem,
porque sem eles qualquer ferramenta compartilhada colapsa o ambiente num grupo
só:

1. **Ferramenta de administração:** unidade com acesso `administrativo` a 2 ou
   mais outras unidades (o bastion), ou declarada em `declaracoes.ferramentas`
   (o monitoramento, o ETL que a porta não denuncia). **Todas** as dependências
   dela saem do acoplamento, das ondas e da propagação de criticidade. Ela sai
   numa lista à parte: precisa alcançar as duas nuvens durante a migração e
   migra por último. Achado `ferramenta_administrativa`.
2. **Balanceador compartilhado:** balanceador com encaminhamento para 2 ou mais
   unidades tem esses encaminhamentos rebaixados a fraco. Ele depende das
   aplicações e migra depois delas. Achado `balanceador_compartilhado`.
3. **Hub de dados:** unidade com acesso `dados` a 3 ou mais bancos/caches que
   **também são acessados por outra unidade** tem esses acessos rebaixados a
   fraco. Achado `hub_de_dados`. Uma aplicação com três bancos só dela não é
   hub: esses bancos não têm outro consumidor.

O que sobra de forte junta. Banco compartilhado por aplicações que não são
ferramenta nem hub junta todas: é acoplamento real, e sai o risco
`banco_compartilhado` com os consumidores. Grupo com 5 ou mais unidades e 40%
ou mais de todas dá o risco `grupo_acoplado_grande`. O que as heurísticas não
pegam (um monitoramento que só acessa dois bancos pela porta do banco) aparece
nesses dois riscos, e o cliente corrige declarando a ferramenta.

### RI8 — Ondas

1. **Onda 0 é a fundação:** redes, subredes, grupos de segurança, gateways.
2. Sobre os grupos, com as dependências **fracas** entre grupos diferentes:
   componentes fortemente conexos (Tarjan) migram na mesma onda (ciclo vira
   achado informativo); no grafo condensado, `onda(G) = 1 + max(onda das
   dependências de G)`, e 1 quando não há dependência. É a camada pelo
   caminho mais longo: determinística e explicável ("está na onda 3 porque
   depende de X, que está na 2").
3. Grupos sem dependência nenhuma, nem de nem para, são **independentes**:
   saem numa lista à parte, sem número de onda, porque podem ir a qualquer
   momento. Sugestão de piloto: o independente de menor criticidade e menor
   tamanho. As ferramentas de administração (RI7) também saem à parte.
4. Dentro da onda, ordem de exibição: criticidade efetiva (`baixa`, `media`,
   não declarada, `alta`), número de recursos, nome. É ordem de leitura, não
   restrição.

### RI9 — Dependências entre ondas

Toda dependência fraca `G → D` com `onda(G) > onda(D)` significa que, entre a
migração de `D` e a de `G`, `G` ainda está na origem e chama `D`, já no
destino. Cada uma sai listada, com portas e evidência. É a lista do que precisa
de conectividade híbrida (VPN ou interconexão) durante a migração.

### RI10 — Criticidade

- **Escala:** `alta`, `media`, `baixa`.
- **Precedência, do mais específico para o mais geral:** declaração pela API
  para o recurso > tag de criticidade no recurso > declaração pela API para a
  aplicação > não declarada. A declaração por aplicação vale em todos os
  ambientes da aplicação; para diferenciar o `dev` do `prod`, declara-se o
  recurso ou usa-se a tag. Declaração por aplicação sem chave de aplicação
  ativa (RI6) é ignorada e o motivo sai na resposta.
- **Tags aceitas:** só chaves explícitas (`criticality`, `criticidade`,
  `business-criticality`, `business_criticality`) com valores de um
  vocabulário fechado (`high`/`alta`/`critical`/`critica`/`tier1`/`p1` → `alta`;
  `medium`/`media`/`moderate`/`tier2`/`p2` → `media`; `low`/`baixa`/`tier3`/`p3`
  → `baixa`), sem diferenciar maiúsculas nem acento. Valor fora do vocabulário
  é ignorado e contado.
- **Unidade e grupo** ficam com a maior declarada entre seus recursos.
- **Efetiva:** a maior entre a própria e a de todo grupo que depende dela,
  direta ou indiretamente (dependências fracas, inclusive as rebaixadas; as da
  ferramenta de administração não propagam, porque o bastion ser crítico não
  torna crítico tudo que ele acessa). Quem sustenta algo crítico é crítico. A
  origem vai junto: `declarada`, `tag:<chave>=<valor>`, `herdada de <grupo>` ou
  `nao_declarada`.
- **Nunca** vem de custo, de centralidade no grafo ou de nome. O número de
  dependentes transitivos sai como **raio de impacto**, com esse nome, e não
  como criticidade.

### RI11 — Riscos

| regra | condição | severidade |
|---|---|---|
| `exposicao_administrativa` | entrada permitida de qualquer origem (`0.0.0.0/0`, `::/0`, `*`, `Internet`) nas portas 22, 3389, 5985 ou 5986 | alta |
| `banco_exposto` | entrada de qualquer origem numa porta de banco (1433, 1521, 3306, 5432, 6379, 11211, 27017, 9042, 9200) num grupo com banco ou cache; ou banco marcado como público (`publiclyAccessible`, `publicNetworkAccess`, IP público no Cloud SQL) | alta |
| `porta_ampla_aberta` | entrada de qualquer origem com protocolo `todos` ou faixa 0–65535 | alta |
| `ponto_unico_de_falha` | banco ou cache sem alta disponibilidade do qual dependem 2 ou mais **unidades** | media |
| `critico_sem_ha` | grupo com criticidade efetiva `alta` contendo banco ou cache sem alta disponibilidade | media |
| `banco_compartilhado` | banco ou cache que juntou 2 ou mais unidades não-hub no mesmo grupo (RI7) | media |
| `grupo_acoplado_grande` | RI7 | media |
| `inventario_desalinhado` | **só com billing:** captura antes do início do período menos 30 dias, ou depois do fim mais 45 dias | media |
| `cobertura_baixa` | **só com billing:** menos de 80% do custo com `resource_id` encontrado no inventário | media |
| `recurso_com_ip_publico` | VM com IP público fora de balanceador | informativa |
| `sem_regras` | inventário sem nenhuma regra de rede: nenhuma dependência pôde ser inferida | informativa |
| `regra_ampla`, `grupo_interno`, `origem_externa`, `origem_nao_identificada`, `ferramenta_administrativa`, `hub_de_dados`, `balanceador_compartilhado`, `ciclo` | dependência não inferida, rebaixada, fora das ondas ou em ciclo, explicado | informativa |

Cada risco sai com regra, severidade, título, detalhe, até 10 recursos de
exemplo e o total.

### RI12 — Recursos ociosos

Disco `disponivel`, instância `parada`, IP público sem associação e snapshot
cujo disco não está no inventário. Saem com GB e custo (quando há correlação),
como **oportunidade**: a decisão de aposentar é do responsável (Retire, nos
7 Rs). A análise não os tira da conta.

### RI13 — Correlação com o billing

- Cada recurso leva **apelidos**: a chave, o id nativo, o ARN, o nome (RDS
  identifier, bucket), a forma com ID numérico no GCP e as formas de self-link.
  Tudo em minúsculas.
- O custo por `resource_id` vem do `billing_enriched`: a mesma base da análise,
  sem imposto nem crédito.
- **Métricas:** custo analisado, custo com `resource_id`, custo encontrado no
  inventário, fração encontrada, recursos do billing encontrados e não
  encontrados, e lacunas agrupadas por (conta, região) e por serviço. Exemplo:
  "a conta X não está no inventário: US$ 4 mil"; "AmazonEC2 `snap-*`: o Config
  não registra snapshot de EBS". E recursos de unidade do inventário sem
  cobrança, por tipo. Rede e grupo de segurança não entram, porque não são
  cobrados.
- **Alinhamento no tempo:** `capturado_em` contra o período do billing (RI11).

### RI14 — Integração com a análise

- O inventário entra **só quando pedido**: `inventory_id` no corpo do
  `POST /analyses/start/{report_id}`. Não se escolhe "o mais recente"
  implicitamente.
- **Validação antes de tudo**, inclusive antes da idempotência: o inventário
  existe e é do usuário (404 `INVENTARIO_NAO_ENCONTRADO`), está `DONE` (409
  `INVENTARIO_NAO_PROCESSADO`) e é do mesmo provedor do billing (422
  `INVENTARIO_DE_OUTRA_NUVEM`). Um id inválido nunca é engolido por um job já
  existente.
- A idempotência atual continua: um job ativo para o report é devolvido como
  está, com o inventário que ele já tinha.
- O pipeline carrega recursos, relações e as **declarações daquele momento**,
  roda a topologia e a correlação e grava `topologia` no payload, com o
  `fingerprint`. A partir daí a análise fica congelada: mudar a declaração
  depois não muda a análise feita.
- **Neutralidade:** com e sem inventário, o payload é idêntico fora da chave
  `topologia`. Isso é teste. Por isso o que o Terraform precisa (versão do
  banco por grupo, posicionamento por pool, discos de boot) fica **dentro** de
  `topologia`, chaveado igual aos pools e grupos de banco, e não enxertado em
  `pools_computacao` ou `inventario_medido`.
- **LLM e agente:** recebem a topologia **compacta** (resumo, cobertura, ondas,
  até 20 riscos e 20 dependências entre ondas). Rede, posicionamento e bancos
  ficam só no payload persistido, para o Terraform. Vale no `_build_prompt` do
  orquestrador e no `GET /analyses/{report_id}/request` (a superfície do
  agente). Mesmo padrão do `_compact_decision_payload`.
- **O backend manda na ordem:** no `impose_decision`, que já é o funil comum ao
  LLM e ao agente, `agent_views.risk_migration.recommended_migration_sequence`
  é substituída pelas ondas calculadas, e os riscos `alta`/`media` entram em
  `risk_flags`. Mesmo princípio do `backend_authority`: o modelo narra e não
  decide a ordem.

### RI15 — Consumo no Terraform

Só quando o payload da análise tem `topologia`. Sem ela, nada muda (teste de
caracterização dos pacotes atuais, byte a byte).

1. **Posicionado** é o recurso de origem que entra num recurso gerado: VM de
   pool com equivalente no destino e banco de grupo gerado. Só subrede e grupo
   com recurso posicionado são espelhados.
2. **Subredes espelhadas, endereçamento novo.** Cada subrede de origem
   posicionada vira uma subrede de destino, **dentro do CIDR da requisição**.
   Reusar os CIDRs da origem quebraria a VPN da migração, porque faixas
   sobrepostas não roteiam. Alocação determinística (ordem: tamanho, depois
   chave): primeiro com o prefixo da origem (nunca maior que metade da rede);
   se não couber, todas `/24`; se não couber, divisão igual da rede; se nem
   assim, a subrede única de hoje, com aviso. CIDR da requisição que sobrepõe a
   origem gera aviso. Várias redes de origem viram **uma** rede de destino, e a
   premissa diz que o isolamento entre elas passa a ser dos grupos de
   segurança. Na AWS, a zona da subrede de destino herda a letra da zona de
   origem (`a`, `b`, `c`; outra letra vira `a`).
3. **Pública ou privada:** espelha a origem (`mapPublicIpOnLaunch` ou recurso
   com IP público), **mas só se a requisição pedir `public_access`**. Sem isso,
   tudo sai privado, e o aviso lista o que era público.
4. **Grupos de segurança:** um por grupo de origem com membro posicionado. As
   regras de **entrada** são traduzidas:
   - CIDR de uma subrede espelhada → CIDR da subrede de destino. Pedaço de
     subrede → a subrede de destino inteira, com premissa.
   - CIDR interno amplo (várias subredes, a rede inteira) → CIDR da rede de
     destino. Preserva a intenção "qualquer coisa de dentro".
   - CIDR externo ou de subrede que não migrou → mantido: essa origem continua
     onde está e fala com o destino pela conectividade híbrida.
   - Referência a grupo espelhado → referência ao grupo de destino. Grupo que
     não migrou → os CIDRs das subredes de origem dos membros dele (que ficam
     na origem, pela mesma razão do item anterior). Sem membro conhecido,
     regra descartada com aviso.
   - **Regra de risco `alta` não é replicada** (RI11): administração, banco ou
     tudo aberto para a internet. Vira aviso. Replicar seria migrar a falha.
   - `negar`, protocolo fora de `tcp`/`udp`/`icmp`/`todos`, prefix list e
     `servico` sem equivalente (`AzureLoadBalancer`) → não traduzidos, com
     aviso. `VirtualNetwork` → CIDR da rede de destino.
   - Saída: liberada, que é o padrão do destino. Se a origem restringia saída,
     há aviso.
   - **Forma por nuvem:** AWS, grupo de segurança com uma regra de entrada por
     regra traduzida; GCP, uma tag de rede por grupo e um firewall por regra
     (`target_tags`, `source_tags`/`source_ranges`); OCI, NSG com regras;
     Azure, um ASG por grupo (a VM entra pela NIC) e **um NSG por subrede de
     destino** com as regras cujo alvo está nela (destino = o ASG do grupo),
     porque a NIC aceita um NSG só e a VM de origem pode ter vários grupos.
     Prioridade da Azure: 100 + 10 por regra, em ordem determinística; passou
     de 4096, as restantes saem com aviso.
5. **Posicionamento das VMs.** O pool continua com o shape e a quantidade `M`
   que a análise aprovou. O que muda é onde ficam: `M` é repartido entre os
   posicionamentos de origem (subrede + conjunto de grupos) proporcionalmente
   ao vCPU de origem, pelo **maior resto**. As VMs do pool que não estão no
   inventário formam o posicionamento "sem posicionamento", que vai para a
   subrede padrão. Se `M` for menor que o número de posicionamentos, os `M`
   maiores recebem uma VM cada e o aviso lista os que ficaram sem máquina.
   Custo aprovado intacto.
6. **Banco:** a versão maior mais frequente no grupo (empate: a maior) vira o
   `default` da variável de versão, no formato do destino (`POSTGRES_15` no
   GCP, `15` na Azure e na AWS, `8.0` para MySQL na AWS), só quando está na
   lista suportada do destino (PostgreSQL 12 a 16; MySQL 8.0). Senão, a
   variável fica sem default e a premissa diz a versão medida. Banco que o
   billing cobrou **sem classe** (Azure Flexible Server) passa a ser gerado com
   a classe e o motor do inventário, medidos, em vez da estimativa por razão de
   custo. Na AWS, o banco recebe os grupos de segurança espelhados e um
   `aws_db_subnet_group` quando tinha subredes espelhadas em 2 zonas ou mais;
   senão, aviso. Nas outras nuvens o posicionamento de banco fica como hoje,
   com aviso (exige endpoint privado, que é decisão de rede do cliente).
7. **Discos:** disco de boot vira o disco raiz da VM (tamanho médio do boot no
   posicionamento), não volume avulso. Hoje o CUR cobra o volume raiz como EBS
   comum, e o pacote o cria duas vezes. Disco sem anexo não é gerado e vai para
   a lista de ociosos. Volumes avulsos = medidos − boot − sem anexo, por
   categoria, nunca abaixo de zero. **O custo aprovado inclui esses discos**:
   o README diz quantos GB o pacote deixou de criar e por quê.
8. **README:** seção "Topologia de origem" com as ondas, as dependências entre
   ondas (as faixas que a VPN precisa liberar durante a migração), os riscos,
   a exposição e a cobertura.
9. **Fora do pacote, dito:** balanceadores (com a exposição medida), gateways
   (NAT, VPN), caches e o que ficou sem posicionamento.

### RI16 — Determinismo

Normalizadores e análise ordenam tudo por chave. O `fingerprint` é o SHA-256 do
JSON canônico dos recursos, das relações e das declarações usadas. O inventário
guarda o dele (sem declarações), e a análise grava o da topologia que usou.

### RI17 — Dados sensíveis e retenção

Inventário tem IP, nome de máquina e tag, que pode trazer e-mail de dono. O
arquivo bruto é apagado depois da ingestão. O normalizado fica até o
`DELETE`. Nada do inventário bruto vai ao LLM: só nomes de aplicação, contagens
e riscos. Nome vindo do inventário que aparece em comentário do `.tf` passa por
saneamento (quebra de linha e caracteres de controle removidos), porque um
comentário com quebra de linha vira código HCL.

---

## 6. Arquitetura

```
api/v1/routes/inventory.py ─┐
                            ├─► use_cases/inventory/*  ─► domain/services/topologia/*
api/v1/routes/analysis.py ──┤        │                      domain/services/grafo.py
                            │        │                      domain/services/correlacao_inventario.py
infrastructure/             │        ▼                      domain/entities/topologia.py
  inventory_ingestion/ ◄────┘   domain/repositories/i_inventory_repo.py
    detector, leitura,              ▲
    normalizadores/*                │
  repositories/pg_inventory_repo ───┘
  terraform/renderers/* ◄── domain/services/terraform_plan.py ◄── rede_destino.py
```

Desacoplamento:

- **Domínio puro:** entidades, grafo, topologia, correlação e tradução de rede
  para o destino. Entra dado, sai dataclass. Sem I/O, sem framework.
- **Normalizador é adaptador:** cada formato implementa `NormalizadorDeInventario`
  (`detectar(amostra)`, `normalizar(documentos)`) e devolve o modelo canônico.
  A coleta ativa da I10 vai produzir os mesmos documentos e reusar os mesmos
  normalizadores.
- **Consumidores não sabem a origem:** a topologia, a análise e o Terraform
  leem o modelo canônico, nunca o formato do provedor.
- **O Terraform lê só o payload congelado.** Não consulta o inventário na hora
  de gerar, então o pacote descreve a análise aprovada.

---

## 7. O que já existe e o impacto de reusar

| existente | uso | impacto |
|---|---|---|
| `cloud_clients/gcp_catalog_client.py` | nenhum: é catálogo de preço, não inventário | nenhum |
| `config.cloud_creds_fernet_key` | nenhum nesta entrega (D1) | nenhum; fica para a I10 |
| padrão de ingestão do billing (upload → BackgroundTask → sessão limpa na falha) | copiado para o inventário | nenhum no billing |
| `detector` do billing (detecção por conteúdo) | mesma ideia, módulo próprio | nenhum: inventário não é billing e não entra em `detector.PARSERS` |
| `InventarioDoPool.recursos` | liga pool ↔ recursos do inventário para o posicionamento | leitura apenas |
| `BancoMedido` | casa grupo de banco do billing com banco do inventário (versão, subrede) | leitura apenas |
| `billing_enriched.resource_id` | custo por recurso na correlação | método novo de agregação no repositório; os existentes não mudam |
| `build_pipeline_llm_input_payload` | ganha a chave `topologia`, só com inventário | aditivo; teste de neutralidade (RI14) |
| `orchestrator._build_prompt` e `GET /analyses/{id}/request` | compactam a topologia | aditivo, no padrão do `_compact_decision_payload` |
| `impose_decision` (funil comum ao LLM e ao agente) | chama a imposição da sequência de migração no fim | uma chamada nova; sem `topologia` no payload, nada muda |
| `terraform_plan.derivar_plano` | consome `topologia` | ramo novo, só com topologia; sem ela, caminho idêntico (caracterização byte a byte) |
| `schemas/terraform.TipoRecurso` | ganha `security_group` | allowlist cresce; renderer sem suporte devolve `None` (vira `unsupported_items`) |
| renderers (4) | subrede por recurso, grupos de segurança, disco raiz | mudança nos 4; `terraform validate` nos 12 pares origem→destino |
| `decision_support` (score e 7 Rs, do colega) | **não muda** | a política `score-7rs-v1` e o `fingerprint` dela ficam iguais |
| `gerar_mocks_billing.py` | fonte dos IDs do inventário mock | o gerador de inventário importa as classes; o billing mock não muda (teste de determinismo existente) |

---

## 8. Mocks de inventário

Gerados por `scripts/gerar_mocks_inventario.py`, a partir dos mesmos geradores
de billing, com os mesmos IDs:

- **AWS Config:** um snapshot por conta e região do CUR mock. VPC e subredes por
  conta, pública e privada por aplicação; grupos por camada (balanceador →
  aplicação → banco) com referência entre grupos; VMs do CUR com
  `blockDeviceMappings` (raiz + dados); volumes do CUR anexados às VMs da mesma
  conta e zona, alguns sem anexo; RDS com `engineVersion`, `multiAZ`, subnet
  group e grupo; balanceadores do CUR com `scheme`; um bastion e um
  monitoramento acessando vários bancos (hub); um grupo com SSH aberto para a
  internet e um banco exposto (riscos); instâncias paradas; tags `app`, `env`,
  `team` e, em parte, `criticality`.
- **GCP Asset Inventory (NDJSON, snake_case):** as VMs e discos do BigQuery
  mock com o `name` do asset por nome e o `data.id` numérico igual ao
  `global_name` do billing; redes, subredes, firewalls por tag.
- **Azure Resource Graph (`{"data": [...]}`, 2 páginas):** VMs, NICs, VNet com
  subredes, NSG e ASG, discos (inclusive tier), Flexible Server com `version`,
  Load Balancer com backend pool.
- **OCI (canônico):** instâncias, VCN, subredes, NSG com regras, volumes,
  Autonomous Database.
- **Recursos só no inventário** (redes, VM parada) e **só no billing** (uma
  conta inteira fora do snapshot), para exercitar a cobertura.

Invariantes testados: determinismo (duas gerações idênticas), toda relação
resolve, todo recurso de unidade do billing mock aparece no inventário (menos a
conta deixada de fora de propósito), correlação ≥ 95% nas contas incluídas.

---

## 9. Etapas

Cada etapa termina com testes, uma revisão lógica escrita neste arquivo e uma
análise de impacto nas etapas anteriores e seguintes.

| etapa | entrega | testes |
|---|---|---|
| **I0** | tag `baseline/pre-inventario-topologia`; este plano com as rodadas de revisão | — |
| **I1** | `domain/entities/topologia.py` (tipos, relações, regra, seletor, recurso, inventário); `domain/services/grafo.py` (union-find, Tarjan, camadas) | unitários de 100% no grafo: ciclos, autolaço, grafo vazio, desconexo, determinismo |
| **I2** | `infrastructure/inventory_ingestion/`: leitura segura (gz, zip, NDJSON, limites), detector por conteúdo, 4 normalizadores | amostras no formato da documentação oficial; limites; formato desconhecido; referência pendente; item removido; as duas grafias do GCP; configuração como string |
| **I3** | `scripts/gerar_mocks_inventario.py` + fixture de sessão nos testes | invariantes da §8 |
| **I4** | `domain/services/topologia/`: membros, dependências, unidades, grupos, ondas, criticidade, riscos, ociosos | casos por regra (RI5–RI12) + mocks inteiros |
| **I5** | migração (3 tabelas), models, repositório, casos de uso, rotas, flag | unitários de rota com fakes; integração no Postgres (ida e volta, isolamento, `DELETE` em cascata) |
| **I6** | correlação: agregação de custo por recurso + serviço de domínio + rota | mocks: cobertura nas contas incluídas, lacuna na conta excluída, ID numérico do GCP |
| **I7** | análise: `inventory_id`, payload `topologia`, compactação, imposição da sequência | neutralidade; payload congelado; LLM e agente sem rede e sem posicionamento |
| **I8** | Terraform: (a) caracterização + versão, discos, README; (b) grupos de segurança nas 4 nuvens; (c) subredes espelhadas e posicionamento | caracterização sem topologia; `terraform validate` nos 12 pares; regra de risco não replicada |
| **I9** | E2E pela API com billing + inventário, relatório de coerência, `docs/` | payload, resposta e pacote coerentes entre si |
| **I10** | coleta ativa pela plataforma | bloqueada por D1 |

---

## 10. Riscos

| risco | mitigação |
|---|---|
| Os formatos foram validados na documentação, não num export real | leitura tolerante, tudo que não foi modelado é contado; o primeiro export real vira fixture |
| O cliente não tem AWS Config ligado | formato canônico; ou ligar o Config (custo por item gravado, baixo) |
| Inventário grande | limites da RNF4, NDJSON em streaming, teto de arestas |
| Regra de rede ≠ tráfego real | dito em toda saída; tráfego real (Flow Logs) fora do escopo |
| Heurísticas de acoplamento (hub, balanceador compartilhado, regra ampla) errarem num ambiente real | limiares como constantes nomeadas, cada decisão sai como achado com evidência, e o cliente corrige declarando a dependência |
| Tradução de regra deixar o destino mais aberto que a origem | só se traduz o que existe; risco alto não é replicado; aproximações viram premissa |
| Terraform por nuvem (Azure com NSG e ASG) | `terraform validate` nos 12 pares |
| Payload maior | topologia compacta para o LLM e o agente; teste de tamanho |

---

## 11. Fora de escopo

- Coleta ativa pela plataforma (I10, bloqueada por D1).
- Tráfego real (VPC Flow Logs e equivalentes).
- Gerar balanceador, gateway, VPN, DNS e IAM no Terraform. Entram como "fora do
  pacote", com o que foi medido.
- Recotar recurso a recurso a partir do inventário (rehost 1:1). Muda custo e
  economia, e precisa de plano próprio.
- Carga de trabalho dentro do Kubernetes (pods, services).
- Várias nuvens de origem numa mesma análise.
- Mudar a política de score e 7 Rs (`score-7rs-v1`) para usar a topologia.

---

## 12. Revisão crítica

### Rodada 1: falhas de lógica no rascunho

Relendo o rascunho regra por regra, contra casos reais de ambiente:

| # | falha | correção |
|---|---|---|
| 1 | O `RDS::DBCluster` era `outro`, e a tabela de pares não tinha como dizer que a instância pertence ao cluster: as instâncias do mesmo Aurora virariam unidades separadas | `membro_de` sai do campo `dBClusterIdentifier`; o union-find aceita `outro` como conector; unidade é o conjunto com pelo menos um recurso de papel unidade (RI3, RI6) |
| 2 | A regra de CIDR tinha um buraco: um CIDR maior que uma única subrede (a VPC com uma subrede só) não caía em nenhum caso | casos pelas subredes que o CIDR **toca**: nenhuma, uma inteira, um pedaço de uma, duas ou mais (RI5) |
| 3 | CIDR de host (`/32`) sem IP conhecido pegaria a subrede inteira: dependência inventada | sem IP, não infere; achado `origem_nao_identificada` (RI5) |
| 4 | Acoplamento forte colapsava o ambiente: um bastion ou monitoramento com acesso a todos os bancos juntava todas as aplicações num grupo; um ALB compartilhado por roteamento de caminho juntava aplicações independentes | exceções de **hub de dados** e **balanceador compartilhado**, aplicadas antes de juntar, com o critério que separa hub de aplicação com vários bancos próprios (RI7) |
| 5 | "A VPC inteira acessa o banco" (CIDR da VPC) virava dependência de todas as aplicações | duas ou mais subredes = `regra_ampla`, sem dependência (RI5) |
| 6 | `ponto_unico_de_falha` contava **grupos** dependentes; depois de juntar por banco compartilhado, os dependentes estão no mesmo grupo e o risco sumia | conta **unidades** (RI11) |
| 7 | `inventario_desalinhado` e `cobertura_baixa` dependem de billing, mas o `GET /topology` não recebe billing | marcados "só com billing"; avaliados na correlação e na análise (RI11) |
| 8 | Na análise, a idempotência devolvia o job ativo antes de validar o `inventory_id`: um id errado passaria em silêncio | validação antes da idempotência (RI14) |
| 9 | Enxertar versão de banco em `inventario_medido` e posicionamento em `pools_computacao` quebraria a neutralidade do payload | tudo dentro de `topologia`, chaveado igual aos pools e grupos (RI14) |
| 10 | Regra que referencia grupo sem membro migrado era descartada, mas a origem desse grupo continua existindo e precisa de acesso durante e depois da migração | vira os CIDRs das subredes de origem dos membros (RI15.4), coerente com a regra de CIDR de subrede que não migrou |
| 11 | O pacote passa a criar menos disco do que a análise cotou (boot e sem anexo), sem dizer | o README declara os GB não criados e o motivo (RI15.7) |
| 12 | Unidade por tag juntava a mesma aplicação em regiões diferentes | a chave inclui a região, coerente com o pool (família, região) (RI6) |
| 13 | Lacuna de correlação só por conta e região escondia a causa mais comum (tipo que o Config não registra) | lacuna também por serviço (RI13) |
| 14 | GCP com alvo por service account e a semântica de NSG da Azure (subrede e NIC juntos, `negar` com prioridade) não estavam cobertos | grupo sintético por service account; aproximação da Azure declarada, sempre no sentido de inferir a mais, nunca a menos (RI4) |
| 15 | Declaração por aplicação sem chave de aplicação ficava sem efeito e sem aviso | ignorada com motivo na resposta (RI10) |
| 16 | A superfície do agente (`GET /analyses/{id}/request`) devolveria o payload inteiro, com a rede e o posicionamento | compacta igual ao LLM (RI14) |
| 17 | Ler 20 arquivos de 50 MB com `file.read()` pesaria 1 GB na memória da requisição, e um JSON de documento único de 500 MB não cabe num `json.load` | gravação em blocos e limites separados para JSON único e NDJSON (RNF4) |

### Rodada 2: consistência entre regras

- **Três leituras de CIDR, três propósitos.** A RI5 é conservadora (não inventa
  dependência de regra ampla), a RI11 olha só a internet e a RI15 preserva a
  intenção no destino (ampla vira a rede de destino). Não se contradizem porque
  respondem perguntas diferentes: "quem depende de quem", "o que está exposto",
  "o que liberar no destino". Conferido caso a caso.
- **Rebaixamento e criticidade.** A dependência rebaixada (hub, balanceador
  compartilhado) continua sendo dependência para a criticidade e para as ondas.
  Só deixa de juntar grupo. Um balanceador `alta` torna `alta` as aplicações
  atrás dele, o que está certo.
- **Independentes × dependências entre ondas.** Independente não tem aresta,
  então nunca aparece na lista da RI9. Conferido na definição.
- **Neutralidade × Terraform.** A análise não muda com o inventário; o
  Terraform muda, e só com `topologia`. O custo aprovado continua o da análise:
  posicionamento reparte `M`, não muda `M`; disco não criado é declarado.
- **Ondas × acoplamento.** Forte está dentro do grupo, fraco entre grupos, e as
  ondas usam só fraco. Um ciclo de fracos vira a mesma onda. Não sobra aresta
  sem tratamento.

### Rodada 3: efeitos fora do pipeline

- **Agente (`/v1/agent/*`)** passa pelo `impose_decision`, então recebe a mesma
  imposição da sequência. Compactação aplicada no `GET /request`.
- **Análises antigas** não têm `topologia`: Terraform, agente e tela seguem
  iguais. Coberto pelo teste de caracterização.
- **Score e 7 Rs** não leem `topologia`, e o `fingerprint` da decisão não a
  inclui: a nota não muda com o inventário. O texto de limitação "Billing não
  comprova dependências" continua verdadeiro.
- **Migração de banco:** só tabelas novas; o `downgrade` as remove. Nenhuma
  tabela existente muda.
- **Tela:** o campo `recommended_migration_sequence` já existe e é exibido; só
  passa a vir calculado quando há inventário.
- **Dashboard, billing e ingestão:** não tocados.

### Rodada 4: segunda leitura depois das correções

As correções da rodada 1 criaram casos novos. Relendo com ambientes típicos
(bastion, monitoramento, banco instalado em VM, ALB compartilhado, dev e prod
na mesma conta):

| # | falha | correção |
|---|---|---|
| 18 | O critério de hub (3 ou mais bancos compartilhados) não pegava o bastion que acessa só 2 bancos: ele ligava duas aplicações num grupo | filtro anterior por **porta de administração** (22, 3389, WinRM) em 2 ou mais unidades: vira ferramenta, fora do acoplamento e das ondas; e `declaracoes.ferramentas` para o que a porta não denuncia (RI7) |
| 19 | Banco instalado numa VM (PostgreSQL em EC2) tinha acesso classificado como `servico`, fraco: a aplicação e o banco dela podiam cair em ondas diferentes | natureza `dados` também pela porta do banco (RI5) |
| 20 | A criticidade "herdada" do bastion tornaria crítico tudo que ele acessa | dependências de ferramenta não propagam criticidade (RI10) |
| 21 | Declaração pela API para a aplicação passava por cima da tag do recurso: o `dev` de uma aplicação crítica ficava crítico mesmo com tag `low` | precedência pelo mais específico: recurso (API) > tag do recurso > aplicação (API) (RI10) |
| 22 | Dependência declarada entre aplicações ligava o `dev` de uma ao `prod` da outra | vale só dentro do mesmo escopo (conta, região, ambiente) (§4) |
| 23 | O plano prometia fechar as lacunas da E10, mas o Resource Graph não traz RAM de VM | tabela do que fecha e do que não fecha, com o motivo (§2) |
| 24 | Azure: NIC aceita um NSG só, e a VM de origem pode ter vários grupos. Um NSG por grupo não aplicaria | ASG por grupo + um NSG por subrede de destino, com o ASG como destino da regra (RI15.4) |
| 25 | Banco da Azure sem classe no billing continuava estimado por custo mesmo com o inventário dizendo a classe | gerado com a classe e o motor do inventário (RI15.6) |
| 26 | Balanceador do GCP: o asset não diz quais VMs estão atrás, e o tráfego chega das faixas do Google | declarado como limite: aparece como exposição, sem dependência inferida (§2) |

Terceira leitura, só das regras mudadas nesta rodada: a ferramenta sai das
ondas **e** da propagação, mas continua nos riscos (um bastion com SSH aberto
para a internet é `exposicao_administrativa` do mesmo jeito). A natureza
`dados` pela porta usa a mesma lista da RI11, então os dois pontos não
divergem. Não sobrou contradição.

---

## 13. Revisão por etapa

Cada etapa fecha com uma revisão lógica e o impacto no que veio antes e no que
vem depois.

### I1 — domínio e grafo

- **Entregue:** `domain/entities/topologia.py` (tipos, papéis, relação, regra,
  seletor, recurso, inventário) e `domain/services/grafo.py` (união-busca com
  representante determinístico, Tarjan iterativo, camadas pelo caminho mais
  longo, dependentes transitivos). 100% de cobertura nos dois.
- **Teste que importa:** 40 grafos aleatórios comparados com força bruta
  (componente = alcance mútuo; camada = `1 + max` das dependências externas do
  componente; dependentes = alcance reverso), e a mesma saída com a entrada
  embaralhada. Cadeia de 5.000 nós em ciclo não estoura a pilha.
- **Convenção fixada:** aresta `(u, v)` = u depende de v. As ondas (RI8), o
  raio de impacto e a propagação de criticidade (RI10) usam as mesmas funções,
  então não há como uma divergir da outra na direção.
- **Impacto:** nenhum no código existente (módulos novos). A I4 depende destas
  assinaturas; a I2 depende do modelo.

### I2 — normalizadores

- **Entregue:** `infrastructure/inventory_ingestion/` com leitura segura
  (gzip e zip pelo conteúdo, orçamento de bytes compartilhado, NDJSON em
  streaming, limite de documento único), construtor comum (dedup pela captura,
  referência resolvida no fim, par de tipos validado pela RI3) e os quatro
  normalizadores. 106 testes; cobertura do pacote entre 91% e 98% por módulo.
- **Achados ao testar contra a documentação oficial:**
  - O exemplo oficial do Config escreve o tipo da relação como
    `AWS::EC2::SUBNET`. O mapa de tipos é consultado sem diferenciar caixa, e
    a relação vem primeiro do campo `subnetId`, que é estruturado. Os dois
    caminhos estão testados.
  - O mesmo exemplo tem um ARN de volume malformado
    (`arn:aws:us-west-2b:...`). Ele vira a chave como veio; a correlação com
    o billing usa os apelidos (o `vol-…` está entre eles), então não depende
    do ARN estar certo.
  - `ipRanges` (lista de texto, formato antigo) e `ipv4Ranges` repetem a mesma
    faixa no mesmo item: deduplicado por regra.
  - Um grupo citado de outra conta (`userId` diferente) vira
    `grupo_externo:<id>` e é contado. Sem isso a regra apontaria para um grupo
    que não existe no inventário.
- **Impacto para trás:** nenhum. Pacote novo, fora de `detector.PARSERS` do
  billing.
- **Impacto para frente:** a I5 precisa persistir as regras separadas dos
  atributos (no modelo são `Inventario.regras`, não atributo do grupo); a I3
  precisa gerar os quatro formatos exatamente como estes testes os descrevem.
