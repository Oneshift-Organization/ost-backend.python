# Plano: cenários de entrada (fatura × inventário)

> Estado: em execução (15/10/2026). Origem: análise `1146f830` em develop, conta real
> AWS 538358956561 (kitconta), com `inventario-aws.json` (AWS Config,
> `select-resource-config`) e `fatura-aws-2026-09.csv` (formato detalhado legado, sem
> ID de recurso). Massas em `../MASSAS REAIS/`.

## 1. O problema de fundo

Os mocks foram montados como se toda conta fosse igual: fatura com ID de recurso,
inventário com tag de aplicação em tudo, portas inteiras. A conta real quebrou
cada uma dessas premissas, e a tela mostrou o resultado como se fosse verdade
("sem aplicação", "todas as portas abertas", "banco compartilhado").

A correção é tratar a **combinação de arquivos** como dado de primeira classe:

| Fatura | Sem inventário | Com inventário |
|---|---|---|
| **Cost Explorer por serviço** (`AWS_CE_DAILY_BY_SERVICE`) | recusada no upload: sem tipo de uso não há o que cotar | — |
| **Resumida com SKU, sem ID** (`AWS_DETAILED_BILLING`, `GCP_COST_TABLE`) | análise e recomendação completas | aplicações e plano; custo por aplicação **estimado pelo tipo** |
| **Detalhada com ID** (CUR 2.0 com IDs, BigQuery detalhado, Azure Cost Details, OCI) | análise e recomendação completas | aplicações e plano; custo **medido** por recurso |
| **CUR 2.0 sem IDs** (exportado sem "Include resource IDs") | igual à resumida | igual à resumida, com aviso de como ligar os IDs |

Cada análise passa a carregar o **diagnóstico de entrada**: o que faltou ou veio
errado nos arquivos, com severidade (crítico, alerta, informativo). A lista de
análises mostra um `!` amarelo ou vermelho; o modal de importação critica o arquivo
antes de enviar.

## 2. O que a conta real revelou

| # | Achado | Causa | Tipo |
|---|---|---|---|
| A1 | Tags de RDS, EKS, balanceador e bucket sumiram (68 recursos com tag no arquivo, 35 no banco) | `select-resource-config` traz `tags` como lista de `{key, value, tag}`; o normalizador só lia objeto. RDS também tem `configuration.tagList`, EKS `configuration.Tags` | bug de leitura |
| A2 | "Todas as portas abertas para a internet" ×2 e dependências `tcp/todas` | portas vêm como decimal (`5432.0`); `int("5432.0")` falha e a regra vira "todas as portas" | bug de leitura |
| A3 | Balanceador sem grupo de segurança | `securityGroups` do ELBv2 vem como `[{"value": "sg-…"}]` | bug de leitura |
| A4 | Nenhuma aplicação | nenhuma das chaves aceitas existe; a conta usa `project`. E nós do EKS, sem tag, contavam contra a cobertura | regra |
| A5 | Unidade do cluster chamada `i-019711e3c383ca17a` | nome = menor chave do conjunto (máquina vem antes do cluster) | regra |
| A6 | VPN no mesmo grupo da aplicação, "banco compartilhado" falso | VPN acessa o banco na 5432 (real), mas é ferramenta de acesso: aceita OpenVPN da internet | regra |
| A7 | 184 recursos "outro" | ~100 são padrões da AWS que existem em toda conta (CodeDeploy, AppConfig, `alias/aws/*`, frotas do autoscaling…) | ruído |
| A8 | "Fatura sem ID de recurso" com texto de Cost Explorer | o arquivo é o formato detalhado que nós recomendamos; a mensagem não separa os formatos | mensagem |
| A9 | Plano por serviço bloqueado | serviço ↔ unidade só vinha do custo ligado por ID | regra |
| A10 | Custo por aplicação vazio | sem ID não havia atribuição alguma, mesmo com 1 cluster, 1 banco, 1 NAT | regra |
| A11 | Nós do EKS trocam de ID todo dia (start/stop) | com CUR, as máquinas de dias anteriores não estão no inventário | regra |
| A12 | Guia "Como exportar" da AWS manda para o Cost Explorer | guia antigo | front |

## 3. Correções e impacto

### 3.1 Leitura do inventário AWS (A1, A2, A3, A7)

- Tags: lista ou objeto em `tags`, mais `configuration.tags`, `tagList`, `Tags` e as
  tags do bucket em `supplementaryConfiguration`.
- Portas: aceitar decimal inteiro (`5432.0`).
- Grupo de segurança do ELBv2 no formato `{"value": …}`.
- Recurso criado pelo cluster (balanceador com `elbv2.k8s.aws/cluster`, disco com
  `KubernetesCluster`/`ebs.csi.aws.com/cluster`) vira membro do cluster, como os nós já eram.
- Padrões da AWS (tabela por tipo e nome) saem do inventário e vão para `contagens`
  como `padrao_do_provedor:<tipo>`.

**Impacto:** só vale para inventário enviado depois do deploy (o banco guarda o
inventário já normalizado; o arquivo cru não fica). Inventário antigo continua como
está até ser reenviado. O fingerprint muda só no inventário novo. Testes de
normalização e mocks rodam no gate.

### 3.2 Topologia (A4, A5, A6)

- Chave de aplicação em duas camadas: primeiro as de aplicação (`app`, `application`,
  `service`…); se nenhuma alcança a cobertura, as de projeto (`project`, `projeto`,
  `product`, `produto`). A premissa diz qual camada foi usada.
- Cobertura medida por **conjunto de anexos** (máquina com discos, cluster com nós), não
  por recurso: 30 nós sem tag de um cluster com tag não pesam contra.
- Nome da unidade sem aplicação: cluster, banco, cache, balanceador, bucket e só então
  máquina; dentro do tipo, nome legível antes de ID.
- Ferramenta de acesso remoto: unidade que aceita da internet porta de VPN (OpenVPN
  1194, WireGuard 51820, IPsec 500/4500, L2TP 1701, PPTP 1723) vira ferramenta, como o
  bastion. Não junta grupo nem conta como consumidor de banco.
- Risco de exposição: um item por grupo de segurança continua, mas o título leva o
  nome do grupo (deixa de parecer duplicado).

**Impacto:** vale na hora para análises existentes nas rotas que recalculam
(`applications`, `migration-plans`, `service-couplings`); o resumo congelado no
payload só muda em análise nova. Fingerprint não muda (cobre a entrada, não o
resultado). Mocks: a regra de ferramenta por VPN e o nome das unidades podem mudar
asserções de nome; conferido no gate.

### 3.3 Custo sem ID de recurso (A8, A9, A10, A11)

- `CustosDoBilling` passa a carregar as linhas sem ID (serviço, tipo de uso, conta,
  região, quantidade).
- `atribuir_por_tipo` (domínio, puro, tabela de regras por provedor) liga a linha ao
  recurso do inventário pelo tipo: máquina pelo tipo, banco pela classe, cluster,
  NAT, balanceador, disco pelo tipo (proporcional ao tamanho), bucket quando é um só.
  Vários candidatos idênticos (mesmo tipo de máquina) dividem igual; candidatos
  diferentes sem medida (vários buckets) não recebem custo.
- A mesma regra cobre a máquina com ID que não está no inventário (nó substituído pelo
  autoscaling), na mesma conta e região.
- O custo atribuído sai **marcado como estimado** e não entra na fração medida.
- Correspondência ganha o status `estimado`: liga custo, com mensagem própria.
- Serviço ↔ unidade também pelo tipo do recurso no inventário: escolher "AmazonS3"
  inclui as unidades com bucket mesmo quando o custo não pôde ser dividido.
- O bloqueio de "escolher por serviço" sai.

**Impacto:** análises antigas ganham custo nas rotas de plano sem reprocessar.
Contrato: `correspondencia.status` ganha `estimado` (front atualizado junto); campos
novos são aditivos. Fatura com ID continua idêntica (as linhas com ID casam antes).

### 3.4 Diagnóstico de entrada e importação (A8, A12)

- Função pura `diagnosticar_entrada` a partir do formato da fatura, do inventário e da
  correspondência; vai no resumo da análise (`GET /v1/analyses`) e na análise.
- `POST /v1/billing-file-checks`: lê só a amostra do arquivo, diz o formato e os
  problemas antes do upload.
- Cost Explorer por serviço: recusado no upload (status `FAILED` com explicação) e
  bloqueado no modal com explicação e o caminho certo.
- Guia "Como exportar" da AWS aponta para o CUR 2.0.

**Impacto:** quem enviar Cost Explorer pela API passa a receber recusa. Análises
antigas com Cost Explorer continuam abertas, com `!` vermelho.

## 4. Fora deste lote

- Modelar CloudFront, Lambda, DynamoDB, ECR, Secrets Manager e grupos de autoscaling
  como tipos próprios (hoje "outro"): mexe em Terraform e em contagens da tela.
- Aplicações de dentro do Kubernetes (export do `kubectl`).
- Reprocessar inventário antigo: o arquivo cru não é guardado; precisa reenviar.

## 5. Validação

Unitários por bloco; no fim, um ensaio local com as massas reais e LLM `mock`
(sem custo de token). Resultado detalhado em §6.

## 6. Resultado

_preenchido ao fim da execução_
