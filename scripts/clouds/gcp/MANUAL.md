# GCP — gerar o arquivo de custos para a OneShift

Script: `gcp-detalhe-de-uso.sh`

## O que ele entrega

**`gcp-billing-export-AAAA-MM.json.gz`**: o mês da fatura, lido do export do Cloud Billing
para o BigQuery. Se a conta tiver o export detalhado (Detailed usage cost), ele é o
preferido, porque traz o ID de cada recurso. O script só lê.

## Antes de começar

- **O export do Cloud Billing para o BigQuery precisa estar ativo.** Para ativar: Billing >
  **Billing export** > **BigQuery export** > **Detailed usage cost** e escolha o projeto e o
  dataset.
  - O export só tem dados a partir de quando foi ativado.
  - Enquanto isso, baixe o **Cost table** do mês pelo console (Billing > Cost table >
    Download to CSV) e importe esse arquivo na OneShift.
- A conta logada precisa de **BigQuery Data Viewer** e **BigQuery Job User** no projeto da
  tabela.
- A consulta lê só o mês pedido. O BigQuery cobra os dados lidos acima da cota gratuita
  mensal de 1 TiB, que cobre a maior parte das contas.

## Passo a passo

1. Entre no console do Google Cloud.
2. Abra o **Cloud Shell**, no ícone `>_` no topo do console. Ele já vem logado e já tem
   `gcloud`, `bq` e `python3`.
3. Envie o script: no menu **⋮** do Cloud Shell, **Upload**, e escolha
   `gcp-detalhe-de-uso.sh`.
4. Rode:
   ```bash
   bash gcp-detalhe-de-uso.sh            # último mês fechado; acha a tabela sozinho
   bash gcp-detalhe-de-uso.sh 2026-09    # mês escolhido (AAAA-MM)
   ```
   O script procura a tabela do export (`gcp_billing_export_resource_v1_...` ou
   `gcp_billing_export_v1_...`) em todos os projetos que a conta enxerga.
5. Se a tabela estiver num projeto que a conta não lista, ou se houver mais de uma conta de
   faturamento, passe o nome:
   ```bash
   bash gcp-detalhe-de-uso.sh 2026-09 meu-projeto.meu_dataset.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX
   ```
6. No fim, ele mostra o nome do arquivo, o número de linhas e o total.
7. Baixe o arquivo: menu **⋮** > **Download** e digite o nome que o script mostrou.
8. Importe o arquivo na OneShift.

## O que pode dar errado

| mensagem | o que fazer |
|---|---|
| `não está logado no Google Cloud` | fora do Cloud Shell, rode `gcloud auth login` |
| `não achei o export do Cloud Billing para o BigQuery` | ative o export (veja acima) ou passe o nome da tabela |
| `Achei o export de mais de uma conta de faturamento` | escolha uma das tabelas listadas e rode de novo passando o nome |
| `o BigQuery recusou a consulta` | a conta precisa de BigQuery Data Viewer e BigQuery Job User no projeto da tabela |
| `a tabela não tem linhas do mês` | o export não estava ativo nesse mês; use o Cost table do console |
| `o mês tem N linhas, demais para baixar por aqui` | acima de 2 milhões de linhas: o script mostra os comandos para exportar pelo BigQuery para um bucket e baixar de lá |
