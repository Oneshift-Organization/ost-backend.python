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
| 1 | Terraform: banco do destino pelo motor medido | Conferidos os quatro geradores (§5). Só o da OCI erra: escreve todo banco como Oracle Autonomous Database, com 1 OCPU por vCPU (o dobro), e a análise cotou OCI PostgreSQL. PostgreSQL passa a `oci_psql_db_system` e MySQL a HeatWave; o Autonomous fica para Oracle. Some o aviso falso "versão 16 fora do que o destino aceita" | nós | ✅ feito, em branch local (aguarda liberação para a develop) |
| 2 | Terraform: buckets do escopo | O bucket só nasce do custo dele; a fatura não liga o custo do S3 ao bucket, e o pacote sai sem bucket e sem aviso (bucket conta como "tipo que o pacote cria"). Passa a sair do inventário, pelo nome | nós | ✅ feito, em branch local (aguarda liberação para a develop) |
| 3 | Terraform: avisos verdadeiros | "AmazonVPC: nenhum recurso no inventário" com 3 VPCs e 15 subredes, e a rede criada; idem Route53 com a zona no inventário. Cost Explorer e Config passam a ser "gestão da conta, não infraestrutura" | nós | ✅ feito, em branch local (aguarda liberação para a develop) |
| 4 | Terraform: ocupação de origem burstable | O empacotador dimensiona burstable pela linha de base; o aviso compara com a vCPU nominal e pede "uma máquina a mais" sem motivo (146% na `f67fcd01`). Mesma base nos dois, dizendo que a origem é burstable | nós | ✅ feito, em branch local (aguarda liberação para a develop) |
| 5 | D1: memória da série B v1 da Azure | B1ls com 2 GiB (real 0,5), B1s 2 (real 1), B1ms 4 (real 2): o Terraform da Azure cria ¼ da memória (9× B1ls para 17,5 GiB). Tabela pela especificação oficial | nós | ✅ feito, em branch local (aguarda liberação para a develop) |
| 6 | M13: score v4.3 | Tabela de antes e depois pronta (§5, item 6), sobre a política v5 que o MCP pôs na develop (PR #37). Falta a decisão | usuário com o MCP; a tabela é nossa | decisão (tabela pronta) |
| 7 | M1: segurança | Revogar a chave da service account do GCP que vazou nas branches órfãs; trocar `MCP_TOKEN`, o token do inspector e a `GCP_BILLING_API_KEY` | usuário (console e segredos do cluster) | decisão |

## 2. Faça assim que possível

| # | item | resumo | de quem | estado |
|---|---|---|---|---|
| 8 | M3: completar a análise sem rodar o LLM | Anexar inventário ou reanalisar recalcula o determinístico e reaproveita a narrativa; o LLM só roda se o resultado mudar. A narrativa antiga não pode citar número que mudou | nós | a fazer |
| 9 | M11 / ponto 2 do MCP: economia comprovada × estimada | Somar por provedor a economia por preço oficial e a por premissa, com fonte e data de cada zero. Campo aditivo, fora dos arquivos travados; aviso ao MCP; a tela é do front | nós (back), front | a fazer |
| 10 | Ponto 5 do MCP: caso Azure de US$ 471.870 | 55,66% de cobertura de preço; conferir capacidade, horas e licença dos dois pools que fazem a maior parte da economia | nós | a fazer |
| 11 | D3 e D4: rótulos errados | Confiança do banco "low" com resto zero; `catalog_alias` contado como tipo inferido (alerta falso na OCI; Azure 27,8%) | nós | a fazer |
| 12 | Terraform: gateways | Nenhum gerador cria internet gateway nem NAT; sem NAT a subrede do destino não sai para a internet | nós | a fazer |
| 13 | Terraform: entrada do banco fora da AWS | Criar no destino a regra de entrada do banco (tcp/5432 a partir da rede de origem); hoje só o aviso | nós | a fazer |
| 14 | Terraform: discos pelo inventário | O tamanho de cada volume do inventário, no lugar de custo ÷ taxa (78 GB estimados na `f67fcd01`) | nós | a fazer |
| 15 | Terraform: EKS → OKE (e AKS, GKE) | Cluster e node pool. Na `f67fcd01` tudo roda no EKS e o pacote entrega VMs soltas. Reverte o "plano de controle fora do MVP" | usuário decide; nós | decisão |
| 16 | Ponto 1 do MCP: franquia das chaves da OCI | 20 versões de chave HSM grátis, depois US$ 0,5334 por versão por mês. Premissa: 1 chave da origem = 1 versão | nós | a fazer |
| 17 | Curadoria no develop | Fila: Kiro, Azure Bastion, EC2 - Other. Falta `CURADORES` no deploy do develop (Swagger dá 403) | usuário aprova; Luiz (infra) | decisão |
| 18 | D2: critério do empacotador | Penalidade de desperdício sobre o custo pela fração ocupada (F1 a 52,41 no lugar de D2as_v5 a 25,18). Medir antes fração × máquina inteira | nós | a fazer |

## 3. Se não der: a apresentação do MVP não precisa disso

| # | item | resumo | de quem | estado |
|---|---|---|---|---|
| 19 | Terraform: balanceador, Vault, OCIR e zona DNS | Hoje o pacote diz "crie à parte". O conteúdo (segredos, imagens, registros DNS) não viaja pelo Terraform de jeito nenhum | nós | a fazer |
| 20 | Ponto 4 do MCP + M4: compromisso vigente, transição e payback | Ler a data de fim da reserva e do Savings Plan (o CUR 2.0 traz) e mostrar à parte o que falta pagar; custo de transição; payback com o custo da migração informado | usuário decide; nós | decisão |
| 21 | M12: resto da precisão da OCI | E3/E4/E5 com burst como destino de origem burstable; sync de banco por API; faturas GCP sem região | nós | a fazer |
| 22 | Ponto 6 do MCP: `in_memory` no seed | A lista inicial do `seed_service_metadata.py` usa `in_memory`; o seed do DE-PARA a regrava com `in_memory_database`. Sem efeito | nós | a fazer |
| 23 | M2: recálculo diário | CronJob no infra com o sync de preços e o câmbio | nós (PR no infra); usuário faz o merge | a fazer |
| 24 | M7 e M8: Terraform pelo agente, parecer do agente na tela | Aguardam resposta | usuário | decisão |
| 25 | M6: coleta automática do inventário | Só com muito tempo e risco de o cliente não gostar | nós | a fazer |

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

## 6. Concluído

| data | item | o que foi feito | commit |
|---|---|---|---|
| 18/10 | Auditoria do MCP | Conferida ponto a ponto: banco por partes, SSD com VPU, DE-PARA da OCI (139 linhas), VPN da Azure e "sem equivalente na OCI" já estavam na develop depois do `684029c` que ela leu; seis pontos seguem abertos (itens 6, 9, 10, 16, 20, 22) | — |
