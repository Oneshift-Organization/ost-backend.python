# PLANO_EMPACOTAMENTO — cotação de compute pela capacidade-hora e o compromisso de compute

> Escrito e implementado em 13/10/2026. Corrige a sobrecotação de compute que a
> tela de instance types deixou visível: um E2 de São Paulo de US$ 21 cotado num
> `c6a.large` de US$ 86, e o C7G do bill AWS cotado 7x a origem.

## 1. Diagnóstico

Medido nos dois bills reais versionados. Eram **dois defeitos**, ambos no backend.
O agente não pode corrigir nenhum: ele recebe o número pronto e narra.

### F1. Pool fracionado cotado como máquina inteira

A capacidade do pool é a média do mês (vCPU-hora ÷ 730). O empacotador cobrava
`preço x 730 h x instâncias`, com as instâncias arredondadas para cima: um pool
de 0,13 vCPU virava uma máquina inteira ligada o mês todo.

| pool (bill GCP real) | capacidade média | origem | cotado na AWS |
|---|---|---|---|
| N1 · americas | 0,05 vCPU | US$ 1,85 | c7a.medium, US$ 37 (20x) |
| N1 · southamerica-east1 | 0,13 vCPU, 0,47 GiB | US$ 7,43 | c6a.large, US$ 86 (11,6x) |
| E2 · southamerica-east1 | 0,51 vCPU, 2,04 GiB | US$ 21,24 | c6a.large, US$ 86 (4,1x) |

No bill AWS, o R6G (0,05 vCPU, US$ 1,73) saía 21x na Azure.

### F2. O compromisso de compute contado duas vezes

O bill com Savings Plan traz o uso e o plano em linhas separadas:

    BoxUsage:c7g.2xlarge     40.456 h     US$ 1.275   (US$ 0,03/h; on-demand: 0,29)
    ComputeSP:3yrNoUpfront                US$ 8.184
    EC2SP:r7g.1yrNoUpfront                US$ 1.898

O pool fica com a capacidade inteira e o custo líquido, e o destino é cotado
on-demand pelas horas medidas. Na consolidação
(`estimated_total = atual − coberto + cotado`), o plano ficava no saldo
"mantido ao custo atual": **o destino pagava as horas on-demand e a taxa do
plano**. No bill AWS real, US$ 10.081,65 a mais em cada destino.

## 2. Correção

### F1 — capacidade-hora (`domain/services/equivalence_matcher.py`)

- Custo = preço da instância x 730 h x **fração da instância que o pool ocupa**
  (a dimensão que mais pesa, vCPU ou RAM). A origem é cobrada pelas horas que
  rodou; o destino, pelas mesmas horas.
- `instancias` continua sendo o dimensionamento para provisionar, arredondado
  para cima, com folga de 2% (2,04 GiB num SKU de 2 GiB é uma máquina, não
  duas). É o que o Terraform usa.
- A escolha do tipo continua penalizando o desperdício das instâncias
  provisionadas: um pool de 0,13 vCPU fica na menor máquina, não numa de 32
  vCPU cobrada em fração.
- Premissa nova: `premissas.cotacao_compute` (`base: capacidade_hora`).

Limite declarado: uma máquina pequena ligada o mês inteiro pode custar até uma
instância inteira no destino. A premissa diz isso.

### F2 — compromisso de compute (`domain/services/compromisso_de_compute.py`)

- Regra como dado (padrão de `usage_type` ou serviço → paga máquina virtual?):
  `ComputeSP:*`, `EC2SP:*`, `HeavyUsage:<instância>` (reserva de EC2), CUD de
  vCPU/RAM do GCP, reserva de VM da Azure. Reserva de RDS, ElastiCache,
  OpenSearch e DAX (`HeavyUsage:db.*`, `cache.*`, `es.*`, `dax.*`) fica de fora.
- O agregador separa `AuxiliaryCosts.commitment_compute` (contido em
  `commitment`).
- A consolidação soma esse valor ao custo coberto de cada provedor, **na
  proporção da capacidade (vCPU) dos pools que ele cotou**. Não rateia por pool:
  o Compute Savings Plan é fungível entre famílias.
- `reconcile_pricing` lê o crédito de `premissas.compromisso_de_compute` para o
  total fechar com a tabela de equivalência.
- Premissa nova: `premissas.compromisso_de_compute` (valor, por provedor e
  explicação). Só aparece quando existe.

O destino segue cotado sem compromisso: comprar plano lá reduz o custo cotado.

## 3. Autoridade do agente

`equivalence_pricing.py` está na trava (`test_versoes_agente.py`). A mudança só
age quando o payload traz `premissas.compromisso_de_compute`, que nenhum
snapshot preparado antes do deploy tem: um snapshot antigo é corrigido
exatamente como antes (teste `test_payload_without_commitment_premise_reconciles_as_before`).
Por isso o hash da `agent-authority-v2` foi trocado **sem subir a versão**, e o
MCP não precisa preparar de novo os snapshots dos testes conjuntos.

Os números mudam para snapshot novo (o fingerprint é outro), como em qualquer
mudança de preço.

## 4. Antes e depois (13/10/2026)

Mesmo método do `PLANO_PRECO_REGIONAL` §8: seis relatórios, sem LLM
(`payload_do_agente_uc.montar`), banco local com o sync por zona. Economia em
US$/mês; negativo é o destino sair mais caro.

| relatório | decisão | economia antes → F1 → F1+F2 | pior pool antes → depois |
|---|---|---|---|
| AWS Cost Explorer (real) | OCI = OCI | OCI 29.523 → 30.349 → **40.431** · Azure −20.739 → −18.655 → **−8.573** · GCP −28.918 → −27.026 → **−16.944** | R6G→Azure 21,4x → C7G→GCP 8,6x |
| GCP Cost Table (real) | OCI = OCI | OCI 2.629 → **2.762** · Azure 152 → **494** · AWS 126 → **440** | N1/americas→Azure 21,6x → 2,0x |
| GCP BigQuery (mock) | OCI = OCI | OCI 2.627 → **2.760** · Azure 151 → **492** · AWS 124 → **438** | 21,6x → 2,0x |
| AWS CUR (mock) | OCI = OCI | OCI 35.990 → 36.866 → **37.370** · Azure −12.732 → **−10.074** · GCP −23.115 → **−20.291** | C8GN→Azure 3.694x → T3A/sa-east-1→GCP 3,9x |
| Azure (mock) | OCI = OCI | OCI 3.926 → **4.116** · AWS −67 → **379** · GCP −414 → **−5** | 1,8x → 1,5x |
| OCI São Paulo (mock) | nenhum = nenhum | GCP −6.173 → **−5.567** · AWS −7.267 → **−6.526** · Azure −7.388 → **−6.628** | A1→GCP 5,4x → 4,9x |

Coerência:

- **Mesma decisão e mesma confiança nos seis.** Nenhum destino trocou de sinal,
  exceto Azure→AWS no mock Azure (−67 → +379), que estava no limite.
- **Bills GCP:** sair para AWS ou Azure volta a compensar um pouco (US$ 440–494),
  porque os pools pequenos deixam de custar 4x a 20x e o E2 de 28 vCPU médios
  passa a pagar 28,49 de 32 vCPU.
- **Bill AWS real:** a maior mudança é a F2. Os três destinos ganham US$ 10.081
  (o plano de compute sai da origem com os pools); a OCI passa de 32% para 44%
  de economia.
- **Reconciliação fecha** em todos os provedores dos quatro relatórios com
  compromisso ou pools afetados (`reconciled`, diferença de centavos).
- **E2E do snapshot do agente:** 4/4 coerentes.

### O que segue acima de 2,5x, pool a pool, e por quê

| grupo | pools | por quê | ação |
|---|---|---|---|
| Cobertos por Savings Plan | C7G, R7G, C8G (AWS real) | origem US$ 0,004–0,018 por vCPU-h contra 0,036–0,054 on-demand: custo líquido do plano | resolvido no total pela F2; pool a pool não é comparável (a premissa diz isso). Ver §5 |
| Burstable na origem | T3A, T4G, T2 (AWS) | vCPU burstable tem base de 20–40%; no GCP não há burstable desse porte e ele é cotado em vCPU cheia | §5 |
| Origem OCI barata | A1, E4, E5 (mock OCI) | diferença real de preço | nenhuma |

## 5. O que fica para depois

1. **Pool coberto por compromisso na tela.** A tabela de equivalência e o
   diálogo de instance types mostram o C7G a 7–8x. Marcar os pools de um bill
   com compromisso de compute (ou estimar a cobertura pelo preço on-demand da
   própria origem) e avisar na tela. Toca `equivalence_projection.py`, que está
   na trava de autoridade: vai com o lote v3.
2. **Burstable na origem para destino sem burstable.** Dimensionar pela base de
   CPU do burstable (20–40%) em vez da vCPU nominal, com premissa. Fila de
   precisão.
3. **Reserva de RDS e ElastiCache classificada como compute.** O
   `HeavyUsage:db.*` entra como `core`/`virtual_server` no enriquecimento. Não
   afeta esta correção (fica fora do crédito), mas a classificação está errada.
   Lote v3 (V3.3, classificação).
4. **Terraform:** com a capacidade-hora, `instancias` segue sendo o que se
   provisiona. O aviso de empacotamento da sprint de quarta deve mostrar as duas
   coisas: quantas máquinas e que fração delas o uso medido ocupa.
