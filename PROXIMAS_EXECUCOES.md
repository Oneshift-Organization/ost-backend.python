# PROXIMAS_EXECUCOES — fila de prioridades

> Criado em 18/10/2026 a pedido do usuário. É a fila única de tudo que está aberto:
> os seis pontos da auditoria do MCP (`PLAN-OCI-EQUIVALENCIAS.md`, 17/10), as melhorias
> M1–M13 do [`PLANO_ESTRATEGIA_SPRINT_TERRAFORM`](PLANO_ESTRATEGIA_SPRINT_TERRAFORM.md)
> §7, os defeitos D1–D4 do E2E de 17/10 e o Terraform da análise `f67fcd01`.
> Cada correção tem a análise de impacto escrita antes do código e vale como regra
> geral, nunca para uma fatura. O que termina vai para **Concluído**, com data e
> commit.
>
> Estados: **a fazer** · **em execução** · **✅ feito** · **decisão** (espera alguém).

## 1. Faça agora

| # | item | resumo | de quem | estado |
|---|---|---|---|---|
| 1 | Terraform: banco do destino pelo motor medido | Conferidos os quatro geradores (§5). Só o da OCI erra: escreve todo banco como Oracle Autonomous Database, com 1 OCPU por vCPU (o dobro), e a análise cotou OCI PostgreSQL. PostgreSQL passa a `oci_psql_db_system` e MySQL a HeatWave; o Autonomous fica para Oracle. Some o aviso falso "versão 16 fora do que o destino aceita" | nós | ✅ feito (develop `ba92bce`) |
| 2 | Terraform: buckets do escopo | O bucket só nasce do custo dele; a fatura não liga o custo do S3 ao bucket, e o pacote sai sem bucket e sem aviso (bucket conta como "tipo que o pacote cria"). Passa a sair do inventário, pelo nome | nós | ✅ feito (develop `ba92bce`) |
| 3 | Terraform: avisos verdadeiros | "AmazonVPC: nenhum recurso no inventário" com 3 VPCs e 15 subredes, e a rede criada; idem Route53 com a zona no inventário. Cost Explorer e Config passam a ser "gestão da conta, não infraestrutura" | nós | ✅ feito (develop `ba92bce`) |
| 4 | Terraform: ocupação de origem burstable | O empacotador dimensiona burstable pela linha de base; o aviso compara com a vCPU nominal e pede "uma máquina a mais" sem motivo (146% na `f67fcd01`). Mesma base nos dois, dizendo que a origem é burstable | nós | ✅ feito (develop `ba92bce`) |
| 5 | D1: memória da série B v1 da Azure | B1ls com 2 GiB (real 0,5), B1s 2 (real 1), B1ms 4 (real 2): o Terraform da Azure cria ¼ da memória (9× B1ls para 17,5 GiB). Tabela pela especificação oficial | nós | ✅ feito (develop `ba92bce`) |
| 6 | M13: score v4.3 | Tabela de antes e depois pronta (§5, item 6), sobre a política v5 que o MCP pôs na develop (PR #37). Falta a decisão | usuário com o MCP; a tabela é nossa | decisão (tabela pronta) |
| 7 | M1: segurança | Revogar a chave da service account do GCP que vazou nas branches órfãs; trocar `MCP_TOKEN`, o token do inspector e a `GCP_BILLING_API_KEY` | usuário (console e segredos do cluster) | decisão |
| 26 | Análise morta por reinício do servidor (`09a7ab29`) | Deploy ou restart no meio da análise mata o job; a tela espera 10 min até a varredura marcar `TIMEOUT`, sem dizer o motivo. Sinal de vida a cada 30 s e varredura que encerra em 3 min, com "reanalise" | nós; o tempo de encerramento do pod é do Luiz (infra) | ✅ feito (develop `1a767ad`, front `1ab7259`) |
| 27 | Região padrão do diálogo do Terraform | São Paulo fixo nos quatro destinos; a análise cotou em outra região. Passa a vir da região onde a análise cotou cada destino | nós (back e front) | ✅ feito (develop `1a767ad`, front `1ab7259`) |
| 28 | Texto do Kubernetes no pacote da análise inteira | Ainda diz "plano de controle fora do MVP"; o cluster sai no pacote do plano de migração | nós | ✅ feito (develop `1a767ad`, front `1ab7259`) |
| 29 | NAT quando a rede de origem não é espelhada | Escopo com cluster e sem subrede espelhada (fatura sem ID) sai sem NAT: os nós privados não baixam imagem | nós | ✅ feito (develop `1a767ad`, front `1ab7259`) |
| 30 | SSD da Azure de referência | 0,088 contra 0,0803 oficial (Premium SSD v2, eastus) | nós | ✅ feito (develop `1a767ad`, front `1ab7259`) |
| 31 | Câmbio no develop e na produção | `sync_fx` sem agendamento: develop com taxa de 14/10, banco local vazio (5,70 do config contra 5,0453 do BCE em 19/10) | nós; produção com ordem | ✅ develop (19/10, BRL/USD 5,0453); produção: com ordem |

## 2. Faça assim que possível

| # | item | resumo | de quem | estado |
|---|---|---|---|---|
| 8 | M3: completar a análise sem rodar o LLM | Anexar inventário ou reanalisar recalcula o determinístico e reaproveita a narrativa; o LLM só roda se o resultado mudar. A narrativa antiga não pode citar número que mudou | nós | ✅ feito (develop `803f6a2`) |
| 9 | M11 / ponto 2 do MCP: economia comprovada × estimada | Somar por provedor a economia por preço oficial e a por premissa, com fonte e data de cada zero. Campo aditivo, fora dos arquivos travados; aviso ao MCP; a tela é do front | nós (back), front | ✅ feito (back e front) (develop `803f6a2`) |
| 10 | Ponto 5 do MCP: caso Azure de US$ 471.870 | 55,66% de cobertura de preço; conferir capacidade, horas e licença dos dois pools que fazem a maior parte da economia | nós | ✅ feito (VM pelo medidor; licença ao custo de hoje) (develop `803f6a2`) |
| 11 | D3 e D4: rótulos errados | Confiança do banco "low" com resto zero; `catalog_alias` contado como tipo inferido (alerta falso na OCI; Azure 27,8%) | nós | ✅ feito (develop `803f6a2`) |
| 12 | Terraform: gateways | Nenhum gerador cria internet gateway nem NAT; sem NAT a subrede do destino não sai para a internet | nós | ✅ feito (develop `803f6a2`) |
| 13 | Terraform: entrada do banco fora da AWS | Criar no destino a regra de entrada do banco (tcp/5432 a partir da rede de origem); hoje só o aviso | nós | ✅ feito (OCI e GCP; HeatWave segue com aviso) (develop `803f6a2`) |
| 14 | Terraform: discos pelo inventário | O tamanho de cada volume do inventário, no lugar de custo ÷ taxa (78 GB estimados na `f67fcd01`) | nós | ✅ feito (develop `803f6a2`) |
| 15 | Terraform: EKS → OKE (e AKS, GKE) | Cluster e node pool. Na `f67fcd01` tudo roda no EKS e o pacote entrega VMs soltas. Reverte o "plano de controle fora do MVP" | usuário decide; nós | ✅ feito (decidido pelo usuário em 19/10) (develop `803f6a2`) |
| 16 | Ponto 1 do MCP: franquia das chaves da OCI | 20 versões de chave HSM grátis, depois US$ 0,5334 por versão por mês. Premissa: 1 chave da origem = 1 versão | nós | ✅ feito (develop `803f6a2`) |
| 17 | Curadoria no develop | Fila: Kiro, Azure Bastion, EC2 - Other. Falta `CURADORES` no deploy do develop (Swagger dá 403) | usuário aprova; Luiz (infra) | código feito (DE-PARA e EC2 - Other, develop `803f6a2`); fila e CURADORES: decisão |
| 18 | D2: critério do empacotador | Penalidade de desperdício sobre o custo pela fração ocupada (F1 a 52,41 no lugar de D2as_v5 a 25,18). Medir antes fração × máquina inteira | nós; a decisão é do usuário | medido (§5, item 18); decisão |

## 3. Se não der: a apresentação do MVP não precisa disso

| # | item | resumo | de quem | estado |
|---|---|---|---|---|
| 19 | Terraform: balanceador, Vault, OCIR e zona DNS | Hoje o pacote diz "crie à parte". O conteúdo (segredos, imagens, registros DNS) não viaja pelo Terraform de jeito nenhum | nós | ✅ registro, cofre e zona DNS feitos (com o item 15); balanceador avulso: a fazer |
| 20 | Ponto 4 do MCP + M4: compromisso vigente, transição e payback | Ler a data de fim da reserva e do Savings Plan (o CUR 2.0 traz) e mostrar à parte o que falta pagar; custo de transição; payback com o custo da migração informado | usuário decide; nós | decisão |
| 21 | M12: resto da precisão da OCI | E3/E4/E5 com burst como destino de origem burstable; sync de banco por API; faturas GCP sem região | nós | a fazer |
| 22 | Ponto 6 do MCP: `in_memory` no seed | A lista inicial do `seed_service_metadata.py` usa `in_memory`; o seed do DE-PARA a regrava com `in_memory_database`. Sem efeito | nós | a fazer |
| 23 | M2: recálculo diário | CronJob no infra com o sync de preços e o câmbio | nós (PR no infra); usuário faz o merge | a fazer |
| 24 | M7 e M8: Terraform pelo agente, parecer do agente na tela | Aguardam resposta | usuário | decisão |
| 25 | M6: coleta automática do inventário | Só com muito tempo e risco de o cliente não gostar | nós | a fazer |
| 32 | "Networking" do GCP sem tipo nem equivalente | Mistura de verdade na fatura real (balanceador 55%, VPN 35%, Private Service Connect 8%): uma linha no DE-PARA rotularia tudo errado. O custo já sai certo por categoria; falta o serviço misto com equivalentes por parte | nós | a fazer (depois do MVP) |

## 4. Diagnóstico do Terraform da `f67fcd01` (18/10)

Geração `c5bb907d` (OCI, `sa-saopaulo-1`, os 11 serviços, só o serviço). Lido no
develop, só leitura.

| serviço | no inventário | no pacote | diagnóstico | item |
|---|---|---|---|---|
| AmazonRDS | 1 PostgreSQL 16.4, db.t3.medium | Oracle Autonomous Database | falha lógica | 1 |
| AmazonS3 | 3 buckets, um em cada aplicação | nenhum, sem aviso | falha lógica | 2 |
| AmazonVPC | 3 VPCs, 15 subredes, 8 grupos, 3 internet gateways, 1 NAT | VCN e 4 subredes; aviso "nenhum recurso" | aviso errado; gateways: limite do gerador | 3, 12 |
| AmazonRoute53 | 1 zona hospedada, fora das aplicações | nada; aviso "nenhum recurso" | aviso errado; a zona: limite do gerador | 3, 19 |
| AmazonEKS | 1 cluster, 2 nós, 4 volumes de PVC | 3 VMs ARM soltas e 1 disco de 78 GB estimado | limite do gerador (fora do MVP) | 14, 15 |
| AWSELB | 1 ALB, 2 listeners | nada | limite do gerador; no EKS o balanceador costuma nascer do ingress | 19 |
| AWSSecretsManager, AmazonECR | 18 segredos e 18 repositórios (1 de cada na aplicação) | nada | limite do gerador; valor e imagem não viajam: limite real | 19 |
| AWS Cost Explorer, AWSConfig | gestão da conta | nada | correto; falta dizer que não é infraestrutura | 3 |
| AmazonEC2 | 2 nós no escopo e o srv-openvpn | 3 VMs; o openvpn fica (ferramenta de administração) | correto; aviso de 146% inconsistente | 4 |

## 5. Análises de impacto (escritas antes do código)

### Item 1 — banco do destino pelo motor medido

**Conferido nos quatro geradores (18/10):**

| destino | o que o gerador faz | veredito |
|---|---|---|
| AWS | motor medido vira `engine` literal; sem motor, a variável carrega motor e versão | certo |
| GCP | `database_version` (ex.: `POSTGRES_16`) cobre PostgreSQL, MySQL e SQL Server; Oracle sai como "não gerado" (o GCP não tem) | certo |
| Azure | só PostgreSQL Flexible; MySQL e SQL Server medidos saem como "não gerado", declarado | limite declarado, não erro (fica para depois) |
| OCI | **todo** banco vira Autonomous Database (motor Oracle), com `cpu_core_count` = vCPU (no x86, 1 OCPU = 2 vCPU: o dobro). Com banco do inventário, nem o aviso de troca de motor aparece | **erro**: produto de outro motor, tamanho dobrado |

**O que muda (gerador da OCI e regra de versão):**

- PostgreSQL → `oci_psql_db_system`, forma `PostgreSQL.VM.Standard.E5.Flex`. OCPU = vCPU ÷ 2, a mesma conta da cotação (US$ 0,098 por OCPU = 0,049 por vCPU). Memória = a da classe da origem, entre 16 GB e 64 GB por OCPU (limites da forma, docs da Oracle de 18/10); sem a classe, 16 GB por OCPU. Armazenamento otimizado regional. Alta disponibilidade = 2 nós, como na cotação.
- MySQL → `oci_mysql_mysql_db_system` (HeatWave), forma `MySQL.N` com o menor N ≥ vCPU (ECPU = vCPU, a premissa da cotação). Com alta disponibilidade, `is_highly_available` (3 instâncias, como na cotação). A versão fica sem default: a OCI só aceita a versão completa do catálogo dela (8.4.x, 9.x).
- Oracle → Autonomous, como hoje, com `cpu_core_count` = vCPU ÷ 2.
- Motor desconhecido no caminho agregado (sem inventário nem export detalhado) → PostgreSQL, com a premissa "motor não informado", como a Azure já faz. SQL Server, e grupo medido sem motor → "não gerado", declarado, como nas outras nuvens (a OCI não tem SQL Server gerenciado).
- Versão: o PostgreSQL da OCI aceita 14, 15 e 16. O aviso de versão passa a dizer o que aquele destino aceita (hoje diz "12 a 16" até quando a versão está dentro).
- Variável nova `db_admin_username`, sem default, para PostgreSQL e MySQL. A senha segue como hoje.

**Quem é afetado:**

- Só pacotes com destino OCI que têm banco. AWS, Azure e GCP saem byte a byte iguais (a caracterização trava isso).
- Caracterização: os pacotes `aws>oci`, `gcp>oci`, `azure>oci` e `gcp_legado>oci` mudam nos arquivos com banco; o esperado é regenerado no mesmo commit, com o motivo.
- Testes que esperam o Autonomous (`test_terraform_com_topologia`, `test_terraform_escopo_pacote`, `test_escopo_de_migracao`) passam a esperar o recurso do motor.
- Custo e análise: nada muda. A análise já cotava o PostgreSQL e o MySQL da OCI; o Terraform passa a casar com a cotação.
- Agente e MCP: o blueprint não ganha chave; muda só o HCL e o texto.
- Validação: `terraform validate` com o provider da OCI fixado (6.37) nos pacotes novos.
- Risco: forma e versões vêm do catálogo atual da OCI. Se a Oracle mudar a lista, o pacote para no `plan`, não no meio do `apply`; o comentário do recurso diz de onde veio a forma.

### Item 2 — buckets do escopo pelo inventário

**O defeito, nas quatro nuvens:** no pacote do escopo (o único que a tela gera), o bucket
sai do custo das linhas da fatura ligadas a ele. Quando a fatura não liga o custo do
armazenamento de objetos a cada bucket (Cost Explorer, fatura da AWS, tabela de custos do
GCP, export da Azure sem ID de recurso), o escopo fica com custo zero e o pacote sai **sem
bucket nenhum**. Nem aviso aparece, porque bucket conta como "tipo que o pacote cria". Na
`f67fcd01`: 3 buckets no inventário, 0 no pacote. Com ID de recurso, sai **um** bucket
agregado com o tamanho somado, e não um por bucket.

**O que muda:**

- O recorte do escopo leva os buckets do inventário que estão no escopo (nome e o custo
  ligado a cada um). Vale para qualquer origem: o caso de uso já lê o inventário na hora
  de gerar.
- O pacote cria **um bucket por bucket do inventário**, no lugar do agregado. O nome de
  origem vai no comentário. O volume de referência sai do custo ligado a ele, quando
  existe; sem custo ligado, o comentário diz que a fatura não liga custo àquele bucket
  (o bucket não tem tamanho declarado em nenhuma nuvem: é só referência).
- Escopo com custo de objetos e sem bucket no inventário: segue o agregado de hoje.
- O README do escopo passa a dizer "buckets saem do inventário".

**Quem é afetado:**

- Só o pacote do escopo (`escopo` na requisição) com bucket no escopo, nos quatro destinos.
  O pacote da análise inteira não muda: o payload gravado não guarda os buckets do
  inventário (fica anotado como limite).
- Caracterização (pacotes sem topologia): não muda.
- Testes do escopo com bucket passam a esperar um recurso por bucket.
- Custo e análise: nada muda; o bucket do Terraform não tem preço.
- `_fora_do_pacote`: o S3 deixa de ser "criado" sem ser criado; nada a mudar ali.

### Item 3 — avisos verdadeiros sobre o que fica fora do pacote

**O defeito:** "X: nenhum recurso dele no inventário das unidades do escopo" saía sempre
que nenhum recurso das aplicações escolhidas ficava ligado ao serviço. Três situações
diferentes caíam na mesma frase:

1. o recurso está no inventário, mas fora das aplicações (a zona do Route53 da `f67fcd01`);
2. o recurso é da fundação de rede (VPC, subredes, gateways), que o pacote espelha, mas que
   não pertence a aplicação nenhuma: a frase dizia "não cria" enquanto o pacote criava a rede;
3. o serviço não tem infraestrutura própria (Cost Explorer, Config, Support).

Para saber a qual serviço da fatura um recurso pertence, sem linha de custo ligada a
ele, existe uma tabela de tipo → serviço. Ela só cobre AWS e GCP, e não conhece zona de
DNS nem a rede. Então o defeito existe nas quatro origens.

**O que muda:**

- A frase passa a dizer o que se sabe: (1) "N recurso(s) no inventário (tipo nativo),
  fora das aplicações do escopo; o pacote não o cria"; (2) "a rede de origem vira a rede
  do pacote pelo espelho (as subredes que as máquinas e os bancos usam); gateways não são
  criados"; (3) "nenhum recurso dele no inventário: serviço da conta (gestão, cobrança),
  sem infraestrutura própria, ou um tipo que o inventário não lista".
- A tabela de tipo nativo → serviço ganha o que o inventário da AWS traz e a fatura cobra
  com nome conhecido: zona e health check do Route53 (`AmazonRoute53`); VPC, subrede,
  internet gateway, VPN, Transit Gateway, ACL e tabela de rotas (`AmazonVPC`). Azure e
  OCI ficam com a frase geral (3) até haver tabela delas, conferida com os nomes que os
  parsers dão aos serviços (anotado como limite).

**Quem é afetado:**

- Só o texto da lista "o que ficou de fora" no pacote do escopo com "só o serviço" (README,
  `unsupported_items`).
- A tabela nova só liga recursos sem linha de custo a um serviço. Recurso de fundação não
  pertence a aplicação, então não entra nem sai do recorte; a zona de DNS sem aplicação,
  idem. O recorte, a cotação do escopo e o catálogo de aplicações não mudam.
- Dependências da fronteira: calculadas entre recursos das aplicações; a fundação não é
  ponta de dependência. Conferir no teste.

### Item 4 — ocupação de origem burstable na mesma base do empacotamento

**O defeito:** o empacotamento (`equivalence_matcher._packing_pela_base`) dimensiona origem
burstable em destino sem crédito pela linha de base (uma t4g entrega 40% de cada vCPU sem
crédito). Os avisos do Terraform comparam a máquina escolhida com a vCPU **nominal**. Na
`f67fcd01`, o pool T4G (2,93 vCPU nominal) cabe em 2 vCPU pela base (1,17), e o aviso diz
"o uso ocupa 146%, avalie uma máquina a mais". O mesmo vale para qualquer origem burstable
(família T da AWS, série B da Azure, núcleo compartilhado do GCP) em qualquer destino sem
crédito. O aviso de "máquina menor" (fragmentação) erra do mesmo jeito.

**O que muda:** os dois avisos usam a base que a própria análise gravou
(`premissas.burstable_pela_base`, por pool) quando o destino não tem crédito; a memória
fica como está. A premissa da máquina diz que a origem é burstable e qual base valeu.
Análise anterior à regra (16/10) não grava a base e foi dimensionada pela nominal: segue
lida pela nominal, sem reinterpretar o que já foi feito.

**Quem é afetado:** só o texto (premissa e avisos) dos pools burstable no Terraform. A
máquina escolhida, o número de instâncias e o custo não mudam (vêm da análise). A
caracterização muda só onde há pool burstable com destino sem crédito.

### Item 5 — D1: especificação das máquinas pequenas (Azure e AWS)

**O defeito:** o parser estrutural (a letra da família dá a memória por vCPU) erra onde a
tabela oficial foge da regra:

- Azure B v1 de 1 vCPU: B1ls 0,5 GiB (dava 2), B1s 1 (dava 2), B1ms 2 (dava 4). As
  demais B v1 e toda a B v2 (Bsv2, Basv2, Bpsv2) conferem.
- AWS, geração anterior: t1.micro (1 vCPU, 0,613 GiB; dava 2 vCPU e 8), m1 (small 1/1,7,
  medium 1/3,75, large 2/7,5, xlarge 4/15), c1 (medium 2/1,7, xlarge 8/7), m2 (xlarge
  2/17,1, 2xlarge 4/34,2, 4xlarge 8/68,4) e m3 (medium 1/3,75, large 2/7,5, xlarge 4/15,
  2xlarge 8/30). A família T atual já foi corrigida em 17/10.
- GCP: a fatura cobra vCPU e memória separadas, sem nome de máquina para interpretar; o
  núcleo compartilhado (e2-micro, f1-micro, g1-small) já tem a tabela própria. OCI: a
  forma flexível traz OCPU e memória no nome.

**O que muda:** tabelas com a especificação oficial dessas máquinas, consultadas antes
da regra estrutural.

**Quem é afetado:**

- **Azure como destino:** a memória do catálogo é gravada no sync de preços. Depois do
  sync, o empacotador deixa de escolher B1ls para pool que precisa de memória (o caso do
  D1: 9× B1ls para 17,5 GiB) e o Terraform da Azure cria a memória que o pool mede. A
  cotação da Azure sobe onde a B1 era escolhida. **Precisa de sync da Azure em cada
  ambiente** (develop e local), que só rodo com autorização.
- **Azure como origem** (fatura com B1s, B1ls, B1ms) e **AWS como origem** (m1, c1, m2,
  m3, t1, também nas classes de banco `db.m1.*`, `db.m3.*`): a capacidade medida fica
  certa; cai a memória desses pools e o destino é cotado pelo que a origem tem.
- Análises gravadas não mudam; reanalisar recalcula.
- Caracterização: muda só se algum payload congelado tiver essas máquinas.

### Item 6 — M13: antes e depois da nota (para a conversa com o MCP)

Simulação só de leitura, em 18/10, sobre as 92 análises concluídas do develop (88 com
cenários, 264 cenários, todos com status `partial`). **Hoje** = nota que o código da
develop dá agora (política `score-7rs-v5`, do PR #37), recalculada do payload gravado.
**v4.3** = a proposta do council aplicada aos mesmos fatores:

- portão financeiro: economia ≤ 0 → teto 39; economia abaixo de 5% do custo atual (a
  faixa de tolerância; **parâmetro a decidir**) → teto 59;
- compatibilidade fora da média técnica (refatoração, portabilidade e dependências com
  os pesos de hoje, renormalizados);
- critério sem evidência vale 50 na parte que falta, sem redistribuir peso;
- cobertura da análise como está.

Faixas da tela: < 40 Não migrar, < 60 Avaliar, < 80 Viável, ≥ 80 Migrar.

| situação | casos | hoje | v4.3 |
|---|---|---|---|
| destino **mais caro** que hoje | 121 | 74 saem Viável ou Migrar (até 81,5), 90 com Avaliar ou melhor | todos ≤ 39 (Não migrar) |
| destino com economia ≥ 20% | 84 | 20 saem Não migrar (nota parcial conservadora) | nenhum abaixo de 40 |
| destino recomendado | 84 | Migrar 48, Não migrar 20, Viável 8, Avaliar 8 | Viável 55, Migrar 20, Avaliar 9 |

Exemplos (mesma fatura, várias análises com o mesmo resultado):

| fatura | destino | economia | hoje | v4.3 |
|---|---|---|---|---|
| fatura AWS pequena (US$ 378, a da `f67fcd01`) | OCI | 42,9% | 89,7 Migrar | 84,4 Migrar |
| | Azure | −5,7% | 72,6 Viável | 39 Não migrar |
| | GCP | −12,0% | 70,5 Viável | 39 Não migrar |
| Cost Explorer AWS (US$ 92.103), sem evidência técnica suficiente | OCI | 34,7% | 34,4 Não migrar | 59,4 Avaliar |
| Cost Explorer AWS, com evidência | OCI | 40,8% | 86,5 Migrar | 77,4 Viável |
| | Azure | −10,2% | 66,6 Viável | 39 Não migrar |
| Azure (US$ 471.870) | OCI | 34,8% | 85,0 Migrar | 75,0 Viável |
| | AWS | 12,9% | 77,3 Viável | 67,7 Viável |
| GCP cost table (US$ 4.882) | OCI | 51,0% | 39,5 Não migrar | 64,5 Viável |
| billing AWS de US$ 430 | OCI | 0,4% | 52,5 Avaliar | 58,4 Avaliar (teto 59) |
| OCI (US$ 296.260) | AWS/Azure/GCP | −84% a −98% | 4 a 21 Não migrar | 29 a 37 Não migrar |

**O que a tabela mostra:**

1. Hoje, um destino 5% a 31% mais caro sai "Viável" em 74 cenários; a v4.3 corta todos.
2. Hoje, a nota parcial conservadora derruba destinos com 35% a 51% de economia para
   "Não migrar" (20 recomendados); a v4.3 os põe em Avaliar ou Viável. É efeito da v5 com
   a regra "sem evidência suficiente, soma só o comprovado", que não estava no council.
3. O topo cai de 85–93 para 72–85: "Migrar" passa a exigir evidência, não só economia.
4. A recomendação (quem vence) não muda: com notas parciais ela segue pela economia.

Decisões para a conversa: a faixa de tolerância (5%?), se a ausência vale 50 também no
eixo de cobertura, e se a v4.3 entra sobre a v5 (como simulado) ou a substitui. A
mudança exige subir `AUTHORITY_VERSION` (arquivo travado) e aviso ao MCP. Tabela
completa: `m13_saida.json` (scratchpad da sessão), gerada por `m13_pod.py`.

### Itens 15 e 19 — Kubernetes, registro de imagens, segredos e DNS no Terraform do escopo

Decidido pelo usuário em 19/10: o Kubernetes é essencial e não pode ficar fora do pacote,
assim como os outros serviços gerenciados que o inventário mostra. Referência: o pacote da
análise `b01f59da` (o mesmo inventário da `f67fcd01`, 11 serviços marcados).

**O que o inventário já traz (AWS, Azure, GCP):** o cluster com versão e subredes; cada
nó ligado ao cluster ("membro de") e ao grupo de nós (tag `eks:nodegroup-name`,
`kubernetes.azure.com/agentpool`, `goog-k8s-node-pool-name`); o balanceador que o
controlador do cluster cria para um Service, também ligado ao cluster; registro de imagens
e segredos marcados como componente; a zona de DNS pelo tipo nativo. Na `f67fcd01`:
`eks-kitconta-prd` 1.35, grupos `ng-infra-arm` (t4g.xlarge) e `ng-microservices-arm`
(r7g.large), o NLB do Kong (Service `LoadBalancer`, 80 e 443), 18 repositórios, 18
segredos e a zona `kitconta.internal.`.

**O que muda:**

- **Cluster:** um cluster gerenciado por cluster do escopo, com os grupos de nós dele
  (OKE, EKS, AKS, GKE). Os pools de máquinas que são nós do cluster deixam de virar VM
  avulsa e viram o grupo de nós, com a máquina e a quantidade que a análise cotou; pool
  só em parte no cluster é dividido pela contagem de nós. A versão medida vira o default
  da variável quando o destino aceita o formato; senão a variável fica sem default.
  Some a frase "plano de controle fora do MVP".
- **Balanceador do cluster:** não vira recurso. Ele nasce do Service do cluster no destino;
  o pacote diz qual Service e quais portas.
- **Registro de imagens:** um repositório por repositório de origem (OCI, AWS); na Azure um
  registro; no GCP um repositório Docker. As imagens não viajam pelo Terraform (copiar com
  `skopeo`/`crane`).
- **Segredos:** na AWS e no GCP, o segredo vazio com o nome da origem; na Azure e na OCI,
  o cofre (o segredo exige valor na criação), com os nomes no README. Valor de segredo
  nunca viaja.
- **Zona de DNS:** uma por zona de origem; privada, ligada à rede do pacote, salvo quando
  o inventário diz que é pública. Os registros não viajam.
- **"Só o serviço":** registro, segredo e zona sem aplicação (sem tag) vão junto quando o
  serviço deles foi escolhido: eles só migram pela escolha do serviço.

**Quem é afetado:**

- Só o pacote do escopo com cluster ou com esses componentes, nos quatro destinos. O
  pacote sem escopo e a caracterização não mudam.
- Custo e análise: não mudam (o Terraform não tem preço). As VMs que viram nós somem da
  lista de máquinas; a capacidade é a mesma.
- `unsupported_items` encolhe; o diálogo do front, que mostrava a lista inteira, fica
  menor (o botão escondido pelo excesso de avisos é do front, item à parte).
- Variáveis novas sem default onde a análise não sabe (versão do Kubernetes quando o
  destino exige formato próprio, papel IAM na AWS).
- Validação: `terraform validate` com os providers fixados nos quatro destinos.

### Item 12 — saída para a internet: gateway de internet e NAT

**O defeito, nos quatro destinos:** o pacote com a rede espelhada cria a rede e as
subredes, mas nenhuma saída. Subrede privada sem NAT não baixa imagem, pacote nem
atualização; o nó do Kubernetes não sobe. Subrede pública sem gateway de internet não
recebe tráfego. A origem tem os dois (`f67fcd01`: 3 internet gateways e 1 NAT).

**O que muda (só no pacote com topologia, onde as subredes vêm do espelho):**

- Recurso novo de saída da rede: subrede privada sai pela NAT; subrede pública, pelo
  gateway de internet.
- OCI: internet gateway e NAT gateway; a tabela de rotas padrão da VCN aponta para a NAT;
  a subrede pública ganha a tabela com o gateway de internet.
- AWS: internet gateway, NAT gateway com IP elástico numa subrede pública própria (/28
  livre no fim da faixa da rede: o NAT da AWS só funciona em subrede pública) e as
  tabelas de rotas privada e pública associadas às subredes.
- Azure: NAT gateway com IP público, associado às subredes privadas (menos a delegada ao
  banco); a Azure não tem gateway de internet à parte (IP público na VM).
- GCP: Cloud Router com Cloud NAT para todas as subredes; a rota padrão para a internet
  já vem com a VPC.
- A frase da VPC no "o que fica fora" passa a dizer que internet e NAT são recriados e
  que a VPN não é.

**Quem é afetado:**

- Pacotes com topologia, nos quatro destinos. Sem topologia, nada muda (caracterização).
- Custo: o NAT do destino tem preço (OCI grátis; AWS, Azure e GCP cobram por hora e por
  GB). A análise já cota o NAT da origem pela quantidade medida; o pacote só passa a
  criar o que foi cotado.
- `terraform validate` nos quatro destinos.

### Item 13 — entrada do banco fora da AWS

**O defeito:** só a AWS anexava ao banco os grupos de segurança espelhados da origem. Nos
outros destinos o pacote avisava "libere a porta a partir da rede de origem", e quem
ficou na origem não alcançava o banco.

**O que muda, destino por destino:**

- **OCI:** o banco recebe os NSGs espelhados dos grupos dele na origem (PostgreSQL pelo
  `network_details.nsg_ids`, Autonomous pelo `nsg_ids`). A regra da origem chega como
  ela era: de outro grupo do pacote vira NSG → NSG; de grupo que ficou na origem vira o
  CIDR dele, que é a liberação que o aviso pedia. Os nós do OKE também levam os NSGs do
  pool, senão a regra "5432 a partir do grupo dos nós" não casaria. O HeatWave (MySQL)
  não aceita NSG no provider fixado: para ele o aviso continua.
- **Azure:** o banco delegado já aceita a entrada da `VirtualNetwork`, que inclui a rede de
  origem ligada por VPN. O aviso vira essa informação.
- **GCP:** o Cloud SQL fica na rede do serviço (acesso privado). O pacote passa a exportar
  e importar as rotas personalizadas no peering desse acesso; com a VPN, quem ficou na
  origem alcança o banco.
- **AWS:** como está.

**Quem é afetado:** pacotes com topologia e banco, na OCI e no GCP. A caracterização não
muda (sem topologia). `terraform validate` nos quatro destinos.

### Item 14 — discos do escopo pelo inventário

**O defeito, nas quatro nuvens:** no pacote do escopo o disco sai do custo das linhas
(custo ÷ taxa), num disco único estimado (78 GB na `f67fcd01`), embora o inventário traga
cada volume com tamanho, tipo e se é de boot. Pior: os volumes da `f67fcd01` são 2 discos
de boot de nós e 4 volumes persistentes do Kubernetes (PVC); nenhum deles é disco avulso.

**O que muda (pacote do escopo com inventário):**

- O recorte leva os discos do escopo com tamanho, categoria, boot e cluster.
- Disco de boot não vira disco: é o disco raiz da máquina (ou do nó).
- Volume persistente do cluster não vira disco: nasce do driver de armazenamento (CSI) do
  cluster no destino; o pacote diz quantos e quantos GB, para recriar a StorageClass.
- O resto vira disco pelo tamanho medido, agrupado por categoria e tamanho (`count`), no
  lugar do disco estimado pelo custo.
- Escopo sem disco no inventário: o disco pelo custo, como hoje.

**Quem é afetado:** só o pacote do escopo com discos no inventário, nos quatro destinos.
Caracterização igual. O custo da análise não muda.

### Item 16 — franquia das chaves na OCI (ponto 1 do MCP)

**O defeito:** a taxa de chave da OCI é zero sempre. O cofre padrão da OCI não cobra as
primeiras 20 versões de chave protegida por HSM; acima disso, US$ 0,5334 por versão por
mês (lista de preços da Oracle, conferida em 16/10, já anotada no seed).

**O que muda:** a franquia deixa de ser regra só do egress e vira tabela por categoria
(egress: 10 TB na OCI, 100 GB na AWS e na Azure; chaves: 20 na OCI). A taxa de chave da
OCI passa a 0,5334 por mês, com as 20 primeiras grátis. Premissa: cada chave da origem é
uma versão (a fatura não diz quantas versões cada chave tem).

**Quem é afetado:** só a OCI como destino, e só faturas com mais de 20 chaves. Precisa
gravar a taxa nova (`seed_category_rates`) em cada ambiente, com autorização. Análises
gravadas não mudam.

### Item 11 — D3 e D4: dois rótulos errados

- **D3:** a cotação do banco marcava confiança "low" sempre que o destino não tinha a
  razão do resto (armazenamento, backup), mesmo com resto zero (banco todo em instância,
  como no mock da Azure). Passa a "low" só quando há resto e ele fica ao custo da origem.
- **D4:** `premissas.tipo_servico_inferido` contava o apelido do catálogo
  (`catalog_alias`) como tipo inferido, enquanto o resto do sistema (`CURATED`, o score e
  a portabilidade) o trata como curado. O alerta "tipo inferido concentrado" acusava
  falso (OCI; Azure 27,8%). Passa a usar a mesma regra.

**Quem é afetado:** só rótulos. D3 pode subir a confiança do banco (e a da análise, que
pesa pelo valor cotado) onde o resto é zero; D4 tira o alerta falso da reconciliação. Os
números de custo, economia e nota não mudam. Análises gravadas só mudam ao reanalisar.

### Item 9 — M11 (ponto 2 do MCP): economia comprovada × estimada

**O que muda (backend, aditivo):** cada linha de `resumo_executivo.por_provedor` ganha
`economia_por_fonte` — a economia de cada parte cotada somada pela confiança da cotação:
`preco_oficial` (preço da API do provedor, ou taxa oficial gravada com fonte e data) e
`estimada` (taxa de referência, razão de taxas, premissa e o compromisso de compute que
sai da origem) — e `itens_gratis`, a lista do que custa zero no destino (NAT, IP, segredos
na OCI) com a categoria, a economia, a fonte e a data da taxa. A soma das duas partes é a
`economia_mensal` de sempre.

**Quem é afetado:** o payload ganha campos; nenhum número muda. O agente (MCP) recebe os
campos no snapshot: nota ao MCP. A tela é do front (passo seguinte). Análises gravadas só
ganham os campos ao reanalisar.

### Item 10 — caso Azure de US$ 471.870 (ponto 5 do MCP): VM pelo nome do medidor

**O que a auditoria achou (develop, só leitura, análise `d2993418`):** o pool "general"
de brazilsouth custa US$ 77.487/mês e entra com 126 vCPU e **0 GB** de memória; a OCI o
cota a US$ 1.428. São US$ 614 por vCPU-mês na origem, dez vezes o normal. Sozinho, ele faz
~US$ 76 mil dos US$ 164 mil de economia da OCI.

**Causa, geral:** a exportação da Azure traz a VM só pelo nome do medidor
(`Virtual Machines | Edsv5 Series | E16ds v5`), sem o SKU (`Standard_E16ds_v5`) nem a
vCPU. O agregador lê a vCPU pelo parser da AWS no tipo de uso e pelo da Azure só no SKU:
nenhum entende "E16ds v5". As horas de 22 mil instâncias E16ds v5 (16 vCPU cada) ficam
sem capacidade, e a família vira "general". Vale para toda fatura da Azure exportada com o
medidor no lugar do SKU.

**O que muda:** o tamanho da VM sai também do nome do medidor ("E16ds v5" →
`Standard_E16ds_v5`, "D16s v5 Spot" → `Standard_D16s_v5`, "B2ms" → `Standard_B2ms`), com
as regras de sempre do parser da Azure (geração v1/v2 de D e E recusada, tabela da B v1).
Só para linha da Azure. A família do pool sai do mesmo tamanho (`EDSV5`, `DSV5`).

**Quem é afetado:** análises de origem Azure cuja fatura não traz o SKU. A capacidade da
origem passa a ser a real; o destino é cotado por ela: o custo de destino sobe e a
economia falsa some. Os pools passam a se chamar pela família. Precisa só reanalisar (o
agregador relê as linhas). Medido antes e depois nas faturas do develop, abaixo.

**Achado na medição, também geral:** a licença (SQL Server, sistema operacional) entrava
no custo de origem do pool que o destino substitui, mas o destino é cotado só pela
capacidade: a licença virava economia. Antes isso aparecia pouco porque a licença caía num
pool sem máquina, descartado da cotação (e o custo dela sumia da conservação). Agora:

- licença sem máquina no próprio pool vai para o pool de maior capacidade do mesmo projeto
  e região (a conservação fecha);
- o custo de origem que o destino substitui é só o de máquina: a licença fica no saldo
  mantido ao custo de hoje, a regra que a tela de equivalentes já mostra ("vai com a
  máquina, ao custo de hoje").

Afeta toda análise com licença cobrada à parte (Azure, GCP): o destino sobe pelo valor da
licença, e a economia cai dele.

**Medido no develop (mesmo banco; código da develop × branch; nada gravado), US$/mês:**

| fatura | destino | economia antes | economia depois | cobertura |
|---|---|---|---|---|
| azure_billing_2026-09 (US$ 471.870) | OCI | 205.524 | 167.771 | 0,64 → 0,61 |
| | GCP | 119.448 | 21.770 | 0,67 → 0,64 |
| | AWS | 102.177 | 2.321 | 0,67 → 0,64 |
| Cost Explorer AWS, CUR, GCP, fatura AWS, OCI | todos | iguais | iguais | iguais |

Na Azure, os pools passam de "general" (126 vCPU, 0 GB) às famílias reais (EDSV5, DSV5,
ESV5, DDSV5, LSV3); somem os alertas `familia_generica_concentrada` e
`preco_vcpu_implausivel`; a conservação fecha; a nota da OCI vai de 88,9 a 85,9 e a
recomendação segue OCI.

### Item 18 — D2, medição (sem mudança de código)

**Pergunta do council (L2):** quanto a cotação pela fração ocupada (capacidade-hora, a
base que o usuário decidiu no PLANO_EMPACOTAMENTO) difere das máquinas inteiras que o
Terraform cria?

**Medido nas análises do develop (19/10, só leitura):** 18 pools, em 8 faturas, têm as
máquinas inteiras custando mais de 1,5 vez o cotado. Todos são pools quase ociosos (0,0 a
0,05 vCPU de média): cotados a centavos, provisionados como uma máquina de ~US$ 70/mês.
Diferença por fatura: de US$ 131 a US$ 1.521/mês (menos de 2% de uma fatura de US$ 92 mil).
Ressalva: só AWS e Azure gravam o preço por hora no payload, e muitos equivalentes não
gravam a quantidade de instâncias; a escolha do SKU (F1 a US$ 52 no lugar de D2as_v5 a
US$ 25) só se mede rodando o empacotador com os candidatos, não pelo payload.

**Decisão do usuário, antes de mudar:** (a) manter a capacidade-hora e só avisar no
Terraform quando a máquina inteira passa de 1,5 vez o cotado (barato, já há o aviso de
ocupação baixa); (b) escolher o SKU pela máquina inteira e cotar pela fração (o council:
"escolher e cotar pela mesma base"); (c) um piso de cotação de uma máquina por pool
ligado. Recomendação: (a) agora e (b) depois de rodar o empacotador com os candidatos de
cada fatura.

### Item 17 — curadoria: o que dá para resolver no código (a aprovação segue com o usuário)

Fila do develop em 19/10: Kiro (AWS), Azure Bastion, EC2 - Other (AWS).

- **Azure Bastion:** o DE-PARA não tinha bastion em nenhuma nuvem. Linhas novas, tipo
  `bastion_host`: Azure Bastion, OCI Bastion, AWS Systems Manager Session Manager e
  Identity-Aware Proxy do GCP (docs oficiais de cada um).
- **Kiro:** IDE com agente de IA da AWS; entra no tipo que já existe,
  `ai_coding_assistant` (Amazon Q Developer, GitHub Copilot, Gemini Code Assist).
- **EC2 - Other:** não é um serviço, é o agrupamento do Cost Explorer para EBS, snapshot,
  NAT, IP elástico e tráfego. Resolvido pelo uso de cada linha (`servico_pelo_uso`), como o
  "Storage" da Azure e o "Networking" do GCP: o tipo vale para o serviço quando um alvo
  tem 60% ou mais do custo; mistura de verdade fica sem tipo.

**Quem é afetado:** faturas com esses nomes passam a ter tipo e equivalentes (Portabilidade,
lock-in, tela de equivalentes); o custo não muda (sai por categoria). O DE-PARA vai a 864
linhas: precisa do `seed_from_csv` em cada ambiente. As três sugestões da fila podem ser
aprovadas (Bastion e Kiro pelo nome novo) ou recusadas (EC2 - Other, resolvido pelo uso):
decisão do usuário. `CURADORES` no deploy do develop segue com o Luiz.

### Item 8 — M3: reanálise sem rodar o LLM quando os números não mudam

**O que muda:** antes de chamar o LLM, o pipeline compara os números que a narrativa cita
(custo atual, recomendado, custo, economia, cobertura e confiança de cada destino, e a nota
e a política do `decision_support`) com os da última análise concluída do mesmo billing.
Iguais: a narrativa anterior é reaproveitada (a autoridade do backend e a decisão são
reaplicadas sobre o payload novo, como sempre), e a análise ganha o aviso
`NARRATIVA_REAPROVEITADA` com o id de onde veio. Diferentes, ou `?nova_narrativa=true` na
rota de início: o LLM roda. O payload gravado é sempre o novo (topologia, preços).

**Quem é afetado:** anexar inventário e reanalisar sem mudança de número deixam de gastar
LLM e terminam em segundos (menos risco de o job morrer num deploy). A narrativa nunca cita
número que mudou: qualquer diferença nos números citados força o LLM. O agente (MCP) não
muda: o snapshot é montado do payload, não da narrativa. O `AnalysisStartOptions` é arquivo
travado da autoridade; por isso a opção entra como parâmetro da rota.

### Ensaio geral de 19/10 — itens 26 a 32

Ensaio com os mocks das quatro nuvens, as massas reais e o inventário (banco local `ost_e2e`,
develop `803f6a2`, front `8eba258`) e as faturas reais do develop recalculadas em memória.
Pipeline, os 24 pacotes Terraform (`validate`) e os números da tela batem com a API; o preço
do banco é igual ao da fonte oficial em todos os 264 pools conferidos. Itens abertos:

**Item 26 — análise morta por reinício.** A análise roda no processo da API (BackgroundTasks).
No deploy, o uvicorn espera as tarefas em andamento e o Kubernetes mata o pod 30 s depois do
aviso: o desligamento do app nem roda. A varredura só encerra o job 10 min depois, com
`TIMEOUT` e sem dizer o motivo. Correção: enquanto a análise (e a geração de Terraform) roda,
o processo grava o sinal de vida (`updated_at`) a cada 30 s numa sessão própria; a varredura
encerra o job ativo sem sinal há 3 min, com "interrompida: o servidor reiniciou; reanalise".
O limite de 10 min passa a contar do início (`started_at`), não do último sinal, para um job
vivo e preso continuar caindo no `TIMEOUT`. Impacto: nenhuma tabela nova; a linha do job só é
escrita no início (transação própria) e no fim, então o sinal não disputa trava com a
análise. Job que termina depois de marcado (laço travado mais de 3 min) grava o resultado
por cima, como hoje. A tela já mostra a falha com "Tentar novamente": passa a aparecer em
~3 min, não em 10. O que evita a falha de vez é do infra: tempo de encerramento do pod maior
que uma análise (~3 min); fica com o Luiz.

**Item 27 — região padrão do diálogo.** A rota `applications` ganha `regioes_de_destino`
(aditivo): para cada destino, a região de preço com mais custo cotado nos pools. O diálogo
usa essa região e, sem ela, continua em São Paulo. Impacto: só o valor inicial do campo;
o usuário troca. Nenhum número muda.

**Item 28 — texto do Kubernetes no pacote da análise inteira.** Troca a frase "O plano de
controle fica fora do MVP" por onde o cluster sai (pacote do plano de migração, com
inventário). O pacote não muda; o README dos pacotes com cluster muda o texto (teste de
caracterização regenerado só nesses).

**Item 29 — NAT sem rede espelhada.** A saída para a internet hoje só nasce com subrede
espelhada. Escopo com cluster de Kubernetes e sem espelho (fatura sem ID: o posicionamento
sai vazio) ficava sem NAT nas quatro nuvens. Passa a nascer também quando o escopo tem
cluster: os nós são privados e precisam baixar imagem. O texto do comentário deixa de dizer
"rede espelhada". Impacto: só soma recursos ao pacote desse caso; pacote sem topologia não
muda (caracterização). A cópia das subredes do cluster continua de fora: mexe no espelho dos
quatro destinos, arriscado para o prazo.

**Item 30 — SSD da Azure.** A referência de `storage_ssd` da Azure passa de 0,088 para
0,0803 (Premium SSD v2, eastus: US$ 0,00011/GiB-h × 730; Retail Prices API em 19/10), no seed
e na taxa que o Terraform usa para voltar do custo ao tamanho. Impacto medido: destino Azure fica
8,75% mais barato no SSD (no mock AWS de US$ 92 mil, SSD de US$ 5.893,74 para US$ 5.378,04;
total Azure de US$ 116.298,59 para US$ 115.782,89); disco estimado de origem Azure fica 9,6%
maior (nenhum pacote da caracterização mudou por isso). Precisa do `sync_prices --providers categories` no develop e na produção.

**Item 31 — câmbio.** Sem código: `sync_fx` no develop e, com ordem, na produção. O agendamento
é o item 23. Medido no banco local: com 5,0453 (BCE, 19/10) no lugar de 5,70, a cost table real
do GCP passa de US$ 4.890 para US$ 5.525 por mês; a economia na OCI vai de US$ 2.305 para
US$ 2.863 e a AWS deixa de sair US$ 133 mais cara para sair US$ 379 mais barata. Câmbio errado
faz a fatura em BRL parecer mais barata e distorce todo destino.

**Item 32 — "Networking" do GCP.** Ver a fila: não é nome que falta, é serviço misto.

## 6. Concluído

| data | item | o que foi feito | commit |
|---|---|---|---|
| 19/10 | Itens 26 a 31 (ensaio geral) | Análise e geração de Terraform com sinal de vida: processo morto encerra em ~3 min com "reanalise" (caso `09a7ab29`); diálogo do Terraform na região cotada (`regioes_de_destino`); texto do Kubernetes no pacote da análise inteira; NAT com cluster sem rede espelhada; SSD da Azure 0,0803. Bateria 2698 verde; CI verde. Develop: `sync_prices --providers categories` e `sync_fx` rodados. Item 32 ("Networking" do GCP) fica para depois do MVP | `1a767ad` |
| 19/10 | Itens 8 a 17, 19 e Kubernetes | Kubernetes, registro, cofre e DNS no Terraform do escopo; NAT e gateway de internet; entrada do banco (NSG na OCI, rotas no GCP); discos pelo inventário; franquia das chaves da OCI; D3 e D4; economia comprovada × estimada (back e front); reanálise sem LLM; VM da Azure pelo medidor e licença ao custo de hoje (caso de US$ 471.870); DE-PARA com Bastion, Kiro e EC2 - Other pelo uso. Bateria completa 2684 verde; CI verde. Develop: DE-PARA 864 semeado, taxa de chave da OCI sincronizada. Front: diálogo do Terraform cabe na tela (sobre a lista do Luiz, `afef18e`) e o bloco de economia por fonte (`7ecc6cd`) | `803f6a2` |
| 18/10 | Itens 1 a 5 | Banco da OCI pelo motor; buckets do escopo pelo inventário; avisos do que fica fora do pacote; ocupação burstable pela base gravada; especificação oficial das máquinas fora da regra (Azure B1, AWS geração anterior). Caso real `f67fcd01` conferido no develop: PostgreSQL 16 na subrede de dados, 3 buckets, VPC e Route53 com a frase certa, T4G a 73% sem aviso. Bateria completa: 2634 testes; CI verde | `ba92bce` |
| 18/10 | Sync da Azure no develop | `sync_prices --providers azure --zonas todas`: 8650 preços; B1ls 2 → 0,5 GiB, B1s 2 → 1, B1ms 4 → 2 (9 regiões) | — |
| 18/10 | Auditoria do MCP | Conferida ponto a ponto: banco por partes, SSD com VPU, DE-PARA da OCI (139 linhas), VPN da Azure e "sem equivalente na OCI" já estavam na develop depois do `684029c` que ela leu; seis pontos seguem abertos (itens 6, 9, 10, 16, 20, 22) | — |
