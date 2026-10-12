# PLANO_SNAPSHOT_AGENTE — leitura por snapshot e envio idempotente do agente

> ## Status — 2026-10-11
>
> | Item | Estado |
> |---|---|
> | Contrato v1 validado pelo time do MCP, com 4 pontos a fechar | ✅ |
> | Contrato v2 com os 4 pontos (`MCP.PLAN/09-CONTRATO-SNAPSHOT.md`) | ⏳ confirmar a idempotência pelo conteúdo |
> | A — fundação: payload extraído, fingerprints e versões | ✅ (revisão no fim do item) |
> | B — persistência: tabelas, repositórios, flag | ✅ (revisão no fim do item) |
> | C — preparação do snapshot | ✅ (revisão no fim do item) |
> | D — envio idempotente com parecer | ✅ (revisão no fim do item); falta a confirmação do MCP sobre a idempotência pelo conteúdo |
> | E — consulta, transição e manutenção | ⬜ |
> | F — validação e entrega | ⬜ |

## 0. Por que este plano existe

O fluxo do agente (MCP) tem três defeitos no backend, confirmados no código e
na API local em 11/10/2026:

1. **O envio não funciona em relatório novo.** `GET /v1/analyses/{id}/request`
   recalcula sem gravar, e `POST /v1/agent/analyses/{id}/result` exige uma
   análise gravada. Responde `409 PAYLOAD_AUSENTE` com `retryable: true`, e
   repetir o GET não resolve.
2. **O envio sobrescreve a análise da tela.** Grava a resposta do agente por
   cima da "última análise com payload" do relatório. O texto do LLM da tela
   (notas, plano, visões) se perde sem aviso.
3. **O envio não sabe o que o agente leu.** Ele usa "a última análise", não o
   payload consultado. Uma análise concorrente pela tela, ou um `inventory_id`
   diferente, troca a base da correção.

A revisão do time do MCP (`REVISAO_MCP_PLAN_V2.md`, 11/10/2026) propôs o
desenho adotado aqui: **snapshot preparado e gravado explicitamente, envio que
aponta para ele, idempotência e transação**. O contrato externo está em
`MCP.PLAN/09-CONTRATO-SNAPSHOT.md` (v2; a v1 está preservada ao lado). Este
plano é o lado de dentro.

### Validação do time do MCP e o que ela mudou

| ponto | resposta no contrato v2 | efeito neste plano |
|---|---|---|
| 1. Repetição depois da expiração | O envio já gravado é devolvido antes das checagens de validade e de atualização | D14; ordem da D2 |
| 2. Persistência da chave | Idempotência pelo conteúdo, sem `Idempotency-Key` | D9 reescrita; schema sem chave |
| 3. Fingerprint e versões | Dois hashes (interno e entregue) e versões registradas; snapshot de versão anterior recusado | D4 reescrita; D13; A3, A4 |
| 4. Concorrência no banco | Constraints únicas e lock de linha | §3 e D8, já previstos; testes na F1 |
| Validade de um vencido | Reaproveitado quando o fingerprint é idêntico | D5 |
| `get_billing_request` grava | Anotações MCP sugeridas | só o contrato |

## 1. Regras de trabalho

- Branch `feat/snapshot-agente` a partir da `develop`, um commit por etapa,
  merge **só** na `develop`. Nada na `main` sem autorização explícita.
- Sem acesso a produção. Validação em Postgres local (podman) e na CI.
- Dados de teste: os CSVs versionados e os mocks. Nada de dado real.
- Cada etapa com testes, revisão crítica e análise de impacto. Mudança de regra
  de negócio pede reanálise geral (item F).
- Preservar versão: nenhuma rota existente muda de comportamento. A antiga é só
  marcada como deprecated.
- Conferir a coerência de cada payload gerado: o snapshot deve bater com o
  `/request` recalculado (F2).

## 2. Decisões

| # | decisão | por quê | alternativa rejeitada |
|---|---|---|---|
| D1 | Tabelas próprias: `agent_snapshots` e `agent_submissions` | Um snapshot nasce a cada leitura do agente. Na `analysis_jobs` ele entraria na lista da tela, no índice de idempotência do `/start`, no reaper e no `disp_status`, e cada interação exigiria um cuidado | Snapshot como linha de `analysis_jobs` com `origem = agente` |
| D2 | Nesta entrega, snapshot e envio **não aparecem na lista da tela e não geram Terraform** | São decisões de produto em aberto. Vêm depois como adição ("promover envio a análise"), sem quebrar o contrato | Decidir agora sem o produto |
| D3 | Preparação é `POST /v1/agent/snapshots` | GET não pode gravar (o time do MCP pediu o mesmo) | Gravar dentro do `GET /request` |
| D4 | **Dois hashes.** `fingerprint` = SHA-256 do JSON canônico de `{versions, payload completo}`: identidade do snapshot interno. `payload_fingerprint` = SHA-256 da representação entregue ao agente | O primeiro cobre tudo que muda o número (preço, câmbio, classificação, inventário, declarações) **e** a versão do código que vai interpretá-lo. O segundo deixa o MCP registrar exatamente o que o agente leu. Determinismo verificado: o mesmo relatório dá o mesmo hash com `PYTHONHASHSEED` 1, 2 e 3 | Um hash só, que confundiria o interno com o entregue |
| D5 | Reaproveitamento por (usuário, relatório, inventário, `fingerprint`), com `INSERT … ON CONFLICT` renovando `expires_at`. **Vale também para snapshot vencido**: fingerprint idêntico significa conteúdo e versões idênticos aos atuais | Mesmo conteúdo, mesmo snapshot. Concorrência resolvida pelo banco | Procurar antes de inserir, que tem corrida |
| D6 | Validade de 24 h desde a última preparação (`AGENT_SNAPSHOT_TTL_HOURS`) | Cobre uma conversa e limita a idade de preço e câmbio | Sem validade |
| D7 | O snapshot guarda o payload **completo**; o agente recebe a visão compacta (sem `topologia.detalhe`) | O Terraform, se um dia ler snapshots (D2), precisa da rede. Hoje a regra já é essa no `/request` | Guardar só a visão do agente |
| D8 | Envio em transação com `SELECT … FOR UPDATE` no snapshot; constraints únicas como segunda barreira | Serializa envios do mesmo snapshot: revisão sequencial e idempotência sem corrida | Só a constraint única, que exige retry |
| D9 | **Idempotência pelo conteúdo:** (`snapshot_id`, `request_hash`) único, com `request_hash` = SHA-256 do corpo validado. Sem `Idempotency-Key` | O resultado do envio é função pura do snapshot e do corpo. Dois envios com o mesmo conteúdo dariam o mesmo resultado; uma chave só obrigaria o MCP a persisti-la entre reinícios (ponto 2 da validação) | `Idempotency-Key` obrigatório (v1) |
| D10 | Flag própria `AGENT_SNAPSHOT_ENABLED`, padrão `false` | A imagem não roda migração. Com a flag desligada, dá para fazer deploy antes de migrar, como no inventário | Reusar `AGENT_FLOW_ENABLED` (padrão `true`): as rotas novas dariam 500 em dev sem a tabela |
| D11 | Rotas antigas sem mudança de comportamento; `POST /v1/agent/analyses/{id}/result` marcado como deprecated | Transição sem quebrar o MCP atual | Corrigir a rota antiga no lugar |
| D12 | Parecer do agente é julgamento: preservado, nunca altera decisão ou número | É a discordância que hoje se perde | Deixar o parecer influenciar a decisão (fica para o plano de autoridade) |
| D13 | **Versões registradas no snapshot** (`contract`, `decision_policy`, `topology`, `authority`) e comparadas no envio. Diferente da atual → `409 SNAPSHOT_DESATUALIZADO`, `motivo: "versao"`. `authority` é uma constante protegida por um teste que guarda o hash do código das regras de autoridade e falha se ele mudar sem a versão subir | O envio aplica a autoridade **atual** sobre um payload **congelado**. Se a política de decisão ou a autoridade mudar no meio, o resultado seria uma reinterpretação silenciosa (ponto 3 da validação) | Versão manual sem trava, que depende de alguém lembrar |
| D14 | **A repetição vem antes das checagens de validade:** corpo idêntico já gravado é devolvido mesmo com o snapshot vencido ou desatualizado | O resultado já foi gravado e comunicado; recusar a repetição faria o MCP perder um envio que existe (ponto 1 da validação) | Checar validade primeiro |

## 3. Modelo de dados

```
agent_snapshots
  id                    uuid PK
  user_id               uuid not null
  report_id             uuid not null  FK billing_reports(id) ON DELETE CASCADE
  inventory_id          uuid null      (sem FK: inventário apagado vira SNAPSHOT_DESATUALIZADO, não apaga o histórico)
  inventory_marker      varchar(200)   fingerprint do inventário + declarations_updated_at, na preparação
  fingerprint           char(64) not null       identidade: {versions, payload completo}
  payload_fingerprint   char(64) not null       representação entregue
  versions              jsonb not null          contract, decision_policy, topology, authority
  payload               jsonb not null          completo
  pools_sem_equivalente jsonb not null
  created_at, expires_at  timestamptz not null
  UNIQUE (user_id, report_id, inventory_id, fingerprint) NULLS NOT DISTINCT   -- PG 15+; CI e local usam 16
  INDEX (expires_at)

agent_submissions
  id                    uuid PK
  snapshot_id           uuid not null  FK agent_snapshots(id) ON DELETE CASCADE
  user_id               uuid not null
  report_id             uuid not null  (desnormalizado para a consulta por relatório)
  request_hash          char(64) not null
  revision              int not null
  agent_raw             jsonb not null
  analysis              jsonb not null  (corrigida: é o que a resposta devolve)
  campos_sobrescritos   int not null
  created_at            timestamptz not null
  UNIQUE (snapshot_id, request_hash)
  UNIQUE (snapshot_id, revision)
  INDEX (user_id, report_id, created_at)
```

A exclusão de conta (`DELETE /v1/account`) apaga `billing_reports`, e os dois
caem em cascata. Nenhuma entrada nova em `_ROOT_MODELS`; um teste garante isso.

---

## Item A — Fundação, sem mudança de comportamento

**Objetivo:** tirar a montagem do payload de dentro da rota para que o
`/request` e a preparação usem o mesmo código; criar os fingerprints e as
versões.

| etapa | o que | testes | pronto quando |
|---|---|---|---|
| A1 | **Caracterização do `/request` recalculado:** hash dos payloads dos dois CSVs versionados (AWS e GCP), sem e com inventário de mock, com a data congelada | teste de integração que compara o resultado antes e depois da extração | o comportamento está travado antes de mexer |
| A2 | **Extração:** `use_cases/analysis/payload_do_agente.py` com `MontarPayloadDoAgenteUseCase`. Faz as checagens do relatório e do inventário e monta o payload completo + `pools_sem_equivalente`. Erros viram exceções de domínio; a rota as traduz para o envelope de hoje. O caminho "persistida" fica na rota | A1 continua igual; os testes de `test_request_endpoint.py` e `test_inventario_na_analise.py` passam sem alteração | o `/request` usa o use case e nada no contrato mudou |
| A3 | **Fingerprints:** função pura `domain/services/fingerprint_payload.py` (JSON canônico + SHA-256), usada para `fingerprint`, `payload_fingerprint` e `request_hash` | unitários (ordem de chaves, unicode, inteiro e decimal); integração com dois `PYTHONHASHSEED` | o mesmo relatório dá o mesmo hash em processos diferentes |
| A4 | **Versões e trava:** `domain/services/versoes_agente.py` com `CONTRACT_VERSION`, `AUTHORITY_VERSION` e a função que monta `versions` a partir do payload (`decision_support.policy_version`, `topologia.versao`). Teste-trava com o hash dos arquivos das regras de autoridade (`authority.py`, `agent_result.py`, `backend_authority.py`, os dois `decision_support.py`, `equivalence_projection.py`, `impor_topologia`) | o teste falha se algum desses arquivos mudar sem `AUTHORITY_VERSION` subir; a mensagem diz o que fazer | mudança de regra de autoridade não passa em silêncio |

**Revisão do item A (feito em 12/10/2026)**
- **A2 sem mudança de comportamento, medida:** 126 casos do `/request`
  capturados antes e depois da extração (recalculado e persistido de todos os
  relatórios locais, os pares GCP e OCI com inventário de mock, quatro erros do
  `/request` e dois do `/start`): **zero diferenças**. Os 24 testes de
  `test_request_endpoint`, `test_inventario_na_analise`, `test_agent_endpoint` e
  `test_legado_caracterizacao` passam sem alteração.
- **O `_build_ctx` do legado passou a usar o mesmo construtor de contexto** da
  preparação. O erro de tipo antigo do `price_repo` (o repositório Pg não herda
  da interface) mudou de lugar; o total do `mypy` ficou igual ao da `develop`.
- **Payload só com tipos nativos de JSON**, conferido nos quatro casos: o hash
  sobre o dicionário é o mesmo que sobre o JSON entregue. O `hash_canonico`
  recusa `Decimal` e `NaN` em vez de converter em silêncio.
- **A trava (A4) usa a lista dos módulos que rodam no envio**, não o fecho de
  imports: o fecho dá 40 arquivos, porque puxa a análise de topologia inteira,
  que só roda na preparação. Do `topologia_no_payload` entram só
  `impor_topologia` e `sequencia_de_migracao`. Um teste acusa import novo fora
  da lista. Verificado que a trava dispara com uma linha a mais em
  `authority.py`.
- **O caso real apareceu no mesmo dia:** a #30 (Luiz) trocou a política de
  decisão de `score-7rs-v2` para `score-7rs-v3`. Um snapshot preparado antes de
  um deploy assim e enviado depois é recusado com `motivo: "versao"`.
- **Não feito:** o teste de determinismo com dois `PYTHONHASHSEED` ficou como
  verificação manual (três relatórios, três seeds, hashes iguais). Um teste
  automático exigiria subprocessos na suíte.

**Crítica do item A**
- **A caracterização por hash é frágil no tempo.** O payload traz
  `preco_idade_dias` e o alerta de câmbio, que mudam de um dia para o outro.
  **Ajuste:** a data é congelada (o "hoje" é injetado na montagem, ou esses
  campos saem antes do hash) e a comparação é refeita na mesma execução, antes e
  depois da extração.
- **O fingerprint muda todo dia pelo mesmo motivo.** Snapshots são menos
  reaproveitados de um dia para o outro. Aceito: a idade do preço é informação
  que o agente lê, e a validade já é de 24 h.
- **A extração mexe numa rota em uso** (o MCP atual chama o `/request`). O risco
  é coberto pela caracterização e pelos testes existentes, que não podem mudar
  na A2. Se algum precisar mudar, a extração está errada.
- **A trava da A4 vai disparar em mudanças que não alteram regra** (um
  comentário, um refactor). É o custo de não depender de memória humana. A
  mensagem do teste explica que, se a regra não mudou, basta atualizar o hash
  sem subir a versão. A decisão fica explícita no commit.
- **A lista de arquivos da trava pode ficar incompleta** se a autoridade passar
  a chamar um módulo novo. **Ajuste:** o teste também confere que os imports de
  `agent_result.py` não saíram da lista conhecida.

## Item B — Persistência

| etapa | o que | testes | pronto quando |
|---|---|---|---|
| B1 | **Migração** `add_agent_snapshots` com as duas tabelas e os índices (§3), com `downgrade` | `alembic upgrade head` e `downgrade -1` no Postgres local; teste de que o índice único trata `inventory_id` nulo como igual | sobe e desce limpo |
| B2 | **Entidades** (`AgentSnapshot`, `AgentSubmission`), **interfaces** (`IAgentSnapshotRepository`, `IAgentSubmissionRepository`) e **repositórios Pg** | integração dos repositórios: upsert com renovação (inclusive de vencido), lock, busca por `request_hash`, próxima revisão, lista por relatório e por snapshot | domínio sem import de framework |
| B3 | **Configuração:** `AGENT_SNAPSHOT_ENABLED=false`, `AGENT_SNAPSHOT_TTL_HOURS=24`, `AGENT_SNAPSHOT_RETENTION_DAYS=7`; `.env.example` | unitário de config | flags documentadas |

**Revisão do item B (feito em 12/10/2026)**
- Migração `e7f8a9b0c1d2` sobe, desce e sobe de novo no Postgres local; o índice
  de reaproveitamento sai com `NULLS NOT DISTINCT`. `alembic check` não acusa
  nada nas tabelas novas (o único desvio apontado é antigo:
  `ix_analysis_jobs_disp`, da `analysis_jobs`).
- 10 testes de integração dos repositórios: reaproveitamento renova a validade
  e mantém id e `created_at`; inventário nulo conta como igual; vencido com o
  mesmo conteúdo volta a valer; **6 preparações simultâneas dão 1 snapshot**;
  lock isola por usuário; conteúdo e revisão únicos (`IntegrityError` na
  duplicata); lista do mais recente com isolamento; expurgo preserva quem tem
  envio; apagar o relatório leva snapshots e envios.
- O reaproveitamento é detectado comparando o id devolvido pelo `RETURNING` com
  o id proposto, e o `populate_existing` garante a validade renovada mesmo se a
  linha já estiver na sessão.
- `mypy` com o mesmo total da `develop`; nenhum erro novo nos arquivos do item.
- O `config.py` não foi reformatado: só o bloco novo entrou. O `__init__.py` dos
  modelos já estava fora da ordem do lint e foi ordenado, porque precisou do
  import novo.

**Crítica do item B**
- **Armazenamento duplicado.** Um relatório analisado pela tela e lido pelo
  agente guarda o payload duas vezes (171 KB no billing AWS real, 97 KB no
  GCP). Aceitável no volume atual. Se crescer, o snapshot pode apontar para o
  payload da análise quando o conteúdo for igual. Sem otimização prematura.
- **`NULLS NOT DISTINCT` exige PG 15+.** Local e CI usam 16, e o `CLAUDE.md`
  registra 16 no ambiente compartilhado. **Ajuste:** a F7 confere a versão antes
  de migrar. Plano B: índice com `COALESCE(inventory_id, uuid nulo)`.
- **`inventory_id` sem FK é deliberado** (§3). O contra-argumento é ter linha
  apontando para inventário inexistente. Aceito: o snapshot é registro histórico
  e o envio o recusa como desatualizado.
- **Retenção dos snapshots com envio está indefinida.** Eles ficam para sempre
  nesta entrega, porque são o lastro do que o agente disse ao usuário e da
  repetição (D14). Precisa de decisão de produto. Registrado, sem bloquear.

## Item C — Preparação: `POST /v1/agent/snapshots`

| etapa | o que | testes | pronto quando |
|---|---|---|---|
| C1 | **`PrepararSnapshotUseCase`:** usa o A2, monta `versions` (A4), calcula os dois hashes (A3), faz o upsert (D5) e devolve o snapshot, a visão do agente (`compactar_topologia`) e se foi reaproveitado | unitários com repositório falso: cria, reaproveita, renova validade, reaproveita vencido com o mesmo fingerprint, conteúdo ou versão diferente gera outro | regra de reaproveitamento coberta |
| C2 | **Rota e schemas:** `201` cria, `200` reaproveita; corpo `{snapshot, payload, pools_sem_equivalente}`; erros do contrato §2.6; `404 SNAPSHOT_DESLIGADO` com a flag desligada | integração: flag desligada, relatório inexistente e de outro usuário, ingestão em andamento, inventário em cada erro | contrato §2 atendido campo a campo |
| C3 | **Coerência:** o `payload` do snapshot é igual ao do `GET /request?recalcular=true` (sem `_meta`), com e sem inventário; `payload_fingerprint` confere com o `payload` devolvido | integração | os dois caminhos não divergem |

**Revisão do item C (feito em 12/10/2026)**
- **Defeito do item B achado aqui e corrigido:** o reaproveitamento renovava só
  a validade. Declarações do inventário reenviadas iguais mudam a data e não o
  conteúdo; o snapshot seria reaproveitado com o marcador antigo, e o envio o
  recusaria como desatualizado logo depois de confirmado. O `ON CONFLICT` agora
  renova também o marcador, com teste no repositório.
- 6 testes unitários (fingerprint com versões, representação compacta e payload
  completo guardado, reaproveitamento de vencido com validade renovada, conteúdo
  ou versão de autoridade diferente geram outro snapshot, marcador do
  inventário).
- 8 testes pela HTTP: os dois interruptores, relatório inexistente e de outro
  usuário, ingestão em andamento (envelope exato), inventário desligado,
  inexistente e de outra nuvem, `201` e depois `200` com o mesmo id, e **o
  `payload` igual ao do `/request` recalculado**, sem e com inventário, com o
  detalhe de rede só no banco.
- **Decisão tomada aqui:** as rotas exigem `AGENT_SNAPSHOT_ENABLED` **e**
  `AGENT_FLOW_ENABLED`; desligar o interruptor geral do agente desliga também o
  snapshot. Os dois casos respondem `SNAPSHOT_DESLIGADO`, o único código do
  contrato.
- **Limite conhecido:** corpo inválido responde `422` mesmo com a flag
  desligada, porque a validação do FastAPI vem antes do handler. É o mesmo
  comportamento das rotas `/v1/agent/*` que já existem.

**Crítica do item C**
- **A preparação sempre recalcula**, mesmo quando reaproveita: o fingerprint só
  existe depois de montar o payload. Medido: cerca de 1 s por relatório local.
  Aceitável para uma chamada por conversa.
- **`201` vs `200` no reaproveitamento** pode confundir cliente que só aceita
  `201` em POST. O `reused` no corpo é o sinal principal.
- **Risco de abuso:** cada preparação custa um recálculo. Não há limite de taxa
  na API hoje; é tema geral, fora deste plano.

## Item D — Envio: `POST /v1/agent/snapshots/{snapshot_id}/submissions`

| etapa | o que | testes | pronto quando |
|---|---|---|---|
| D1 | **Schemas:** `ParecerIn` (`posicao`, `motivo`, `riscos`) dentro de `AgentAnalysisIn`, opcional | unitários de schema | a rota antiga aceita o parecer sem mudar de comportamento |
| D2 | **`SubmeterAnaliseUseCase`**, em uma transação, na ordem do contrato §3.3: trava o snapshot do usuário (`404`); procura (`snapshot_id`, `request_hash`) e devolve o gravado se existir (D14); checa validade (`410`); checa versão, inventário e relatório (`409` com `motivo`); aplica `receber_analise_do_agente` sobre o payload do snapshot; grava com a próxima revisão | unitários da sequência e de cada desvio | nenhum caminho grava parcialmente |
| D3 | **Rota:** `201` novo, `200` repetição com `Idempotent-Replayed: true`; erros do contrato §3.5 | integração | contrato §3 atendido |
| D4 | **Parecer preservado:** o parecer volta intacto depois da camada de decisão (`impose_decision` reconstrói a resposta) | integração com `decision_support` e `topologia` presentes | a discordância não se perde |

**Revisão do item D (feito em 12/10/2026)**
- **O `parecer` entrou num schema novo** (`EnvioDoAgenteIn`, que estende o
  `AgentAnalysisIn`), não no antigo: no antigo, a rota
  `/v1/agent/analyses/{id}/result` passaria a devolver `"parecer": null`, uma
  mudança de contrato. O plano dizia "a rota antiga aceita o parecer"; a decisão
  final é que ela não muda.
- **O parecer não passa pela autoridade:** sai do corpo antes da correção e
  volta em `analysis.parecer_agente`. Garantido por construção e por teste com
  espião, não por depender de nenhuma etapa da correção preservar uma chave
  desconhecida.
- **Categoria nova `EXPIRADO` → 410** no tradutor de erros.
- 10 testes unitários (a ordem do contrato §3.3: repetição antes da validade e
  da versão, cada motivo de desatualização, isolamento, hash e revisão).
- 12 testes pela HTTP: relatório novo direto para o agente (**o cenário que
  hoje dá `PAYLOAD_AUSENTE`**), decisão e texto da recomendação do payload lido,
  julgamento do agente preservado, repetição `200` com o cabeçalho e o mesmo
  corpo (inclusive com as chaves em outra ordem), revisão nova para corpo novo,
  **4 envios iguais simultâneos → 1 envio; 4 diferentes → revisões 2 a 5**,
  **a análise da tela intacta depois do envio**, vencido recusa corpo novo e
  devolve o gravado, versão anterior (`authority` e `decision_policy`) recusada
  com `versoes_divergentes`, relatório fora de `done`, inventário alterado
  (declaração) e apagado, snapshot de outro usuário, corpo inválido e flag
  desligada.
- `mypy` com o mesmo total da `develop`.

**Crítica do item D**
- **"Desatualizado" é estreito.** Cobre versão, inventário apagado ou alterado
  e relatório que saiu de `done`. **Não** detecta reprocessamento do billing por
  script administrativo nem sync de preço dentro das 24 h. Aceito porque o
  envio é corrigido contra o snapshot, que é internamente coerente, e a validade
  limita a defasagem. Escrito no contrato (§3.5, "Limite da detecção").
- **A idempotência pelo conteúdo não permite gravar duas revisões idênticas de
  propósito.** É desejado: o resultado seria igual, e uma revisão repetida só
  poluiria o histórico.
- **O hash é do corpo validado, não dos bytes.** Ordem de chaves e campos
  ignorados não mudam o hash. É o comportamento certo, e está no contrato.
- **Um corpo que difere só por espaços ou maiúsculas no texto livre vira
  revisão nova.** Não normalizamos texto: decidir que dois textos são "o mesmo"
  seria interpretar o agente. Aceito.
- **O lock serializa envios do mesmo snapshot.** Com um agente por conversa, não
  há contenção prática.
- **Risco:** `receber_analise_do_agente` hoje roda sobre `job.llm_input_payload`.
  Passar o payload do snapshot é a mesma forma, mas a D4 precisa usar um payload
  com `decision_support` e `topologia`, os dois caminhos que reescrevem a
  resposta.
- **Depende da confirmação do item 1 do contrato v2** (idempotência pelo
  conteúdo). Se o time do MCP preferir manter a chave, a D2 ganha a checagem da
  chave antes do hash, e o resto não muda.

## Item E — Consulta, transição e manutenção

| etapa | o que | testes | pronto quando |
|---|---|---|---|
| E1 | `GET /v1/agent/submissions/{id}` (corpo do envio) e `GET /v1/agent/submissions?report_id=` ou `?snapshot_id=`, `limit` 1 a 100, com resumo da análise | integração com isolamento entre usuários e com várias revisões | contrato §4 |
| E2 | `deprecated=True` no `POST /v1/agent/analyses/{report_id}/result`, com a descrição apontando para o substituto | o teste da rota antiga continua passando | o OpenAPI mostra deprecated e nada mais muda |
| E3 | **Expurgo:** snapshots vencidos há mais de `AGENT_SNAPSHOT_RETENTION_DAYS` e **sem envio** são apagados na varredura periódica, só com a flag ligada | integração: apaga o vencido sem envio, preserva o com envio | a varredura não loga erro com a flag desligada nem sem a tabela |
| E4 | **Exclusão de conta** leva snapshots e envios | integração com `DELETE /v1/account` | nada sobra do usuário |

**Crítica do item E**
- **A rota antiga continua sobrescrevendo a análise da tela até ser removida.**
  A proteção é a condição combinada com o time do MCP: envio só com relatório
  de teste. A remoção precisa de data, combinada depois que eles migrarem. Senão
  vira dívida permanente.
- **A varredura roda a cada 60 s.** Um `DELETE` a mais por minuto, com índice em
  `expires_at`, é barato.
- **A tela ainda lê as sugestões pela rota antiga** (`GET /v1/agent/analyses/{id}/result`).
  Migrar a tela para a E1 é trabalho do front, ainda não pedido. Vai para o
  guia do front.

## Item F — Validação e entrega

| etapa | o que |
|---|---|
| F1 | **Bateria do time do MCP e da validação:** billing novo → preparação → envio → consulta; dois usuários; dois inventários; análise pela tela entre a preparação e o envio (**a análise da tela não muda**); repetição depois do commit (mesmo corpo devolve o mesmo envio); repetição depois da expiração (devolve o gravado); duas preparações simultâneas (um snapshot); dois envios simultâneos iguais (um envio) e diferentes (revisões 1 e 2); versão de autoridade alterada entre preparação e envio (`409 versao`); relatório que sai de `done`; contexto ausente |
| F2 | **Coerência:** snapshot = `/request` recalculado (C3); `analysis` e `campos_sobrescritos` iguais entre o envio e a repetição |
| F3 | **E2E pela API local** (`scripts/e2e_snapshot_agente.py`): os dois CSVs versionados e os mocks de inventário. Gera também **exemplos reais de resposta** (preparação, reaproveitamento, envio, repetição, cada erro) para o time do MCP montar os mocks deles |
| F4 | `ruff`, `mypy` estrito, `pytest`; a suíte também em Python 3.12 (podman), que é a versão da CI |
| F5 | **Revisão crítica geral e análise de impacto:** rotas existentes (caracterização), front (nada muda), MCP (contrato), banco (migração), varredura, exclusão de conta |
| F6 | **Documentação:** `docs/endpoints/agent-snapshots.md` (o contrato validado), `docs/data-model.md`, `docs/api-conventions.md` (seção de idempotência, que hoje diz "ainda não há"), `PLANO_API_V2.md` §9 e §10 (corrigir "nenhum bloqueio neste repositório", o estado da flag, a fila de sync que não existe), índice do `CLAUDE.md`; no MCP.PLAN, o 09 passa a "implementado" |
| F7 | **Entrega:** merge na `develop`. No ambiente dev: conferir a versão do Postgres, `alembic upgrade head` e só então `AGENT_SNAPSHOT_ENABLED=true`. Avisar o time do MCP |

**Crítica do item F**
- **O E2E não cobre o cliente MCP real.** Só a API. O caminho completo
  (tool → API) depende do time do MCP rodar a develop deles contra a nossa. Os
  exemplos da F3 reduzem o risco de divergência de forma, mas não substituem o
  teste deles.
- **Concorrência em teste é aproximada.** Os testes disparam as chamadas em
  paralelo (`asyncio.gather`) contra o Postgres real; provam as constraints e o
  lock, mas não todas as intercalações possíveis.
- **A F7 depende de quem tem acesso ao ambiente dev** para migrar e ligar a
  flag. Pela regra, não acesso produção. O dev é feito pelo pipeline e pelo
  `ost-infra`, combinado com quem cuida.

---

## Crítica geral do plano

1. **O tamanho é real.** Duas tabelas, quatro rotas, uma flag, expurgo, trava de
   versão e um refactor numa rota em uso. A versão menor (snapshot dentro de
   `analysis_jobs`) acoplaria o fluxo do agente ao legado, que tem índice de
   idempotência, reaper, `disp_status` e lista da tela.
2. **A idempotência pelo conteúdo é a única mudança de desenho depois da
   validação.** Simplifica o lado do MCP e remove três códigos de erro, mas
   precisa da confirmação deles. Até lá, A, B, C e E andam; só a D espera.
3. **Duas decisões de produto ficam abertas** (lista da tela e Terraform), mais
   a retenção de snapshots com envio. O plano não as resolve; garante que
   possam entrar depois sem quebrar o contrato.
4. **O defeito da rota antiga continua até a remoção.** É a maior fragilidade
   da transição. Mitigação: condição de uso combinada e data de remoção depois
   da migração do MCP.
5. **Tamanho do payload:** 171 KB no billing AWS real, cerca de 43 mil tokens
   por leitura. O contrato tem `versions.contract` para uma visão compacta
   futura, mas este plano não a entrega.
6. **O que este plano não faz:** não muda regra de autoridade (fica no plano de
   autoridade v2), não converte provedor não avaliado em sugestão, não cria a
   fila de sync, não mexe no front.
