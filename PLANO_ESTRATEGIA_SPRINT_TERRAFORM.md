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

## Antes do Terraform — checklist (atualizado em 16/10, noite)

O que falta para começar o T0, por dono. ✅ feito · ⏳ em aberto.

**Resumo:** nada bloqueia o T0 a T4. O ambiente está pronto; o T5 espera a decisão
2.1 e o T7 a 2.2. O T0 roda local: a imagem `hashicorp/terraform:1.9.8` já está no
podman (a máquina não tem o binário).

**Ordem combinada em 16/10 (noite):**
1. ✅ Correções de precisão (`8547faf`, `56efc16`): instância mínima do Cloud Run, guarda
   contra lista parcial no sync, preço de segredo, CDN e plano de controle do Kubernetes
   (PLANO_PRECO_NAO_COMPUTE §6).
2. ✅ Curadoria aprovada (`b09b3ba`): os 26 serviços e o Oracle (`PLANO_SCORE_V5_LOCKIN`
   §8.1), mais a linha "Bandwidth" da Azure no DE-PARA.
3. ✅ T0 com o C2 (`35a3888`): os 24 testes de Terraform passam local; a CI instala o binário.
4. ✅ **Terraform T1 a T4 (17/10):** ver cada bloco no §4. Os 25 pacotes de mock (com e sem topologia, todos os pares) validam no Terraform 1.9.8.

O PR #31 (develop → main) foi **fechado** em 16/10 a pedido do usuário: a promoção para
a main saiu de pauta. A segurança (2.9) foi para as melhorias (§7).

Pendências de terceiros, uma nota por responsável:
- MCP e autor do PR #32 (a mesma pessoa): `MCP.PLAN/14-PENDENCIAS-COM-VOCES-16-10.md`;
- Luiz, dono do `build-push.yml`: `INFRA.PLAN/01-CI-IMAGEM-SO-COM-TESTES-VERDES.md`.

**Ambiente de develop (infra / usuário)**

| # | item | estado |
|---|---|---|
| A1 | Migrations, regras, preços, FX no dev (1.1) | ✅ 15/10: alembic no head, 147 regras, preços AWS/OCI/Azure, 8 taxas |
| A2 | Preços GCP no dev: `GCP_BILLING_API_KEY` no cluster (infra #10) | ✅ 16/10: 722 preços oficiais (eram 24) |
| A3 | Refazer o sync `categories-live` (falhou com 429) | ✅ 16/10: 42 categorias |
| A4 | Flag `AGENT_SNAPSHOT_ENABLED=true` no dev (1.2); `INVENTORY_ENABLED` já está ligado | ✅ 16/10: `POST /v1/agent/snapshots` responde 401 sem token, não mais 404 |
| A5 | Deploy das imagens da develop | ✅ back: automático (push → GHCR → Flux), develop em `19aba2d`. ⏳ front: confirmar onde é publicado (não roda no namespace `develop`) |

**Validação (usuário)**

| # | item | estado |
|---|---|---|
| V1 | Cenários de entrada na tela: reenviar `inventario-aws.json` da kitconta, refazer a análise e seguir o roteiro do PLANO_CENARIOS_DE_ENTRADA §6 | ⏳ |
| V2 | Teste do pacote de 14 pares no dev (1.3) | ⏳ |
| V3 | Encaminhar ao MCP as notas `MCP.PLAN/10` a `/13` (1.5; a 13 é a autoridade v4) | ⏳ confirmar |

**CI**

| # | item | estado |
|---|---|---|
| C1 | O build e o deploy do develop não esperam os testes: `build-push.yml` e `tests.yml` rodam em paralelo, sem `needs` (item 21 de 12/10). Um teste vermelho não impede a imagem de subir. **Do Luiz** (o `build-push.yml` é dele e também publica a main); proposta pronta em `INFRA.PLAN/01` | ⏳ |
| C2 | Passo que instala o binário do Terraform e o cache dos providers (T0 na CI). **Nosso**: o `tests.yml` é mantido pelo backend | ✅ 16/10: `hashicorp/setup-terraform` 1.9.8 e cache dos providers nos dois jobs |

**Decisões (usuário)** — detalhe e recomendação em §2

| # | decisão | bloqueia |
|---|---|---|
| D1 | 2.1 plano de migração salvo | T5 |
| D2 | 2.2 envio do agente gera Terraform? | T7 |
| D3 | 2.9 revogar a chave GCP das branches órfãs e trocar tokens | — (movida para as melhorias, §7) |
| D4 | 2.3–2.8 | não bloqueiam o T0 |
| D5 | Aplicações de dentro do Kubernetes: opção A ou B (PLANO_CENARIOS_DE_ENTRADA §9) | — |

**MCP** — §3: 3.1 idempotência, 3.2 testes conjuntos (**desbloqueada**: A4 feito),
3.3 contrato `agent-snapshot-v2`, 3.4 `X-OST-User-Token`, 3.5 tratar
`INVENTARIO_NAO_CORRESPONDE`.

**Nós (back/front)** — nada em aberto que bloqueie o T0. O lote da autoridade (3.6)
saiu como v4 em 16/10, sem esperar o 3.2 (decisão do usuário). O T4 continua
necessário: a regra `*filestore*` de `seed_classification_rules.py` ainda manda o
Filestore para `storage_hdd`, e o gerador faz disco dele.

## 1. Fechar hoje

| # | o quê | de quem | como saber que fechou |
|---|---|---|---|
| 1.1 | Ambiente dev: `alembic upgrade head`, `python -m app.infrastructure.database.seed_classification_rules`, `scripts/sync_prices.py` por zona, `scripts/sync_fx.py` | infra / operador | `GET /v1/inventories` responde (não 404) e `alembic current` = head |
| 1.2 | Flags no ost-infra: `INVENTORY_ENABLED=true`, `AGENT_SNAPSHOT_ENABLED=true` | infra | aba Dependências mostra o cadeado com upload, não "não habilitado" |
| 1.3 | Teste em dev com o pacote (14 pares) | usuário | o resultado de cada par bate com o `LEIA-ME.md` |
| 1.4 | Nota ao MCP do que mudou para o agente: `MCP.PLAN/12` | nós (feito neste commit) + usuário encaminha | time do MCP confirma leitura |
| 1.5 | Encaminhar `MCP.PLAN/10`, `/11` e `/12` | usuário | — |

## 2. Decisões do usuário

Decididas pelo usuário em 17/10, depois de uma revisão por um conselho de cinco
agentes (custo, produto, segurança, arquitetura, red team). A coluna "decisão"
vale; a recomendação original fica para histórico.

| # | decisão em aberto | decisão (17/10) | bloqueia |
|---|---|---|---|
| 2.1 | O plano de migração fica **salvo**? | **Não agora.** O T5 recebe a escolha na requisição, sem estado | — |
| 2.2 | Decisão 23: o envio do agente pode gerar Terraform? | **Pendente de resposta** (melhorias, M7) | T7 |
| 2.3 | Decisão 22: o envio do agente aparece na lista de análises da tela? | **Pendente de resposta** (melhorias, M8) | — |
| 2.4 | Decisão 24: por quanto tempo guardar snapshot com envio? | **Como está:** para sempre. MVP com teste de cliente real e apresentação do produto | — |
| 2.5 | Decisão 25: data de remoção da rota antiga de envio | duas semanas depois de o MCP confirmar a migração | MCP |
| 2.6 | Anexar inventário refaz a análise inteira (LLM de novo) | **Duplicidade corrigida já** (17/10): a lista mostra uma análise por billing; a nova é uma revisão. Reaproveitar a narrativa sem rodar o LLM vai para as melhorias (M3, prioridade máxima) | — |
| 2.7 | Fonte do `perf_coef` | **Manter 1 vCPU = 1 vCPU e mostrar isso na tela** ([`PLANO_ALTERACOES_DE_TELA`](PLANO_ALTERACOES_DE_TELA.md) §1) | — |
| 2.8 | Termos de uso para o ciclo de vida do preço (billing de um cliente alimenta a base de todos?) | levar ao jurídico antes da P3.3 | P3.3 |
| 2.9 | Segurança: revogar a chave GCP das branches órfãs; trocar `MCP_TOKEN`, token do inspector e `GCP_BILLING_API_KEY` | movida para as melhorias (§7) em 16/10 | — |
| 2.10 | Quando promover develop → main | **fora de pauta**: o PR #31 foi fechado em 16/10 a pedido do usuário | — |
| 2.11 | Fatura como fonte, etapa A (nomes novos de serviço) | **Feito (17/10):** fila de sugestões; a curadoria da OneShift aprova pela API de administração ([`docs/endpoints/curadoria.md`](docs/endpoints/curadoria.md)) | — |
| 2.12 | Kubernetes opção B (export do `kubectl`) | **Adiar.** O pacote do Terraform avisa que leva o cluster inteiro (17/10) | — |
| 2.13 | Modo "só o serviço" | **Adiar** (melhorias, M5) | — |
| 2.14 | Custo de transição (multicloud) | **Adiar** (melhorias, M4) | — |
| 2.15 | Coleta automática do inventário (I10) | **Adiar** (melhorias, M6) | — |
| 2.16 | Payback na tela | **Não mostrar agora.** Entra como complemento do custo de transição (M4) | — |

## 3. MCP e agente

| # | pendência | de quem | estado |
|---|---|---|---|
| 3.1 | Confirmar a idempotência pelo conteúdo e a lista com resumo do contrato de snapshot | MCP | aberta desde 12/10 |
| 3.2 | Testes conjuntos (a autoridade no dev é a v4) | MCP + nós | **desbloqueada** em 16/10: snapshot ligado no dev (A4) |
| 3.3 | `decision_support` compacto (crítico): a parte pura (K1) está pronta e não exposta; falta o aceite do contrato `agent-snapshot-v2` opcional ([`PLANO_DECISION_SUPPORT_COMPACTO`](PLANO_DECISION_SUPPORT_COMPACTO.md)) | MCP aceita → nós expomos a rota | aguarda MCP |
| 3.4 | Cliente do agente enviar `X-OST-User-Token` | quem hospeda o agente | aberta |
| 3.5 | Novo 422 `INVENTARIO_NAO_CORRESPONDE` na preparação de snapshot com inventário de outra conta | MCP trata o código | nota 12 |
| 3.6 | Lote da autoridade v3 (contestação verificável, classificação, bloco C com o nível refeito pelo inventário) | nós | ✅ saiu como v4 em 16/10, sem esperar o 3.2 (decisão do usuário); V4.4 e V4.5 divididos com o autor do #32 (`PLANO_SCORE_V5_LOCKIN`) |

## 4. Sprint de Terraform

Ordem pensada para ter base verde antes de mexer no gerador, e o que o usuário
vê primeiro (aviso e escopo) antes do opcional.

### T0 — Base verde (antes de tudo) — ✅ feito em 16/10

> Local, com o binário 1.9.8 do podman (`docs/dev-setup.md`, "Testes de Terraform"):
> os 24 testes passaram sem nenhuma correção no gerador. Foram 20 unitários, com 0 pulado
> em 2010, e 4 de integração, com os pacotes dos quatro mocks validados. Na CI, pelo C2.
> Próxima etapa: T1 a T4.

Os 24 testes de `validate`/`fmt` nunca rodaram: nem a máquina local nem a CI têm
o binário do Terraform.

- Rodar local num container `hashicorp/terraform` com `TF_PLUGIN_CACHE_DIR`
  quente; corrigir o que falhar.
- Pedir ao dono da CI o passo que instala o binário e o cache (workflow `tests.yml`).
- **Aceite:** 0 testes pulados local; na CI, quando o passo existir.

### T1 — Aviso de empacotamento — ✅ 17/10

> Premissa de cada pool: "N máquinas provisionadas; o uso medido ocupa X% delas".
> Aviso quando a máquina do destino é menor que 90% da média da origem (2 FSV2 de
> 16 vCPU viram 9 de 4), quando a ocupação passa de 100% e quando fica abaixo de 50%.

Com a capacidade-hora, `instancias` é o que se provisiona e a fração é o que o uso
medido ocupa. O caso conhecido: 2 FSV2 viram 9 VMs.

- README do pacote e resposta da geração: "N máquinas provisionadas; o uso medido
  ocupa X% delas".
- **Aceite:** o mock Azure mostra o aviso no pool FSV2; caracterização atualizada
  de propósito.

### T2 — Isolamento entre subredes na Azure — ✅ 17/10

> Toda subrede espelhada ganha NSG com as regras das dependências (ASG), mais
> `permitir-mesma-subrede` (4095) e `negar-outras-subredes` (4096, origem
> `VirtualNetwork`), antes da `AllowVnetInBound` (65000).

`AllowVnetInBound` libera tráfego entre todas as subredes da VNet: o destino fica
mais aberto que a origem.

- NSG com bloqueio explícito entre subredes, liberando só o que as dependências
  pedem (as mesmas regras que viram ASG).
- **Aceite:** `terraform validate` nos mocks; teste de que subrede sem dependência
  não fala com outra.

### T3 — Banco fora da AWS nas subredes espelhadas — ✅ 17/10

> - Azure: subrede de dados espelhada e delegada ao PostgreSQL Flexible, com zona DNS
>   privada e sem acesso público. A porta 5432 fica aberta para a VNet, porque o banco
>   não entra em ASG. Se houver VM na subrede, o banco fica fora e o pacote avisa.
> - OCI: endpoint privado (`subnet_id`) na subrede de dados espelhada.
> - GCP: acesso privado a serviços (faixa /16 e conexão de peering). Corrige um defeito
>   antigo: o Cloud SQL usava `private_network` sem a conexão e falharia no apply em
>   todo pacote GCP com banco.

O banco migrado não vai para as subredes espelhadas da origem.

- Posicionar o banco nas subredes de dados espelhadas, como a VM.
- **Aceite:** mocks GCP/Azure/OCI com o banco na subrede certa.

### T4 — Disco fantasma do Filestore — ✅ 17/10

> - Categoria nova, `file_storage`, ao custo atual: Filestore, EFS, Azure Files e File
>   Storage da OCI. A correção é na agregação, e por isso vale para fatura já
>   processada; as regras de SKU também foram corrigidas.
> - No bill GCP real, o disco HDD estimado vai de 2.256 GB para cerca de 670 GB.
> - O README avisa que o armazenamento de arquivos não vira disco.
> - Seis relatórios: decisão e confiança iguais. No GCP, a economia da OCI cai
>   US$ 22,77 e a de AWS e Azure sobe US$ 7,85.

Cerca de 1.570 GB de HDD que não existem no bill GCP. A causa é a classificação
(V3.3, lote v3); na sprint, uma guarda no gerador.

- Não gerar disco a partir de linha classificada como armazenamento de arquivos
  (Filestore, EFS, Azure Files), com aviso no README.
- **Aceite:** pacote do bill GCP real sem o disco; aviso presente.

### T5 — Terraform do escopo ([`PLANO_ESCOPO_DE_MIGRACAO`](PLANO_ESCOPO_DE_MIGRACAO.md) E3) — ✅ back 17/10

> Feito com a escolha no corpo (2.1 = não agora): `TerraformGenerationIn.escopo`. Medido
> nas análises locais: checkout/auth gera auth, checkout e pagamentos (50 de 95 VMs),
> o README cita catalogo (antes) e monitoramento (para trás); fatura sem ID de recurso
> recorta pela proporção do inventário (50 de 105). Os 16 pares origem × destino dos
> mocks passam no `terraform validate`. Falta o botão no front.

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

- Fila de precisão: P1 custos auxiliares por zona e P1b burstable na origem ✅ (16/10);
  P2 `perf_coef` espera a decisão 2.7; **P3 ciclo de vida do preço**: P3.0 a P3.2 ✅,
  a fatura como fonte espera o ok da etapa A ([`PLANO_CICLO_DE_VIDA_DO_PRECO`](PLANO_CICLO_DE_VIDA_DO_PRECO.md)).
- ✅ Lote da autoridade: saiu como v4 em 16/10.
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

## 7. Melhorias (sem data)

Em ordem de prioridade, definida pelo usuário em 17/10. M1 e M2 vieram de 16/10.

| # | melhoria | prioridade | de quem | observação |
|---|---|---|---|---|
| M3 | **Completar a análise existente sem rodar o LLM de novo** quando os números não mudam: anexar inventário (ou reanalisar) recalcula o determinístico e reaproveita a narrativa; o LLM só roda se o resultado mudar ou se o usuário pedir | **máxima** | nós | Analisar com cautela antes: a narrativa velha não pode citar número que mudou (marcar desatualizada); a topologia do inventário vai ao LLM hoje sem necessidade. A duplicidade na lista já foi corrigida em 17/10 |
| M9 | ✅ **back e front 17/10** (front `5be3b28`) ([`docs/endpoints/billing.md`](docs/endpoints/billing.md#put-v1billing-filesjob_idalias)). **Renomear o billing (alias), pedido em 17/10.** Rota `PUT /v1/billing-files/{job_id}/alias` grava um nome dado pelo usuário. A tela (lista de billings, lista de análises, menu de análises recentes, cabeçalho da análise, exportações) mostra o alias no lugar do nome do arquivo. Sem alias, o padrão é o nome do arquivo (`source_filename`) | **máxima** | nós (back e front) | Coluna nova `alias` nullable em `billing_reports` (migration; o deploy do develop não roda migration). `source_filename` fica intacto: é o rastro do upload. As respostas ganham `nome_exibicao` = alias ou nome do arquivo, e o front passa a ler esse campo. Isolamento por `user_id`: 404 para billing de outro usuário. Nota ao MCP: campo aditivo no snapshot |
| M10 | ✅ **17/10** ([`PLANO_PRECISAO_OCI`](PLANO_PRECISAO_OCI.md): régua oficial, lote P1 e DE-PARA). **Tabela de custos da OCI, pedido em 17/10.** Corrigir as falhas na identificação do preço dos recursos e dos custos da OCI: recurso sem preço ou com o preço errado, custo que não chega à categoria certa | **máxima** | nós | Primeiro levantar os casos concretos (quais recursos e quais linhas falham, em que tela aparecem) e escrever a análise de impacto; corrigir de forma geral, nunca para uma fatura. Pontos de partida: `cloud_price_points` da OCI (`_OCI_POR_NOME` no `category_rate_client`), regras de classificação da OCI e a cobertura de SKU (`sku_coverage_ratio`) |
| M11 | **Tela: economia comprovada × estimada.** Separar na tela a economia que sai de preço oficial da que sai de premissa ou referência, com a fonte de cada linha, inclusive dos itens que custam zero no destino (NAT, IP público e segredos na OCI) | média | nós (back e front) | Vinda do council da OCI (17/10): o CFO desconfia de um número único com centavos que mistura fontes de qualidade diferente. [`PLANO_PRECISAO_OCI`](PLANO_PRECISAO_OCI.md) §5 |
| M12 | **Resto da precisão da OCI:** instâncias E3/E4/E5 com burst (cobradas pela linha de base) como destino de origem burstable; sync de banco gerenciado por API nas 4 nuvens (hoje taxas oficiais registradas com fonte e data); faturas de origem GCP com pools sem região | média-baixa | nós | [`PLANO_PRECISAO_OCI`](PLANO_PRECISAO_OCI.md) §5 |
| M4 | **Custo de transição** (multicloud temporário): durante as ondas, paga-se as duas nuvens e o tráfego entre elas (egress, VPN). **Complemento: payback na tela**, com o custo da migração informado pelo usuário, somado ao de transição | média-baixa | nós | Hoje a economia mostrada não inclui esse custo. Desenho em [`PLANO_ESCOPO_DE_MIGRACAO`](PLANO_ESCOPO_DE_MIGRACAO.md) §6 |
| M5 | Modo "só o serviço" no plano de migração | baixa | nós | Depende do M4: a parte que fica conversa com a que foi |
| M6 | Coleta automática do inventário pela plataforma (I10) | extremamente baixa | nós | **Apenas se houver muito tempo disponível e estivermos correndo grande risco de o cliente não gostar do projeto.** Nunca guardar chave do cliente; papel delegado só leitura |
| M7 | **Terraform pedido pelo agente (decisão 2.2), pendente de resposta.** O Terraform sai dos números da análise, que o backend controla; a opinião do agente não entra no gerador. Um "Terraform do agente" seria o mesmo arquivo que o usuário já gera pela tela, e depois edita. A pergunta real: o agente pode pedir a geração pelo chat? | pendente de resposta | usuário | Sugestão: sim, como ferramenta do MCP que chama o mesmo gerador, depois do primeiro teste conjunto |
| M8 | **Parecer do agente na tela (decisão 2.3), pendente de resposta.** A "lista" é a página "Análises" do painel e o menu de análises recentes. O parecer que o agente devolve pelo chat fica no nosso banco e hoje não aparece na plataforma. Como linha nova na lista, recriaria a duplicidade corrigida em 17/10 | pendente de resposta | usuário | Sugestão: um quadro "Parecer do agente" dentro da análise do billing, depois do primeiro teste conjunto |
| M1 | **Segurança (era a decisão 2.9):** revogar a chave da service account do GCP (`oneshift-catalogsync`) commitada nas branches órfãs `dev/feat/billing-refactor-prep` e `feat/sku-accuracy-impl`; trocar `MCP_TOKEN`, o token do inspector e a `GCP_BILLING_API_KEY` | não definida | usuário (console do GCP e segredos do cluster), numa janela combinada com o MCP | apagar as branches não resolve: o objeto continua alcançável no GitHub. Depois da revogação, limpar o histórico só se ainda interessar |
| M2 | Recálculo diário agendado: CronJob no infra rodando o sync de preços e o câmbio | não definida | nós preparamos o PR no infra; o merge é do usuário | [`PLANO_CICLO_DE_VIDA_DO_PRECO`](PLANO_CICLO_DE_VIDA_DO_PRECO.md) §11 |
