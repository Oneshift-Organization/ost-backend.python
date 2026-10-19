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
| 1 | Terraform: banco da OCI pelo motor | O gerador da OCI escreve todo banco como Oracle Autonomous Database, com 1 OCPU por vCPU (o dobro). A análise cotou OCI PostgreSQL. PostgreSQL passa a `oci_psql_db_system` e MySQL a HeatWave; o Autonomous fica para Oracle. Some o aviso falso "versão 16 fora do que o destino aceita" | nós | a fazer |
| 2 | Terraform: buckets do escopo | O bucket só nasce do custo dele; a fatura não liga o custo do S3 ao bucket, e o pacote sai sem bucket e sem aviso (bucket conta como "tipo que o pacote cria"). Passa a sair do inventário, pelo nome | nós | a fazer |
| 3 | Terraform: avisos verdadeiros | "AmazonVPC: nenhum recurso no inventário" com 3 VPCs e 15 subredes, e a rede criada; idem Route53 com a zona no inventário. Cost Explorer e Config passam a ser "gestão da conta, não infraestrutura" | nós | a fazer |
| 4 | Terraform: ocupação de origem burstable | O empacotador dimensiona burstable pela linha de base; o aviso compara com a vCPU nominal e pede "uma máquina a mais" sem motivo (146% na `f67fcd01`). Mesma base nos dois, dizendo que a origem é burstable | nós | a fazer |
| 5 | D1: memória da série B v1 da Azure | B1ls com 2 GiB (real 0,5), B1s 2 (real 1), B1ms 4 (real 2): o Terraform da Azure cria ¼ da memória (9× B1ls para 17,5 GiB). Tabela pela especificação oficial | nós | a fazer |
| 6 | M13: score v4.3 | Preparar o antes e depois das análises v4.2, sem gravar no histórico, para a conversa. O autor do MCP fez merge do score v5 na develop em 18/10 (PR #37, `0a8efcc`) | usuário com o MCP; a tabela é nossa | decisão |
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

## 5. Concluído

| data | item | o que foi feito | commit |
|---|---|---|---|
| 18/10 | Auditoria do MCP | Conferida ponto a ponto: banco por partes, SSD com VPU, DE-PARA da OCI (139 linhas), VPN da Azure e "sem equivalente na OCI" já estavam na develop depois do `684029c` que ela leu; seis pontos seguem abertos (itens 6, 9, 10, 16, 20, 22) | — |
