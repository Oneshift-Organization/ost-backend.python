# PLANO_CICLO_DE_VIDA_DO_PRECO — o preço envelhece, e o billing do cliente o renova

> Proposta do usuário (14/10/2026, `PLAN-ciclo-de-vida-dados-preco.md`), encaixada
> na **fila de precisão** como **P3** ([`PLANO_AUTORIDADE_V3_E_PRECISAO.md`](PLANO_AUTORIDADE_V3_E_PRECISAO.md)
> §3). Este documento guarda a proposta e diz como ela entra no que já existe,
> o que muda em relação ao texto original e em que ordem. Nada implementado.

## 1. A ideia, em uma frase

Preço oficial não fica falso quando envelhece, fica **possivelmente desatualizado**:
muda de função (de "preço atual" para "referência"), e volta a valer quando um
billing recente o confirma. Estados `VIVO → MADURO → EM_DECOMPOSICAO → ADUBO`,
nenhum terminal; `confidence_score = peso_da_fonte × decaimento(idade) × corroboração`.

## 2. O que já existe e vira base

| peça da proposta | o que já temos | onde |
|---|---|---|
| fato de preço | `cloud_price_points` (provedor, SKU, região, unidade, moeda, preço, `source`, `confidence` official/reference, `captured_at`) | `infrastructure/database/models/cloud_price_point.py` |
| idade e confiança na resposta | cada equivalente leva `preco_origem`, `preco_capturado_em`, `preco_idade_dias`, `confianca_preco` | `pricing_evidence`, payload do agente |
| preço de tabela no billing | o CUR já traz `pricing_public_on_demand_cost` (o parser lê) | `parsers/aws_parser.py` |
| preço praticado vs tabela | `premissas.desconto_origem_sobre_lista` (quanto o cliente paga abaixo da lista) | `domain/services/premissas_detalhadas.py` |
| "pedir renovação" | V3.1: tabela `pedidos_de_sync` + `sync_prices.py --pedidos` quando o agente contesta um preço velho | lote v3 |
| coleta oficial | `scripts/sync_prices.py` por zona, quatro provedores | `PLANO_PRECO_REGIONAL` |

O ciclo de vida é o que falta **entre** essas peças: hoje o preço é usado ou não,
e a idade só aparece como número.

## 3. O que muda em relação à proposta original

1. **Sem RabbitMQ/Celery.** A mensageria está diferida (decisão MVP de 18/05). A
   extração de fatos roda como **BackgroundTask** ao fim do enriquecimento do
   billing, e o decaimento como **script agendado** (`scripts/recalcular_decaimento.py`,
   CronJob no cluster), do jeito que o `sync_prices` roda.
2. **Quórum por usuário, não por tenant.** O isolamento é por `user_id`
   (`tenant_id` adiado). `tenant_hash` vira `usuario_hash` (hash com sal do
   `user_id`), só para contar quórum.
3. **O fato de preço estende `cloud_price_points`**, não substitui: colunas
   `estado`, `confianca_score`, `confirmado_em`, `ultima_fonte`, `versao`; a tabela
   nova é só a de observações (`price_observations`) e o histórico. Quem lê preço
   hoje continua funcionando.
4. **`pricing_model` já é implícito**: hoje só cotamos on-demand. Observação de
   billing com Savings Plan, RI, spot, crédito ou desconto (EDP/EA) **nunca**
   revitaliza o preço on-demand; vira observação de preço praticado. É a regra
   6.1 da proposta, e encaixa com a correção de compromisso de compute
   ([`PLANO_EMPACOTAMENTO.md`](PLANO_EMPACOTAMENTO.md)).
5. **Autoridade do agente.** Expor `estado`/`score` não muda número. Usar o
   estado para rebaixar confiança ou trocar preço muda a análise corrigida:
   sobe `AUTHORITY_VERSION` (vai com o lote v3 ou num v4).
6. **V3.1 vira um caso deste plano**: a contestação do agente é uma observação
   do tipo `agente`, com peso baixo; o pedido de sync é a reação a um fato em
   `EM_DECOMPOSICAO`.

## 4. Fases

| fase | o que entrega | muda número? | depende de |
|---|---|---|---|
| **P3.0 Descoberta** | por provedor, qual campo do billing é preço de lista e qual é custo efetivo (CUR: `pricing/publicOnDemandRate`; BigQuery: `price.list_price`?; Azure Cost Details: `payGPrice`/`unitPrice`; OCI: preço de lista do relatório?); idade média dos preços na base; termos de uso | não | **jurídico/termos** (uso agregado e desidentificado) |
| **P3.1 Fundação** | colunas novas em `cloud_price_points`, `price_observations`, histórico; backfill (todo preço atual vira fato com observação oficial e estado pela idade); testes das transições e do score | não | P3.0 |
| **P3.2 Decaimento** | script agendado; `estado` e `confianca_score` no payload ao lado de `preco_idade_dias`; aviso na tela para `EM_DECOMPOSICAO` | não (só rótulo) | P3.1 |
| **P3.3 Revitalização por billing** | extração desidentificada após o enriquecimento; só confirma (não troca valor) quando o preço de lista do billing bate com o da base | não | P3.0 (campos), termos |
| **P3.4 Atualização por quórum** | quarentena, quórum de N usuários distintos, outlier contra o histórico (`ADUBO` como baseline), SKU candidato | **sim** | P3.3, versão de autoridade |
| **P3.5 Calibração** | distribuição por estado, taxa de revitalização, divergência por provedor; ajuste de meias-vidas e limiares | depende | dados reais |

Meias-vidas e limiares da proposta (compute 90 dias, storage 180, preview 30;
≥0,8 VIVO, ≥0,6 MADURO, ≥0,3 EM_DECOMPOSICAO) entram como **valores iniciais em
configuração**, não em código.

## 5. Critérios de aceite (da proposta, mantidos)

- Nenhum preço usado sem estado e score.
- Um billing sozinho nunca altera um preço compartilhado.
- Billing com desconto não altera preço on-demand de tabela.
- Nenhum identificador de cliente (conta, tag, nome de recurso, volume, gasto)
  chega às tabelas compartilhadas.
- Todo valor atual é rastreável até as observações que o sustentam.

## 6. Decisões em aberto (do usuário)

1. Termos de uso: o billing de um cliente pode alimentar a base de todos? Opt-out?
2. Peso entre fonte oficial recente e billing recente quando divergem.
3. Quórum e tolerância por categoria.
4. SKU que some da doc oficial mas segue nos billings.
5. Quando: proposta é **depois** da sprint de Terraform; a P3.0 (descoberta) é
   barata e pode rodar em paralelo.
