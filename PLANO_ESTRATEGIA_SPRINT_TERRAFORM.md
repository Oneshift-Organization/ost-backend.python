# PLANO_ESTRATEGIA_SPRINT_TERRAFORM — fechar o 13/10, decidir, alinhar o MCP e entrar no Terraform

> Escrito em 14/10/2026. É o mapa da sprint: o que fecha hoje, o que depende de
> decisão do usuário, o que depende do time do MCP e de infra, e a sprint de
> Terraform em blocos com critério de aceite. Os planos de cada assunto continuam
> sendo a fonte do detalhe.

## 0. Onde estamos (13 e 14/10, tudo na develop, com push)

| entrega | backend | front |
|---|---|---|
| Adaptação e equivalente por destino no acoplamento | `17e18e1` | `6029710` |
| Compute pela capacidade-hora e compromisso de compute fora da origem ([`PLANO_EMPACOTAMENTO`](PLANO_EMPACOTAMENTO.md)) | `fbc6a31` | — |
| Aplicações, plano de migração, custo do escopo, exportação, dependência entre serviços ([`PLANO_ESCOPO_DE_MIGRACAO`](PLANO_ESCOPO_DE_MIGRACAO.md)) | `5c52b81` | `c2266d4` |
| Inventário de outra conta recusado; fatura resumida avisada | `06ad096`, `5fff15b` | `6d66437` |
| Mock e decisões da tela (NÃO APAGAR) | branch `docs/base-migracao` | — |

Pacote de teste para o dev: `~/Downloads/oneshift-testes-inventario-2026-10-14/`
(fatura + inventário certo, de outra conta e parcial, por nuvem; resultado
esperado no `LEIA-ME.md`, validado contra a API local).

## Antes do Terraform — checklist (16/10)

O que falta para começar o T0, por dono. ✅ feito · ⏳ em aberto.

**Ambiente de develop (infra / usuário)**

| # | item | estado |
|---|---|---|
| A1 | Migrations, regras, preços, FX no dev (1.1) | ✅ 15/10: alembic no head, 147 regras, preços AWS/OCI/Azure, 8 taxas |
| A2 | Preços GCP no dev: falta `GCP_BILLING_API_KEY` no cluster (hoje 28 preços) | ⏳ |
| A3 | Refazer o sync `categories-live` (falhou com 429) | ⏳ |
| A4 | Flag `AGENT_SNAPSHOT_ENABLED=true` no dev (1.2); `INVENTORY_ENABLED` já está ligado | ⏳ confirmar |
| A5 | Deploy das imagens da develop (back `60f309d`, front `5099761`) | ⏳ |

**Validação (usuário)**

| # | item | estado |
|---|---|---|
| V1 | Cenários de entrada na tela: reenviar `inventario-aws.json` da kitconta, refazer a análise e seguir o roteiro do PLANO_CENARIOS_DE_ENTRADA §6 | ⏳ |
| V2 | Teste do pacote de 14 pares no dev (1.3) | ⏳ |
| V3 | Encaminhar ao MCP as notas `MCP.PLAN/10`, `/11` e `/12` (1.5) | ⏳ confirmar |

**Decisões (usuário)** — detalhe e recomendação em §2

| # | decisão | bloqueia |
|---|---|---|
| D1 | 2.1 plano de migração salvo | T5 |
| D2 | 2.2 envio do agente gera Terraform? | T7 |
| D3 | 2.9 revogar a chave GCP das branches órfãs e trocar tokens | — (segurança, fazer já) |
| D4 | 2.3–2.8 e 2.10 | não bloqueiam o T0 |
| D5 | Aplicações de dentro do Kubernetes: opção A ou B (PLANO_CENARIOS_DE_ENTRADA §9) | — |

**MCP** — §3: 3.1 idempotência, 3.2 testes conjuntos (depende de A4), 3.3 contrato
`agent-snapshot-v2`, 3.4 `X-OST-User-Token`, 3.5 tratar `INVENTARIO_NAO_CORRESPONDE`.

**Nós (back/front)** — nada em aberto que bloqueie o T0. O lote da autoridade v3
(3.6) espera 3.2.

## 1. Fechar hoje

| # | o quê | de quem | como saber que fechou |
|---|---|---|---|
| 1.1 | Ambiente dev: `alembic upgrade head`, `python -m app.infrastructure.database.seed_classification_rules`, `scripts/sync_prices.py` por zona, `scripts/sync_fx.py` | infra / operador | `GET /v1/inventories` responde (não 404) e `alembic current` = head |
| 1.2 | Flags no ost-infra: `INVENTORY_ENABLED=true`, `AGENT_SNAPSHOT_ENABLED=true` | infra | aba Dependências mostra o cadeado com upload, não "não habilitado" |
| 1.3 | Teste em dev com o pacote (14 pares) | usuário | o resultado de cada par bate com o `LEIA-ME.md` |
| 1.4 | Nota ao MCP do que mudou para o agente: `MCP.PLAN/12` | nós (feito neste commit) + usuário encaminha | time do MCP confirma leitura |
| 1.5 | Encaminhar `MCP.PLAN/10`, `/11` e `/12` | usuário | — |

## 2. Decisões do usuário

Com a recomendação; nenhuma bloqueia o início da sprint, exceto a 2.1 para o T5.

| # | decisão | recomendação | bloqueia |
|---|---|---|---|
| 2.1 | O plano de migração fica **salvo** (para reabrir, exportar e gerar o Terraform do escopo)? | **Sim**, entidade simples (`migration_plans`: escolha + fingerprint da análise). O Terraform do escopo referencia o plano salvo, não uma escolha solta | T5 |
| 2.2 | Decisão 23: o envio do agente pode gerar Terraform? | **Não nesta sprint**: Terraform só da análise e do plano do usuário | T7 |
| 2.3 | Decisão 22: o envio do agente aparece na lista de análises da tela? | Sim, com selo "do agente" | — |
| 2.4 | Decisão 24: por quanto tempo guardar snapshot com envio? | 90 dias | — |
| 2.5 | Decisão 25: data de remoção da rota antiga de envio | duas semanas depois de o MCP confirmar a migração | MCP |
| 2.6 | Anexar inventário refaz a análise inteira (LLM de novo) | aceitar por ora; reavaliar se o custo pesar | — |
| 2.7 | Fonte do `perf_coef` | tabela curada das publicações oficiais (opção 1 do P2) | P2 |
| 2.8 | Termos de uso para o ciclo de vida do preço (billing de um cliente alimenta a base de todos?) | levar ao jurídico antes da P3.3 | P3.3 |
| 2.9 | Segurança: revogar a chave GCP das branches órfãs; trocar `MCP_TOKEN`, token do inspector e `GCP_BILLING_API_KEY` | fazer já, numa janela combinada com o MCP | — |
| 2.10 | Quando promover develop → main | depois do teste em dev (1.3) e dos 24 testes de Terraform rodando (T0) | — |

## 3. MCP e agente

| # | pendência | de quem | estado |
|---|---|---|---|
| 3.1 | Confirmar a idempotência pelo conteúdo e a lista com resumo do contrato de snapshot | MCP | aberta desde 12/10 |
| 3.2 | Testes conjuntos com a autoridade v2 | MCP + nós | **bloqueada** por 1.1/1.2 (snapshot desligado no dev) |
| 3.3 | `decision_support` compacto (crítico): a parte pura (K1) está pronta e não exposta; falta o aceite do contrato `agent-snapshot-v2` opcional ([`PLANO_DECISION_SUPPORT_COMPACTO`](PLANO_DECISION_SUPPORT_COMPACTO.md)) | MCP aceita → nós expomos a rota | aguarda MCP |
| 3.4 | Cliente do agente enviar `X-OST-User-Token` | quem hospeda o agente | aberta |
| 3.5 | Novo 422 `INVENTARIO_NAO_CORRESPONDE` na preparação de snapshot com inventário de outra conta | MCP trata o código | nota 12 |
| 3.6 | Lote da autoridade v3 (contestação verificável, classificação, bloco C com o nível refeito pelo inventário) | nós | espera 3.2 |

## 4. Sprint de Terraform

Ordem pensada para ter base verde antes de mexer no gerador, e o que o usuário
vê primeiro (aviso e escopo) antes do opcional.

### T0 — Base verde (antes de tudo)

Os 24 testes de `validate`/`fmt` nunca rodaram: nem a máquina local nem a CI têm
o binário do Terraform.

- Rodar local num container `hashicorp/terraform` com `TF_PLUGIN_CACHE_DIR`
  quente; corrigir o que falhar.
- Pedir ao dono da CI o passo que instala o binário e o cache (workflow `tests.yml`).
- **Aceite:** 0 testes pulados local; na CI, quando o passo existir.

### T1 — Aviso de empacotamento

Com a capacidade-hora, `instancias` é o que se provisiona e a fração é o que o uso
medido ocupa. O caso conhecido: 2 FSV2 viram 9 VMs.

- README do pacote e resposta da geração: "N máquinas provisionadas; o uso medido
  ocupa X% delas".
- **Aceite:** o mock Azure mostra o aviso no pool FSV2; caracterização atualizada
  de propósito.

### T2 — Isolamento entre subredes na Azure

`AllowVnetInBound` libera tráfego entre todas as subredes da VNet: o destino fica
mais aberto que a origem.

- NSG com bloqueio explícito entre subredes, liberando só o que as dependências
  pedem (as mesmas regras que viram ASG).
- **Aceite:** `terraform validate` nos mocks; teste de que subrede sem dependência
  não fala com outra.

### T3 — Banco fora da AWS nas subredes espelhadas

O banco migrado não vai para as subredes espelhadas da origem.

- Posicionar o banco nas subredes de dados espelhadas, como a VM.
- **Aceite:** mocks GCP/Azure/OCI com o banco na subrede certa.

### T4 — Disco fantasma do Filestore

Cerca de 1.570 GB de HDD que não existem no bill GCP. A causa é a classificação
(V3.3, lote v3); na sprint, uma guarda no gerador.

- Não gerar disco a partir de linha classificada como armazenamento de arquivos
  (Filestore, EFS, Azure Files), com aviso no README.
- **Aceite:** pacote do bill GCP real sem o disco; aviso presente.

### T5 — Terraform do escopo ([`PLANO_ESCOPO_DE_MIGRACAO`](PLANO_ESCOPO_DE_MIGRACAO.md) E3)

- `TerraformGenerationIn` aceita o plano (`migration_plan_id`, se 2.1 = sim; senão
  a escolha no corpo).
- Só as unidades do plano, mais a fundação de rede que elas usam; na ordem de
  migração; o README lista a conectividade entre nuvens a prever (`antes` e
  `para_tras` que ficaram de fora).
- Front: o botão "Gerar Terraform do escopo" liga.
- **Aceite:** checkout (prod) gera só auth, checkout e pagamentos, com as subredes
  e grupos deles; o README cita catalogo (antes) e monitoramento (para trás).

### T6 — Blueprint pelo modelo (Bloco 5 do [`PLANO_TERRAFORM`](PLANO_TERRAFORM.md), opcional)

Nomes lógicos e texto do README pelo modelo; o pacote funciona sem ele. Por último,
se sobrar tempo.

### T7 — Terraform a partir do envio do agente

Só se 2.2 mudar.

## 5. Depois da sprint

- Fila de precisão: P1 custos auxiliares por zona, P1b burstable na origem, P2
  `perf_coef`, **P3 ciclo de vida do preço** ([`PLANO_CICLO_DE_VIDA_DO_PRECO`](PLANO_CICLO_DE_VIDA_DO_PRECO.md)).
- Lote da autoridade v3, depois dos testes conjuntos (3.2).
- Pendências da tela Dependências: mensagem própria para fatura sem ID de recurso
  já entrou; falta "migrar só o serviço" (modo parcial, E6) se o usuário quiser.
- ✅ **Fatura AWS no formato certo** — feito em 15–16/10: guia do CUR 2.0 e recusa no
  upload (PLANO_CENARIOS_DE_ENTRADA §3.4), órfãs sem duplicar no re-enrich. (Achado em 15/10 numa análise do develop com
  1,2% do custo classificado). O guia de exportação da AWS no front
  (`lib/billing-export-steps.ts`) manda baixar o CSV do Cost Explorer "custo por
  serviço": sem tipo de uso, a análise sai vazia (sem pools, sem recomendação).
  Decidido **não** classificar esse formato (pareceria completo sem ser):
  1. trocar o passo a passo da AWS para CUR 2.0 (Data Exports), com o script do
     CloudShell (Cost Explorer API por serviço + tipo de uso, no formato
     detalhado que o parser já lê) como alternativa imediata;
  2. recusar no upload o "custo por serviço" (`AWS_CE_DAILY_BY_SERVICE`) com
     mensagem que diz o porquê e como exportar, antes de gastar a análise;
  3. de carona: reenriquecer duplica `unmapped_billing_lines` (o caso de uso
     apaga `billing_enriched` e não as órfãs). Sem efeito hoje (ninguém lê a
     tabela), mas corrigir junto.

## 6. Riscos

- **Dev sem migration** trava 1.3, 3.2 e qualquer teste real da tela: é o item de
  maior alavanca do dia.
- **Caracterização do Terraform** congela os pacotes byte a byte: T1 a T4 mudam o
  pacote de propósito; cada mudança regenera a caracterização no mesmo commit, com
  o motivo.
- **Escopo do T5** depende de 2.1: sem decisão, começa pela escolha no corpo e
  migra para o plano salvo depois.
