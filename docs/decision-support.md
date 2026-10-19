# Score determinístico e 7 Rs — política v5

## Lock-in efetivo (v5, 18/10/2026)

`score-7rs-v5` / `agent-authority-v5` mantêm os pesos **35/50/15**,
os fatores internos e todas as regras 7R. A origem do fator lock-in passa por
`lock_in_da_origem(workload, inventory_services(payload)[nome])`:

- catálogo curado; inventário com exclusividade maior pode elevar o nível;
- diminuir exige cobertura ≥90% e correlação não estimada;
- sem evidência direta, classe unânime pode formar hipótese, nunca fato;
- classe ou inventário estimado que decide o par torna a amostra provisória
  e a nota parcial. Destino comprovadamente mais restritivo prevalece;
- pior nível conhecido entre origem e destino; alternativas continuam sujeitas
  à mesma capacidade curada. Ausência não vira nível baixo.

Cada evidência expõe `lock_in_origem`, `lock_in_fonte` e `lock_in_motivo`, além
do `lock_in` efetivo do par. "Migra junto" permanece no fator dependências,
não duplica exclusividade da nuvem. O acoplamento v5 mostra a mesma origem e
os mesmos níveis por destino; só arquitetura altera `nivel_sugerido` além dela.
Históricos seguem o caminho da própria política e não são recalculados.

A trava de autoridade inclui `lock_in_efetivo.py`, preservando hashes anteriores.
Envios novos de snapshots anteriores são recusados por versão; repetição já
gravada continua recuperável mesmo após expiração ou troca de versão.
Não há migration, reanálise automática nem alteração nos preços da OCI.
Esta entrega não implementa a proposta separada de portão financeiro/score v4.3.

## Rebalanceamento (v4.2)

Novas análises usam **financeiro 35%, técnico 50%, cobertura 15%**.
Somente os pesos globais mudam; critérios internos, pisos de evidência,
arredondamento, bloqueios e regras dos 7 Rs permanecem iguais.
A autoridade sobe para `agent-authority-v4.2`: snapshots anteriores exigem
nova preparação para envios novos, sem alterar retries já persistidos.
Históricos v4/v4.1 mantêm 50/40/10 e a UI exibe os pesos salvos em `axes`.
Não há migration nem recálculo de análises existentes.

## Permanência e comparação (v4.1)

`outcome` distingue `migrate`, `retain` e `inconclusive`. Sem destino elegível,
se todos os destinos cotados têm custo maior ou igual ao atual e cobertura de
preços positiva, a recomendação é **permanecer na cloud atual**, limitada ao
escopo avaliado. Falta de cotações não é evidência de vantagem da origem.

`comparison_basis` registra origem/destino mensal, diferença (destino − origem)
e cobertura por provedor, na moeda USD usada pelo resultado. Não soma alternativas.
`score` mede atratividade de migração; não é qualidade da cloud nem confiança
da decisão. Sem seleção, usa a alternativa com maior economia (menor aumento),
desempatada pelo provedor, e `selected_provider` permanece nulo. Nenhum score
anula bloqueios. O campo compacto legado `score_do_selecionado` referencia a
nota exibida; não implica provedor recomendado. Histórico não é recalculado.

Na versão histórica `agent-authority-v4.2`, snapshots anteriores não eram reinterpretados
no envio. A v5 mantém essa proteção e a idempotência de repetições já persistidas.

## Escopo e compatibilidade

Novas análises usam regras puras do backend; Claude e o fallback NVIDIA narram
os mesmos resultados. Não há serviço, GPU ou chamada de IA adicional para calcular
scores ou classificar estratégias. Não é um modelo de machine learning treinado.
Análises já persistidas não são recalculadas. Não requer migration de banco:
o snapshot e o resultado usam os campos JSON existentes.

`decision_support` é um campo aditivo de `AnalysisV2Response`. Contrato
`oneshift-decision-v1`, política `score-7rs-v5`, escala 0–100. O backend o
recalcula após inferência, inclusive no fallback e na submissão do agente.
Entradas do agente não podem substituir scores nem estratégias.

## Política de pontuação

Política inicial normativa e versionada, não calibrada com migrações reais:

| Eixo | Peso global | Fatores e pesos internos |
|---|---:|---|
| Financeiro | 35% | economia 100% |
| Técnico | 50% | compatibilidade funcional 40%; adaptação 30%; portabilidade/lock-in 20%; dependências 10% |
| Cobertura da análise | 15% | identificação 30%; preços 40%; evidência técnica 30% |

A v4 mantém lock-in no técnico. O terceiro pilar mede a cobertura disponível,
não governança nem benefícios comerciais. Preços incompletos deixam de reduzir
a nota financeira diretamente e passam ao eixo de cobertura. Análises persistidas
conservam seus eixos, notas e versão originais; não são recalculadas.

- Economia: `clamp(50 + 100 * economia_mensal / custo_atual, 0, 100)`.
- Cobertura: `30 * identificação + 40 * cobertura_preços + 30 * cobertura_técnica`.
  Identificação considera o custo positivo não financeiro, incluindo serviços
  desconhecidos no denominador; tipos curados ou aliases entram no numerador.
- Compatibilidade: 100 para mesma capacidade curada (não comprova compatibilidade
  de runtime, versão, licença ou aplicação). Não inventar evidência para tipos inferidos.
- Refatoração: 30 se necessária, 100 se explicitamente dispensada nos dois
  lados do catálogo; ausência não equivale a `false`.
  Sem esse metadado, uma classe curada pode formar hipótese explícita:
  rehost=80, replatform=60, refactor=30. Hipótese mantém o score preliminar e
  nunca altera o fato de refatoração ou confirma uma estratégia 7 Rs.
- Lock-in: baixo=100, médio=50, alto=0; vale a restrição conhecida mais forte,
  com origem efetiva e proveniência descritas na seção v5.
- Dependências: baixo=100, médio=50, alto=0 na fração de custo correlacionada
  pelo inventário (`topologia.detalhe.servicos`). Ausência de inventário é
  desconhecida, nunca dependência baixa. Não substitui lock-in do catálogo.
  Se a correlação inclui custo estimado por tipo (em vez de ID), o fator é
  rotulado como hipótese e a nota permanece preliminar, conservadoramente em
  todos os serviços, pois o resumo ainda não separa essa origem por serviço.
- Alternativas do mesmo provedor só são agrupadas se todas forem da mesma
  capacidade curada; usam a avaliação conservadora, sem selecionar SKU/preço.
- Fatores técnicos são ponderados pelo custo positivo dos componentes. Impostos,
  compromissos não são aplicações migráveis. Itens não classificados não
  pontuam tecnicamente, mas continuam reduzindo a cobertura de identificação.
  Mapeamento de tipo inferido não confirma compatibilidade técnica.
- Governança não pontua (removida do score na `score-7rs-v3`). Declarar
  `region_approved` ou `compliance_approved` como `false` continua bloqueando
  o destino. Declarações do responsável não são certificações.

Cada fator expõe regra, entradas, cobertura e contribuição. Arredondamento
decimal para duas casas; pequenas diferenças de 0,01 podem aparecer ao somar
valores exibidos já arredondados. Dentro do técnico, o valor observado é
`sum(nota_fator * peso_fator * cobertura_fator) / sum(peso_fator * cobertura_fator)`.
Os pesos globais 35/50/15 não são redistribuídos pela falta de dados.
Assim, desconhecido não equivale a ruim.

Para publicar uma nota normalizada, exigem-se identificação, preços e evidência técnica
de pelo menos 50%, e dois critérios técnicos cobrindo pelo menos 50% do custo cada.
Sem isso, havendo comparação econômica, publica-se `value=lower_bound`,
`status=partial`, `assessment_method=conservative_bound`, com pendências explícitas.
Somam-se contribuições comprovadas sem redistribuir os pesos desconhecidos.
Isso evita extrapolar uma pequena parcela fácil para a infraestrutura inteira.
Sem comparação calculável, a nota permanece indisponível, nunca inventada.
Os limites são regras iniciais de produto, não calibração estatística.

`assessment_method=evidence_normalized` identifica a nova leitura. Havendo piso,
dados faltantes ou hipóteses produzem `status=partial` e nota observada única,
arredondada ao inteiro na UI e rotulada **preliminar**; ela pode subir ou cair
com novas evidências. `complete` exige todas as coberturas integrais, sem hipóteses.
Na leitura normalizada, os limites inferior/superior são diagnósticos, não a nota exibida nem um
intervalo probabilístico. Confiança alta/média/baixa permanece separada da nota.

Campos legados obrigatórios permanecem numéricos: scores inteiros recebem o
valor observado arredondado; `viability_score` é esse inteiro ×10. Sem nota,
recebem zero por compatibilidade. Consumidores novos **devem usar `decision_support`**
para distinguir ausência de zero real. O histórico recebe `decision_score_value`
e `decision_score_method`; registros antigos conservam a exibição conservadora.

Mesmos dados normalizados + snapshot de catálogo/preços/câmbio + declarações +
versão de política produzem a mesma decisão e fingerprint SHA-256. Reimportar a
mesma fatura com preços ou evidências atualizados pode legitimamente mudar a nota.
Notas iguais de duas faturas diferentes, isoladamente, não comprovam um defeito.

O cálculo usa o detalhe de topologia congelado no backend; o modelo recebe
apenas o resumo e o ledger compacto. Rede e regras privadas não são enviadas.

## Benefícios comerciais (fora da pontuação)

`decision_support.corporate_benefits` é aditivo e opcional para resultados
históricos. A versão `corporate-benefits-v2`, atualizada em 18/10/2026, contém
fontes oficiais de programas de crédito e suporte para AWS, Azure, GCP e OCI.
Em OCI, o [Startup Growth Engine](https://www.oracle.com/br/artificial-intelligence/startup-growth-engine/)
substitui a indicação de Free Tier neste catálogo. O
[anúncio oficial de 23/06/2026](https://www.oracle.com/br/news/announcement/oracle-expands-oracle-partner-network-with-startup-growth-engine-2026-06-23/)
informa até US$ 60 mil em créditos OCI ou financiamento de avaliações, condicionado
à seleção e ao nível elegível do eOPN. O máximo não é saldo aprovado. A página do
programa exige produto validado e ausência de relação comercial ativa com OCI
na inscrição. O link de suporte passa a ser o [Oracle Support](https://www.oracle.com/support/),
sem presumir plano contratado ou SLA. A revisão desta versão é das referências OCI;
as referências das demais nuvens permanecem as revisadas em 14/10/2026.

Autoridade `agent-authority-v5.1`: muda apenas o catálogo informativo, sem alterar
a política `score-7rs-v5`, os pesos 35/50/15 ou preços. Snapshots preparados antes
da mudança precisam ser renovados para novos envios; repetições já persistidas
mantêm o contrato de idempotência.

Toda oportunidade tem `eligibility=not_verified` e `score_effect=none` no
catálogo. O billing não comprova elegibilidade, porte, aprovação ou plano
contratado. Nenhum benefício modifica economia, preço, score ou recomendação.
A UI oferece um diálogo discreto no detalhe de economia, com requisitos, links
oficiais e data da revisão. Não há nova chamada LLM nem consulta externa em runtime.

Fontes e requisitos ficam em `app/domain/services/corporate_benefits.py`.
Revisar periodicamente e antes de atualizar; mudar catálogo/data/versão e a
autoridade do agente conjuntamente. Resultados salvos não são enriquecidos
retroativamente. Condições podem mudar e devem ser reconfirmadas na fonte.

Simulação de créditos aprovados **não está implementada nesta entrega**. Exige
saldo, moeda, vencimento, escopo e limites de uso comprovados, além de comparação
sem benefício após o prazo. Não tratar oferta potencial como economia permanente.

O novo eixo usa `id=coverage`; `enterprise` continua significando governança
em históricos. `enterprise_score` legado fica em zero nas novas respostas (não
participa da fórmula); consumidores devem ler os eixos de `decision_support`.
O contrato de snapshot usa `agent-authority-v5` (lock-in efetivo sobre
o rebalanceamento e a permanência existentes): envios novos
sobre snapshots de política/autoridade anterior são recusados por versão. Repetições já gravadas
mantêm a idempotência existente. Nenhuma allowlist ou flag de envio é liberada.

## Seleção e bloqueios

Elegibilidade exige economia positiva e cobertura financeira de pelo menos 50%.
Região ou conformidade explicitamente reprovadas bloqueiam o destino, mesmo com
economia alta. Um componente declarado que não corresponde ao snapshot também
bloqueia o cenário, evitando ignorar uma restrição por erro de identificação.

Se todos os destinos elegíveis têm nota completa, vence a maior nota; desempate
por economia e nome do provedor. Se algum é parcial, a seleção é **econômica
preliminar**, por economia, sem comparar notas de completudes diferentes. Isso
fica explícito na saída. Sem elegível, não há destino recomendado; a decisão
explícita de permanência ou inconclusão e a nota de referência seguem a seção v4.1.

## Novos arquivos Azure

Não existe regra por report_id, nome de arquivo, valor total ou quantidade de linhas.
O parser usa as colunas EA/MCA/PAYG; o enriquecimento carrega catálogo e regras
ativos do provedor a cada ingestão. Regras genéricas por MeterCategory,
MeterSubCategory e MeterName também se aplicam a novos arquivos.
Se catálogo e regras estiverem ambos ausentes, interrompe com diagnóstico antes
de substituir o enriquecimento. Serviço desconhecido é preservado como não
classificado; não recebe preço, SKU ou compatibilidade inventados. Os testes
sintéticos variam assinatura PAYG/EA, região, valores, nomes e IDs de arquivo,
passando pelo parser, normalização e enriquecimento. Isso não certifica toda
variação futura de export nem supre inventário ausente.

## Estratégias 7 Rs

Referência conceitual: [AWS Prescriptive Guidance — migration strategies](https://docs.aws.amazon.com/prescriptive-guidance/latest/large-migration-guide/migration-strategies.html).
As regras abaixo são implementação própria, não uma certificação da AWS.

| R | Evidência exigida nesta versão |
|---|---|
| Retain | responsável declara permanência necessária |
| Retire | desuso, aprovação de desativação e dependências revisadas |
| Repurchase | SaaS aprovado, aderência funcional e plano de dados |
| Relocate | plataforma suportada, licenças transferíveis e plataforma inalterada |
| Refactor | catálogo registra refatoração em origem ou destino |
| Replatform | sem refatoração nos dois lados, lock-in médio/alto |
| Rehost | sem refatoração nos dois lados, baixo lock-in |

Os três últimos são **candidatos** por componente de billing, não conclusões
sobre a aplicação inteira. Sem metadados suficientes, estratégia indeterminada.
Declarações conflitantes exigem revisão; uma exigência de retenção prevalece.
Todos precisam de validação de inventário, runtime e dependências antes de execução.

Retain/Retire/Repurchase/Relocate não reutilizam custos do cenário genérico:
ele fica bloqueado até reprecificação específica. O gerador Terraform atual
recusa esses cenários ou bloqueios de governança com HTTP 422
`ESTRATEGIA_REQUER_REVISAO`, em vez de gerar infraestrutura incoerente.

## Contexto adicional opcional pela API

`POST /v1/analyses/start/{report_id}` continua aceitando chamada **sem body**.
Quem possui evidências adicionais pode enviar:

```json
{
  "decision_context": {
    "governance": [{
      "provider": "gcp",
      "region_approved": true,
      "compliance_approved": true,
      "operations_ready": true,
      "evidence": "Referência à avaliação aprovada pelo responsável"
    }],
    "workloads": [{
      "workload": "Nome exato do componente no billing",
      "provider": "gcp",
      "retain_required": true,
      "evidence": "Restrição contratual vigente"
    }]
  }
}
```

Usar booleanos JSON reais; não enviar valores desconhecidos como `false`.
Não colocar senhas, tokens ou dados pessoais nas evidências. O contexto entra
no snapshot persistido; a idempotência de um job ativo preserva a primeira
solicitação. O formulário de coleta dessas declarações não faz parte desta entrega;
a interface exibe fielmente as informações disponíveis e as lacunas.

## Operação

- Deploy de frontend, backend e prompts em **develop**, sem mudar o principal
  Claude ou seu fallback NVIDIA. Frontend tolera resultados antigos sem o bloco.
- Prompts v2 mantêm envelope `oneshift-prompts-v1` e comportamento legado quando
  não existe `decision_support`; cache de prompts pode levar 300s para atualizar.
- Testes: `pytest app/tests/unit --no-cov`, testes HTTP sem banco/LLM, build e
  testes de apresentação no frontend. Não usar dados de clientes em fixtures.
- Rollback: reverter os commits desta funcionalidade por GitOps. Nenhuma tabela
  é removida ou alterada. Preservar snapshots e resultados para auditoria.
- Redesign da homepage, Mermaid, elegibilidade automática e simulação de créditos
  aprovados não fazem parte desta entrega.
