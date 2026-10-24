# AWS — gerar o arquivo de custos para a OneShift

Script: `aws-detalhe-de-uso.sh`

## O que ele entrega

Um destes dois arquivos, nesta ordem de preferência:

1. **`aws-cur2-AAAA-MM.csv.gz`**: o CUR 2.0 do mês, se a conta já tem um export
   configurado em Billing > Data Exports. É o arquivo mais completo, porque traz o ID de
   cada recurso. Se o mês vier em mais de um arquivo, o script junta todos num só.
2. **`aws-fatura-detalhada-AAAA-MM.csv`**: montado pela API do Cost Explorer quando a conta
   não tem o CUR 2.0. Traz cada conta, serviço e tipo de uso, com custo amortizado e sem
   impostos, créditos e reembolsos. Serve para a análise inteira; só não traz o ID de
   cada recurso.

O script só lê. Não cria export, bucket nem permissão.

## Antes de começar

- Acesso ao console da AWS com permissão de leitura de custos:
  - `ce:GetCostAndUsage` e `ce:GetDimensionValues`, para o Cost Explorer;
  - para o CUR, `bcm-data-exports:ListExports`, `bcm-data-exports:GetExport` e leitura no
    bucket do export.
- O Cost Explorer precisa estar ativado em Billing and Cost Management. Na primeira
  ativação, a AWS leva até 24 horas para mostrar os dados.
- Cada chamada à API do Cost Explorer custa US$ 0,01. Um mês costuma dar poucas chamadas.

## Passo a passo

1. Entre no console da AWS com a conta pagadora (management) ou com a conta que você quer
   analisar.
2. Abra o **CloudShell**, no ícone `>_` no topo do console. Ele já vem logado e já tem
   `aws` e `python3`.
3. Envie o script: no CloudShell, **Actions > Upload file** e escolha
   `aws-detalhe-de-uso.sh`.
4. Rode:
   ```bash
   bash aws-detalhe-de-uso.sh            # último mês fechado
   bash aws-detalhe-de-uso.sh 2026-09    # mês escolhido (AAAA-MM)
   ```
5. No fim, ele mostra o nome do arquivo, o número de linhas e o total.
6. Baixe o arquivo: **Actions > Download file** e digite o nome que o script mostrou.
7. Importe o arquivo na OneShift.

## Opções

| como | para quê |
|---|---|
| `FONTE=ce bash aws-detalhe-de-uso.sh 2026-09` | ignora o CUR e usa o Cost Explorer direto |

## O que pode dar errado

| mensagem | o que fazer |
|---|---|
| `não está logado na AWS` | fora do CloudShell, rode `aws configure` ou `aws sso login` |
| `o Cost Explorer recusou a consulta` | a conta precisa de `ce:GetCostAndUsage`, e o Cost Explorer precisa estar ativado em Billing |
| `não tem custo no mês` | confira o mês. Numa conta-membro de uma Organization, rode na conta pagadora |
| `a AWS pediu para esperar` | é limite de chamadas; o script tenta de novo sozinho |
| `sem acesso ao bucket` | o export CUR existe, mas a conta não lê o bucket. Peça a leitura ou use `FONTE=ce` |
| `aviso: o mês ainda não terminou` | o custo vem parcial, até hoje |

## Para ter o custo de cada recurso

Crie um export CUR 2.0: Billing and Cost Management > **Data Exports** > **Create**,
**Standard data export**, tabela **CUR 2.0**, marque **Include resource IDs**, granularidade
**Daily** e formato **gzip - text/csv**. O export começa a gravar em até 24 horas. Depois,
rode o script de novo.
