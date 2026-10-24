# Azure — gerar o arquivo de custos para a OneShift

Script: `azure-detalhe-de-uso.sh`

## O que ele entrega

**`azure-cost-details-INICIO_FIM.csv`**: o detalhe de uso (Cost Details) de uma assinatura,
baixado pela API de consumo da Azure, com as colunas que a OneShift lê. O script só lê.

## Antes de começar

- A conta logada precisa ter leitura de custos na assinatura: Proprietário, Colaborador,
  Leitor ou Leitor de Cobrança.
- Funciona com assinatura pay-as-you-go. Em contrato EA ou MCA, se a API recusar, use o
  download pelo portal (Cost Management + Billing > Usage + charges).

## Passo a passo

1. Entre no portal da Azure.
2. Abra o **Cloud Shell**, no ícone `>_` no topo do portal, no modo **Bash**. Ele já vem
   logado e já tem `az` e `jq`.
3. Envie o script: no Cloud Shell, **Manage files > Upload** e escolha
   `azure-detalhe-de-uso.sh`.
4. Descubra o ID da assinatura:
   ```bash
   az account show --query id -o tsv
   ```
   Para ver todas as assinaturas: `az account list -o table`.
5. Rode:
   ```bash
   bash azure-detalhe-de-uso.sh                                        # assinatura atual, último período fechado
   bash azure-detalhe-de-uso.sh <ID-da-assinatura>                     # outra assinatura
   bash azure-detalhe-de-uso.sh <ID-da-assinatura> 2026-09-24 2026-10-23   # datas escolhidas
   ```
   As datas são o período da fatura, no formato AAAA-MM-DD.
6. No fim, ele mostra o nome do arquivo, o número de linhas e o total.
7. Baixe o arquivo: **Manage files > Download** e digite o nome que o script mostrou.
8. Importe o arquivo na OneShift.

## Opções

| como | para quê |
|---|---|
| `PERIODOS=3 bash azure-detalhe-de-uso.sh` | os 3 últimos períodos fechados num arquivo só |

## O que pode dar errado

| mensagem | o que fazer |
|---|---|
| `não está logado na Azure` | fora do Cloud Shell, rode `az login` |
| `assinatura não encontrada` | confira o ID com `az account list -o table` |
| `não consegui listar os períodos de cobrança` | a conta precisa de leitura de custos na assinatura (veja acima), ou passe as datas direto |
| `a Azure recusou todas as formas de consulta` | o tipo de contrato não aceita esta API: baixe pelo portal e mande um print da tela para o suporte da OneShift |
| `a Azure pediu para esperar` | é limite de chamadas; o script tenta de novo sozinho |
| `aviso: o período ainda não terminou` | o custo vem parcial, até hoje |

## Observação

A Azure cobra em real pelo câmbio dela. O arquivo traz o valor exatamente como foi faturado.
