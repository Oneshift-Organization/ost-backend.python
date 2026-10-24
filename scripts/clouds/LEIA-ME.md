# Scripts para gerar o arquivo de custos de cada nuvem

Cada pasta tem o script da nuvem e um `MANUAL.md` com o passo a passo. Todos rodam no shell
do console da própria nuvem, que já vem logado. Eles só leem: não criam nem alteram nada na
conta.

| pasta | script | onde rodar | arquivo que sai |
|---|---|---|---|
| `aws/` | `aws-detalhe-de-uso.sh` | AWS CloudShell | `aws-cur2-AAAA-MM.csv.gz` ou `aws-fatura-detalhada-AAAA-MM.csv` |
| `azure/` | `azure-detalhe-de-uso.sh` | Azure Cloud Shell (Bash) | `azure-cost-details-INICIO_FIM.csv` |
| `gcp/` | `gcp-detalhe-de-uso.sh` | Google Cloud Shell | `gcp-billing-export-AAAA-MM.json.gz` |
| `oci/` | `oci-detalhe-de-uso.sh` | OCI Cloud Shell | `oci-cost-report-AAAA-MM.csv.gz` |

Em resumo, para todas:
1. Abra o shell do console (ícone `>_`).
2. Envie o script pelo menu de upload do shell.
3. Rode `bash <script> <mês>`. Sem o mês, ele pega o último mês fechado.
4. Baixe o arquivo pelo menu de download do shell.
5. Importe na OneShift, sem descompactar.

Cada script confere o arquivo antes de entregar (linhas e total) e explica na mensagem o
que fazer quando falta permissão.
