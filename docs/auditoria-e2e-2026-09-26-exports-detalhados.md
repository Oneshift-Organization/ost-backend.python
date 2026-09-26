# Auditoria E2E — exports detalhados (26/09/2026)

Etapa E10 do [`PLANO_CUR_BIGQUERY.md`](../PLANO_CUR_BIGQUERY.md). Os quatro mocks
passaram pela **API HTTP** de ponta a ponta: upload, ingestão, análise com o LLM
configurado (Anthropic), geração de Terraform para as outras três nuvens e
`terraform validate` feito pela própria API.

Reproduzir:

```bash
python scripts/gerar_mocks_billing.py --destino /tmp/mocks
PYTHONPATH=. python scripts/sync_prices.py --providers azure,oci,gcp,aws,categories
python scripts/e2e_exports_detalhados.py --url http://localhost:8000 \
  --arquivo /tmp/mocks/mock_aws_cur2_2026-03.csv.gz \
  --arquivo /tmp/mocks/mock_gcp_bigquery_2025-12.json.gz \
  --arquivo /tmp/mocks/mock_azure_cost_details_2026-03.csv \
  --arquivo /tmp/mocks/mock_oci_cost_report_2026-03.csv.gz
```

## Resultado

| mock | linhas consolidadas | gasto analisado | base | desconto de origem | capacidade | análise | Terraform (3 destinos) |
|---|---|---|---|---|---|---|---|
| AWS CUR 2.0 | 1.864 (de 32.933) | US$ 92.103,48 | amortizado | 16% | 100% medida | COMPLETED | 3/3 válidos |
| GCP BigQuery | 535 (de 31.594) | US$ 4.890,20 (R$ 27.874,13) | amortizado | 4,9% | 100% medida | COMPLETED | 3/3 válidos |
| Azure | 48 (de 1.428) | US$ 6.710,82 | amortizado | 1,7% | 52% estimada (Azure não informa RAM) | COMPLETED | 3/3 válidos |
| OCI | 41 (de 1.272) | US$ 3.127,80 | amortizado | — | 100% medida | COMPLETED | 3/3 válidos |

Conservação de custo (reconciliação) fechou nos quatro. Nenhuma violação.

## Coerência entre camadas, verificada

- **Mesmo dinheiro, dois caminhos.** O CUR e a fatura agregada do mesmo mês dão
  o mesmo gasto (US$ 92.103,48). O BigQuery e o Cost Table de onde ele saiu
  também (R$ 27.874,13, já sem free tier). Depois da correção do Spot na fatura
  legada, os dois parsers dão a **mesma capacidade** para o C7G (603,87 vCPU),
  e isso é teste.
- **Compromisso.** No agregado, o Savings Plan inteiro (US$ 13,7 mil com as
  reservas) fica num balde à parte e as instâncias cobertas aparecem com custo
  zero. No amortizado, sobra só o ocioso (US$ 1,9 mil). O resto está nos pools
  e no banco, onde o plano foi usado.
- **Armazenamento medido.** EBS: 48.626 GB em 99 volumes. Snapshots: 7.406 GB,
  declarados como backup e não gerados como disco. Azure: tiers convertidos
  (12 × P30 + 6 × S20 = 15.360 GB). Tudo bate com o gerador.
- **Bancos.** 165 instâncias medidas no CUR. O pacote gera os 8 grupos mais
  caros, com `count` e vCPU da classe. Cache (21 nós Redis), motores que o
  destino não gera e grupos menores estão declarados no README.
- **Recomendação.** O backend (`resumo_executivo.recomendado`) é a autoridade
  e sobrescreve o provedor e a economia do LLM. Na OCI, todos os destinos saem
  135% a 204% mais caros, e o resultado é manter.
- **Recursos por pacote.** aws→azure tem 65 blocos contra 38 no aws→gcp. Não
  há discrepância: o blueprint tem 38 recursos nas três nuvens, e a Azure exige
  uma interface de rede por VM (26) mais o resource group.

## Defeitos encontrados e corrigidos durante E4–E10

Registrados um a um na rodada 4 da revisão crítica do plano (linhas 18 a 30).
Os que também afetavam o **caminho legado** — os dois bills reais:

- Spot, Dedicated e instâncias com prefixo de região sem capacidade na fatura
  AWS: ~150 vCPU a menos e US$ 1,7 mil contados como compromisso.
- Prefixo de região (`USE1-`) lido como família de máquina: capacidade fantasma.
- SKU de free tier do GCP virando pool de custo negativo, marcado como "coberto
  por compromisso".
- Snapshot EBS classificado como SSD ou HDD conforme a região; storage do RDS em
  São Paulo classificado como disco.
- Aurora I/O-Optimized, suporte estendido, VPN, VPC Endpoint, IPv4 público e DNS
  sem classificação.

## O que continua em aberto (não é defeito desta entrega)

| item | por quê | o que resolveria |
|---|---|---|
| Confiança da análise "low" nos quatro | 62% do custo cotado (storage, egresso, banco) usa taxa de **referência** por categoria; só compute tem preço oficial. `categories-live` trouxe 3 pontos | preço oficial por categoria nas quatro nuvens |
| Azure: capacidade "estimada" | o export dá vCPU e não RAM | inventário por API (Bloco 3) |
| Banco da Azure sem inventário | o billing do Flexible Server não traz a classe da instância | idem |
| OCI: sem tempo ativo por instância | a OCI cobra OCPU-hora sem informar quantas OCPUs a instância tem | idem |
| Mocks de Azure/OCI são sintéticos | não há bill real dessas nuvens no repositório | primeiro bill real vira fixture |
| Semântica de correção da OCI | a doc não diz se a correção substitui ou soma | validar com o primeiro relatório real |
