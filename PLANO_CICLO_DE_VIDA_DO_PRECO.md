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
   **Decidido (16/10): sim**, como experimento, com regra nos termos de uso; os
   clientes atuais aceitam ceder. Desenho no §10.
2. Peso entre fonte oficial recente e billing recente quando divergem.
3. Quórum e tolerância por categoria.
4. SKU que some da doc oficial mas segue nos billings. **Decidido (16/10):** fica,
   marcado `ausente_desde`, envelhece até ADUBO e sai da cotação (migração
   `d4e5f6a7b8c9`); volta quando a coleta traz de novo.
5. Quando: proposta é **depois** da sprint de Terraform; a P3.0 (descoberta) é
   barata e pode rodar em paralelo.

## 7. Feito (16/10/2026)

O usuário mandou pôr o plano em prática seguindo o padrão de qualidade do projeto:
- TDD nas regras de cálculo;
- domínio puro, mypy strict e Ruff;
- cobertura em `domain/` e `use_cases/`;
- regras em tabela de dados, não em código;
- análise de impacto antes;
- nada que mude número.

### P3.0 Descoberta

| formato | preço de lista na fatura | serve para revitalizar (P3.3) |
|---|---|---|
| AWS CUR (legado e 2.0) | `pricing_public_on_demand_cost` / `pricing/publicOnDemandCost` | sim |
| Azure Cost Details | `PayGPrice` | sim |
| GCP BigQuery | `cost_at_list` | sim |
| AWS Cost Explorer, fatura legada da AWS | não tem | não |
| GCP Cost Table | não tem (só custo) | não |
| OCI cost report | não tem (`cost/myCost`) | não |

Idade dos preços hoje:
- tudo tem menos de 30 dias, porque os syncs acabaram de rodar;
- achado: a referência ganhava `captured_at` = dia do seed e pareceria nova para
  sempre. Agora ela leva a data em que o valor foi conferido (o commit que trouxe o
  valor). Por isso 15 taxas de 25/07 já saem "em decomposição".

### P3.1 Fundação

- **Domínio** (`domain/services/ciclo_de_vida_do_preco.py`, 22 testes): estados,
  score = peso da fonte × 0,5^(idade/meia-vida), estado pelos limiares, duração de
  cada estado e a regra mais específica.
- **Regra do agente (decisão do usuário, 16/10):**
  - o agente observa e não confirma;
  - a observação dele nunca revive um preço nem sobe o score (pode ser alucinação);
  - quando o preço já é ADUBO, ela pode ir para a tela ao lado dele, marcada como
    não verificada (`informacao_do_agente`).
- **Tabelas** (migração `c3d4e5f6a7b8`):
  - colunas `estado`, `confianca_score`, `confirmado_em`, `ultima_fonte` e `versao`
    em `cloud_price_points`;
  - `price_observations`, o histórico de valores, billing e agente;
  - `price_lifecycle_rules`, com meia-vida e limiares por padrão de tipo e de nome;
  - `price_source_weights`, com o peso e o "confirma" de cada fonte;
  - os valores iniciais do plano vêm na migração.
- **Histórico:** a confirmação diária do sync não grava linha; atualiza o ponto.
  Grava observação a primeira vez e cada troca de valor, que sobe `versao`. Todo
  valor atual fica rastreável até uma observação.

### P3.2 Decaimento

- `RecalcularCicloDeVidaUseCase` (7 testes) roda:
  - ao fim de cada `sync_prices`;
  - em `scripts/recalcular_ciclo_de_vida.py`, para agendar todo dia, porque o
    decaimento anda com o relógio.
- **No payload:** `preco_estado` e `preco_score` vão ao lado de `preco_idade_dias`,
  no financeiro de cada equivalente de compute. Nos candidatos, vale o pior ponto,
  como a captura mais antiga.
- **Na tela:** o detalhe do serviço mostra "Atualização do preço". Em decomposição
  e adubo, mostra também o aviso de que o preço pode ter mudado.
- Medido nos seis relatórios: números idênticos, como previsto.

## 8. Quanto dura cada estado, e o que muda na tela

Dias desde a última confirmação, sem confirmação nova. Com sync diário, preço
oficial ativo fica sempre VIVO; o ciclo só anda para o preço que parou de ser
confirmado (SKU que saiu da API, região sem sync, referência digitada).

| meia-vida (regra) | fonte | VIVO | MADURO | EM DECOMPOSIÇÃO | ADUBO |
|---|---|---|---|---|---|
| 90 dias: compute, banco, padrão | oficial (peso 1) | 0–29 | 29–66 | 66–156 | > 156 |
| | billing (0,9) | 0–15 | 15–53 | 53–143 | > 143 |
| | referência (0,7) | nunca | 0–20 | 20–110 | > 110 |
| 180 dias: disco, objeto, rede | oficial | 0–58 | 58–133 | 133–313 | > 313 |
| | billing | 0–31 | 31–105 | 105–285 | > 285 |
| | referência | nunca | 0–40 | 40–220 | > 220 |
| 30 dias: SKU em preview | oficial | 0–10 | 10–22 | 22–52 | > 52 |
| | referência | nunca | 0–7 | 7–37 | > 37 |
| qualquer | agente (0, não confirma) | nunca | nunca | nunca | não muda o estado |

O que cada estado faz com o dado exibido. Hoje (P3.2) é só rótulo; nenhum estado
muda o número da análise:

| estado | na tela | no payload e no agente | no número |
|---|---|---|---|
| VIVO | "Atual · capturado há N dias" | `preco_estado: vivo` | usa |
| MADURO | "Recente · capturado há N dias" | `maduro` | usa |
| EM DECOMPOSIÇÃO | "Possivelmente desatualizado" + aviso | `em_decomposicao` | usa; pedir sync (V3.1) é a reação |
| ADUBO | "Antigo, só como referência" + aviso. Se o agente disse algo depois da última confirmação, mostrar ao lado como "informação do agente, não verificada" | `adubo` | usa até a P3.4; o agente nunca troca o número nem revive o preço |

## 9. Próximos passos

- **Gravar a contestação de preço do agente como observação `agente`:** vai com o
  lote v3 (V3.1), porque mexe no envio do agente. A tela do ADUBO lê essa observação.
- **P3.3:** revitalizar por billing só depois da decisão 1 do §6 (termos de uso).
  Formatos que servem: CUR, Azure Cost Details, BigQuery.
- **P3.4:** quórum, que muda número e exige versão de autoridade.
- **P3.5:** calibração com dados reais.

## 10. Fatura como fonte (P3.3 e P3.4): desenho combinado em 16/10, nada construído

O usuário respondeu à análise crítica; o desenho abaixo junta as duas coisas.

**O que entra e o que nunca entra**
- Entra:
  - o preço de lista explícito (CUR `publicOnDemandRate`, Azure `PayGPrice`,
    BigQuery `cost_at_list`);
  - a taxonomia do provedor: código de produto, serviço, categoria de medidor,
    tipo de uso, descrição e código de SKU, unidade.
- Nunca entra numa tabela compartilhada:
  - o custo efetivo, o desconto e as linhas de compromisso, spot e crédito;
  - tag, nome de recurso, projeto e conta.
- O relatório de custo da OCI não tem preço de lista. Somado ao inventário ele casa
  o shape exato e a quantidade, mas o desconto continua no valor. Caminho: estimar o
  fator de desconto do cliente pelos SKUs que ele tem e nós temos oficiais (mediana
  das razões); dividir o efetivo por esse fator dá um preço de lista derivado, com
  peso menor.

**Âncoras e oscilação (proposta do usuário)**
- Preço de documentação oficial é âncora: só outra observação oficial troca o valor.
  A fatura confirma (revive) ou propõe divergência.
- Volatilidade por SKU: com que frequência e quanto a fatura diverge da âncora.
  Mediana e desvio absoluto mediano, não média. SKU estável tem tolerância apertada,
  SKU oscilante tem tolerância larga.
- Sem âncora (serviço que não buscamos), vale o quórum:
  - 3 clientes distintos com preços muito parecidos, pela mediana, no mesmo SKU,
    região e moeda;
  - pode ser reajuste ou promoção da nuvem, e naquele período o preço fica mais
    confiável.
- Valor que veio de fatura envelhece mais rápido (meia-vida menor), para promoção não
  deixar dano permanente.
- Ponderação:
  - preço **abaixo** da âncora é o caso perigoso (desconto ou promoção) e pede quórum
    maior;
  - preço **acima** costuma ser reajuste real.
  - Mesmo com quórum, três clientes no mesmo Savings Plan mostram a mesma taxa com
    desconto: por isso só entra preço de lista, nunca custo efetivo.

**Moeda e região:** o preço vale por região e moeda. Uma fatura em reais não mexe no
preço em dólar. Os nomes, que não dependem de moeda, entram de qualquer fatura.

**Data:** a observação leva o período de uso da linha (todos os formatos trazem),
não o dia do upload. Observação mais velha que a última confirmação oficial não
revive nada.

**Casamento com o SKU (o ponto mais perigoso; plano antes de código):** casar pelo
código que o próprio provedor usa na lista de preço, não por nome ou shape:

| nuvem | na fatura | na lista de preço |
|---|---|---|
| AWS | `product/sku` | código de SKU do offer |
| Azure | `MeterId` | `meterId` da Retail Prices |
| GCP | `sku.id` | `skuId` do catálogo |
| OCI | `cost/productSku` | `partNumber` do cetools |

Exige guardar esse código em cada ponto de preço (os fetchers já leem); sem código
igual, não casa.

**Dado de teste:**
- quórum, mediana e corte de outlier pelo histórico;
- fora os uploads marcados como teste (os mocks do nosso banco têm preços
  inventados);
- tudo atrás de flag de experimento.

**Nome não é equivalência: ideias**
1. **A identidade é o código, o nome é rótulo.** Cada formato tem um identificador
   estável do serviço:
   - AWS: código de produto;
   - GCP: `service.id`;
   - Azure: categoria de medidor;
   - OCI: código de serviço.
   O DE-PARA liga o identificador uma vez; cada fatura nova acrescenta variantes de
   nome ao mesmo identificador, e esses apelidos são seguros. Só identificador novo
   vai para a curadoria.
2. **O inventário como ponte.** Com o ID do recurso, a linha da fatura liga ao tipo
   do recurso no inventário (ELBv2, Key Vault). O tipo já liga ao DE-PARA, então a
   equivalência sai sem adivinhar.
3. **O uso como evidência,** como o `servico_pelo_uso`: o padrão de tipos de uso e
   unidades de um nome desconhecido aponta o serviço quando um alvo domina (60% ou
   mais).
4. **Quórum para virar automático:** a mesma proposta vinda de N clientes distintos
   vira equivalência "automática", com confiança menor que "curada".
5. **Fila de curadoria:**
   - nome novo, custo, tipos de uso e proposta;
   - aprovar ou recusar, guardando a fonte (as páginas oficiais de comparação entre
     nuvens que o DE-PARA já cita).

**Ordem:**
1. Etapa A, nomes: não mexe em número.
2. B, confirmação por preço de lista igual.
3. C, troca de valor por quórum. Muda número e pede versão de autoridade.

## 11. Melhorias futuras

- **Recálculo diário agendado:** CronJob no infra rodando o sync de preços, que já
  recalcula ao final. Hoje o recálculo só roda quando alguém roda o sync. Sem
  agendamento, o rótulo de um preço que ninguém reconfirma fica parado.
- **Guarda contra lista parcial no sync:** se uma fonte devolver menos itens sem erro, o
  SKU que faltou fica `ausente_desde` e sai da cotação na hora (antes era apagado). Não
  perde dado, mas a cobertura cai até o próximo sync. Recusar a substituição quando a
  coleta nova tiver bem menos itens que a anterior (PLANO_PRECO_REGIONAL, risco do R3).
