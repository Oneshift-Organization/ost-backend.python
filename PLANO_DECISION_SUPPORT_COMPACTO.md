# PLANO_DECISION_SUPPORT_COMPACTO — representação v2 do snapshot do agente

> Prioridade: **crítica** (decisão do usuário, 13/10/2026: "não deixe esse
> assunto morrer"). Depende de alinhamento com o time do MCP antes de expor a
> rota; a parte pura (K1) não depende.

## 1. Problema, medido

O agente lê o payload do snapshot inteiro a cada análise. Medido na
representação entregue hoje (`agent-snapshot-v1`), com a develop `7efdc38`:

| relatório | payload | `decision_support` | maior bloco |
|---|---|---|---|
| AWS Cost Explorer (real) | 158,9 KB | **89,4 KB (56%)** | `workload_strategies` 62,4 KB (72 itens = 24 serviços × 3 destinos) |
| GCP Cost Table (real) | 88,2 KB | 55,5 KB (63%) | `workload_strategies` 37,6 KB |
| OCI (mock) | 18,3 KB | 8,3 KB | `provider_scores` 4,8 KB |

Dentro do `decision_support` do bill AWS:
- `score` (6,5 KB) é **cópia** do item de `provider_scores` do provedor
  selecionado;
- `workload_strategies[].evidence` (18,5 KB) repete metadado que já está em
  `decision_workloads`;
- `rationale`, `missing_information` e `assumptions` são textos de regra
  repetidos item a item: 7 regras, com 2 ou 3 variantes de
  `missing_information` em algumas;
- `provider_scores[].axes[].factors[].evidence` traz uma entrada por serviço e
  por destino só para dizer o peso e o valor de cada um.

## 2. Restrições (revisão do MCP de 11/10, itens 5 e 6)

1. **Nenhum resumo sem contrato versionado.** A forma nova é uma versão nova da
   representação, opcional; a v1 continua padrão.
2. **Acesso ao detalhe completo.** O que sair da representação tem de estar
   numa rota, pelo `snapshot_id`, idêntico ao que o backend usou.
3. **Cortar bloqueios ou evidências pode alterar a decisão.** Bloqueios,
   elegibilidade, `selected_provider` (inclusive `null`), notas, faixas,
   cobertura, pesos e regras ficam **intactos**. Só sai o que é repetição ou
   evidência por item, e a evidência vira distribuição (quantos serviços e
   quanto custo em cada valor) mais o ponteiro para o detalhe.

## 3. Nome

`versions.contract` identifica a forma da representação: hoje
`agent-snapshot-v1`. A compacta é `agent-snapshot-v2`. O "contrato v2" do
pacote do MCP (`MCP.PLAN/09`) é a segunda versão **do documento** e corresponde
a `agent-snapshot-v1`. A nota `MCP.PLAN/10` já registra isso; a proposta
(`MCP.PLAN/11`) repete.

## 4. Desenho

**D1. Escolha na preparação.** `POST /v1/agent/snapshots` aceita
`"contract": "agent-snapshot-v1" | "agent-snapshot-v2"`, padrão v1. As versões
entram na identidade do snapshot (`fingerprint`), então cada representação tem
o seu snapshot, com o **mesmo payload completo** gravado. O envio não muda: a
autoridade corrige contra o payload completo e não compara `contract`
(`versoes.divergencias`). `AUTHORITY_VERSION` não sobe.

**D2. Sem perda (só forma):**
- `score` → `score_do_selecionado: "<provedor>" | null`, apontando para o item
  de `provider_scores`;
- textos de regra num dicionário `textos: {"t1": "…"}` emitido uma vez;
  cada estratégia leva os ids (`rationale`, `missing_information[]`,
  `assumptions[]`);
- listas vazias e `null` omitidos dentro de `workload_strategies`.

**D3. Com ponteiro (a evidência por item sai, o efeito dela fica):**
- `workload_strategies[].evidence` sai;
- `factors[].evidence` vira `distribuicao`: para `lock_in`
  `{"low": {"servicos": 6, "custo": 9123.45}, "medium": …, "None": …}`, e o
  mesmo para `refactoring`. Os números do fator (valor, faixa, cobertura,
  contribuição) não mudam, porque já estão calculados no fator.
- `detalhe: {"rota": "/v1/agent/snapshots/{snapshot_id}/decision-support",
  "omitido": ["workload_strategies[].evidence", "provider_scores[].axes[].factors[].evidence"]}`.

**D4. Rota de detalhe.** `GET /v1/agent/snapshots/{snapshot_id}/decision-support`
devolve o bloco `decision_support` completo do payload gravado, mais
`sha256` dele, para o MCP conferir. Mesmas flags e o mesmo isolamento das rotas
`/agent`; vale também para snapshot v1.

**D5. Medido no protótipo:** AWS 89,4 → 30,1 KB de `decision_support` (−66%),
payload 158,9 → 99,6 KB (−37%); GCP 55,5 → 20,2 KB (payload 88,2 → 53,0 KB).

## 5. Etapas

| etapa | o que | depende de |
|---|---|---|
| K1 | função pura `compactar_decision_support` + testes de propriedade (abaixo) | nada |
| K2 | `contract` na preparação; representação v2 | OK do MCP para a forma |
| K3 | rota de detalhe | OK do MCP |
| K4 | E2E (`e2e_snapshot_agente.py --contract v2`) e exemplos reais para os mocks do MCP | K2, K3 |
| K5 | docs: `agent-snapshots.md`, `MCP.PLAN/11` atualizado com o que foi entregue | K4 |
| K6 | data para a v2 virar padrão e para remover a v1 | decisão do usuário com o MCP |

**Testes de K1 (nos dois bills reais e nos mocks):**
1. Todo campo do `decision_support` completo, fora os dois de evidência, está
   na forma compacta com o mesmo valor (reconstrução exata).
2. `distribuicao` soma, por valor, o mesmo número de serviços e o mesmo custo
   da evidência original.
3. `score_do_selecionado` aponta para o item igual ao `score` completo; `null`
   quando não há selecionado.
4. O envio contra um snapshot v2 produz a mesma análise corrigida que contra o
   v1 do mesmo payload (K2).

## 6. Fora do escopo

- Compactar `pools_computacao` (30,6 KB) e `decision_workloads` (24,4 KB) do
  bill AWS. São os próximos maiores; ficam para uma v3 se o custo continuar
  alto.
- Mudar qualquer número, regra ou bloqueio do `decision_support`.

## 7. Estado

- 18/10/2026: K2 e K3 implementados com aceite no MCP: v2 opt-in e detalhe
  congelado autenticado. V1 segue padrão. Testes de contrato sem banco e de
  equivalência de submissão adicionados; validação conjunta no develop e
  medição dos seis relatórios continuam pendentes. Não confundir PR com deploy.
- 13/10/2026: plano e proposta ao MCP (`MCP.PLAN/11`). **K1 feito**:
  `use_cases/agent/representacao_v2.py`, com testes de propriedade nos dois bills
  reais (reconstrução exata sem as evidências; bloco menor que a metade). Nada
  exposto. K2 a K4 esperam o OK do MCP.
