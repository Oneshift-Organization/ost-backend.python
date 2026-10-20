# Equivalências, preços e 7 Rs — política v2

## Contrato aditivo

- `cross_cloud_equivalents` continua sendo uma projeção do snapshot do backend.
- Cada linha pode trazer `source_monthly_cost`, `pricing_method`, `pricing_reason`,
  `pricing_evidence` e `included_in_estimate`. Nulo continua diferente de zero.
- `pricing_reconciliation` explica, por provedor, preço dos componentes selecionados
  + saldo de origem mantido sem nova cotação = custo projetado. Escopos divergentes
  são marcados `review_required`, nunca silenciosamente considerados reconciliados.
- `decision_support.policy_version` passa a `score-7rs-v2`. A fórmula de score não
  muda; a seleção de evidências e as hipóteses de estratégia são versionadas.
- Estratégias podem trazer `confidence`, `assumptions` e `alternative_targets`.

## Preços e classificação

1. Computação preserva cotação de capacidade por família/região. Alternativas não
   se somam: o cenário usa a opção cotada de menor custo, com desempate estável.
2. Preços por categoria são distribuídos apenas quando o custo integral da categoria
   corresponde aos serviços curados elegíveis. O rateio fecha em centavos, é marcado
   como estimativa por razão de tarifas e não significa cotação individual de SKU.
3. Se essa atribuição não for segura, a estimativa aparece numa linha explicitamente
   agregada. O serviço continua mapeado, com motivo da ausência de cotação individual.
4. Taxas de unidades incompatíveis ou moeda diferente da moeda de análise são excluídas.
5. Aliases revisados impedem Registry de virar disco. Ausência de quantidade no billing
   não elimina equivalência funcional. Taxas, créditos e compromissos não são workloads.

## Estratégias

Pares de capacidade conhecida permitem uma hipótese de rehost/replatform/refactor,
com confiança baixa e perguntas de validação. Metadados explícitos continuam sendo
evidência mais forte. Hipóteses NÃO preenchem fatores técnicos desconhecidos do score.
Alternativas concordantes preservam a estratégia; discordantes exigem escolha.
Retain, retire, repurchase e relocate continuam exigindo as declarações específicas
do responsável. Nada autoriza execução automática de infraestrutura.

## Segurança e compatibilidade

Nenhuma migration, seed em produção, modificação de billing persistido ou chamada de
IA é necessária para publicar. Relatórios já persistidos não são reescritos; a nova
política vale para análises novas. Frontends antigos ignoram os campos adicionais.
Os dois caminhos (pipeline/recepção do agente) impõem os valores do backend.

## Regressão

`test_pricing_strategy_regressions.py` cobre preços/deltas, alternativas, reconciliação,
aliases no catálogo distribuído, export sem quantidade, cobranças financeiras,
hipóteses dos 7 Rs e autoridade sobre a resposta do agente. As suítes existentes
preservam famílias, regiões, ordenação, scores, alertas e histórico.
