# OCI — gerar o arquivo de custos para a OneShift

Script: `oci-detalhe-de-uso.sh`

## O que ele entrega

**`oci-cost-report-AAAA-MM.csv.gz`**: os relatórios de custo (cost reports) do mês, juntos
num arquivo só. A OCI grava vários relatórios por dia. O script baixa os que foram criados
no mês e nos 5 dias seguintes e deixa só as linhas de uso do mês pedido. As correções da
Oracle vêm junto. O script só lê.

## Antes de começar

Os relatórios ficam num bucket da Oracle, fora do seu tenancy. O grupo do seu usuário
precisa desta política, criada no **tenancy raiz** (Identity & Security > Policies). Troque
`<grupo>` pelo nome do grupo:

```
define tenancy usage-report as ocid1.tenancy.oc1..aaaaaaaaned4fkpkisbwjlr56u7cj63lf3wffbilvqknstgtvzub7vhqkggq
endorse group <grupo> to read objects in tenancy usage-report
```

Tenancies sem cobrança por uso (só Always Free) não têm cost reports.

## Passo a passo

1. Entre no console da OCI.
2. Abra o **Cloud Shell**, no ícone `>_` no topo do console. Ele já vem logado e já tem
   `oci` e `python3`.
3. Envie o script: no menu do Cloud Shell, **Upload**, e escolha `oci-detalhe-de-uso.sh`.
4. Rode:
   ```bash
   bash oci-detalhe-de-uso.sh            # último mês fechado
   bash oci-detalhe-de-uso.sh 2026-09    # mês escolhido (AAAA-MM)
   ```
   No Cloud Shell, o script já sabe o tenancy. Fora dele, informe:
   ```bash
   TENANCY=ocid1.tenancy.oc1..xxxx bash oci-detalhe-de-uso.sh 2026-09
   ```
5. No fim, ele mostra o nome do arquivo, as linhas de uso do mês e o total.
6. Baixe o arquivo: menu do Cloud Shell > **Download** e digite o nome que o script
   mostrou.
7. Importe o arquivo na OneShift, como está, sem descompactar.

## O que pode dar errado

| mensagem | o que fazer |
|---|---|
| `não achei o OCID do tenancy` | passe o tenancy: `TENANCY=ocid1.tenancy.oc1..xxx bash oci-detalhe-de-uso.sh` |
| `não consegui listar os relatórios` | falta a política `endorse group ... to read objects in tenancy usage-report` (veja acima) |
| `nenhum relatório de custo criado` | o mês não teve cobrança por uso, ou o tenancy é só Always Free |
| `não consegui baixar ...` | instabilidade da rede; rode de novo |
| `aviso: o mês ainda não terminou` | o custo vem parcial, até hoje |

## Observação

Para o número final de um mês, rode alguns dias depois do fechamento: as correções da
Oracle chegam nos relatórios seguintes.
