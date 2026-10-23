# Exportar a fatura de cada nuvem

Scripts para o cliente gerar, no shell do console da própria nuvem, o arquivo que a
OneShift importa. Cada um confere o arquivo antes de entregar (linhas e total) e explica
o que fazer quando falta permissão.

| nuvem | script | onde rodar | o que entrega |
|---|---|---|---|
| AWS | `aws-detalhe-de-uso.sh [AAAA-MM]` | AWS CloudShell | o CUR 2.0 do mês, se a conta já tem o export (com o ID de cada recurso); senão, a fatura detalhada pela API do Cost Explorer (conta × serviço × tipo de uso, custo amortizado) |
| Azure | `azure-detalhe-de-uso.sh [assinatura] [início fim]` | Azure Cloud Shell (Bash) | o Cost Details da assinatura, pela API de consumo |
| GCP | `gcp-detalhe-de-uso.sh [AAAA-MM] [projeto.dataset.tabela]` | Cloud Shell | o mês do export do Cloud Billing para o BigQuery (detalhado, se houver) |
| OCI | `oci-detalhe-de-uso.sh [AAAA-MM]` | Cloud Shell da OCI | os cost reports do mês juntos num arquivo só |

Exemplos:

```bash
bash aws-detalhe-de-uso.sh 2026-09
az account show --query id -o tsv
bash azure-detalhe-de-uso.sh <ID-da-assinatura> 2026-09-24 2026-10-23
bash gcp-detalhe-de-uso.sh 2026-09
bash oci-detalhe-de-uso.sh 2026-09
```

Sem argumento, cada um pega o último mês fechado. Os scripts só leem: não criam export,
bucket nem política. O que falta (export do BigQuery no GCP, política do bucket de
relatórios na OCI) cada um explica na mensagem de erro.

Testados contra respostas no formato documentado de cada API (PLANO_CONFERENCIA_DE_SERVICOS
§5); o arquivo gerado passa pelo detector e pelo parser da nuvem.
