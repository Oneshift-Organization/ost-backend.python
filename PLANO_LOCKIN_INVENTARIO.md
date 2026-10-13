# PLANO_LOCKIN_INVENTARIO — acoplamento por serviço, endpoint próprio, evidência do inventário

> Prioridade: **0**. A tela "Acoplamento dos serviços" exibe dado inconsistente
> hoje.
>
> **Estado (13/10/2026):** blocos A e B implementados, contrato em
> [`docs/endpoints/service-couplings.md`](docs/endpoints/service-couplings.md).
> A implementação mudou o desenho do A2 e corrigiu uma premissa da §2; ver §9 e §10. Decisão: **endpoint novo, só de leitura**, calculado do payload
> determinístico já salvo. A lista `cross_cloud_equivalents` (tabela de preço
> por destino) **não muda** nos blocos A e B. Só o bloco C, versionado, mexe no
> score.

## 1. O que a tela mostra hoje

Print de referência (relatório AWS): Baixo 32 · Médio 4 · Alto 0 · **A confirmar 32**.

| Sintoma | Causa |
|---|---|
| "Bancos de dados (agregado)" US$ 27.760 *A confirmar* acima de "AmazonRDS" US$ 27.068 *Médio*; "Do total" passa de 100% | A tela monta a visão por serviço a partir de `cross_cloud_equivalents`, que é uma tabela **por destino**. Para cada destino, a linha do RDS fica sem preço e fora da estimativa, e a linha "(agregado)" carrega a cotação da categoria inteira. **No preço não há conta dupla**; ela só aparece quando a tela junta as linhas. |
| Contadores somam 68 para ~30 serviços | A linha agregada existe uma vez por destino (`category/{key}/{provider}`) e a tela conta linhas, não serviços. |
| "A confirmar" na linha agregada | A linha agregada é sintética e não tem `source_metadata`; `technical_evidence` devolve `lock=None`. |
| "M7G · global", "C7G · global"… todos *Baixo* | `equivalence_projection._sources` fixa `vendor_lock_in_level: "low"` para todo pool de compute. É premissa (rehost), não evidência, e a tela não diferencia. O score não lê pool: lê o serviço de máquinas virtuais (AmazonEC2), que no catálogo é **médio**. A tela mostrava baixo para o mesmo compute que o score trata como médio. |
| "global" | Falta de região no billing agregado; `_sources` preenche `"global"`. |
| Serviço com nota só da classe → *A confirmar* | Por desenho, a nota herdada do `service_type` (`migration.fonte = "classe"`) não entra no score técnico (`test_class_hypothesis_remains_separate_from_technical_score`). A tela não tem como mostrar a hipótese. |
| Nenhuma nota considera o que precisa migrar junto | O acoplamento hoje é só "o serviço é exclusivo da nuvem?". A topologia do inventário já sabe o que depende de quê, mas isso não chega à tela de acoplamento. |

## 2. Por que não mexer na lista atual

| Consumidor de `cross_cloud_equivalents` | O que quebraria |
|---|---|
| `reconcile_pricing` (`pricing_reconciliation`) | A linha agregada é o que fecha a cotação do destino. Trocar o total pelo resto deixa a cotação sem dono. |
| `decision_support` (score técnico) | Lê o acoplamento dos serviços da fatura (`decision_workloads`) × destino, com mapeamento único por destino. Mudar o nível de um serviço muda score e pode mudar o provedor recomendado. (A primeira versão deste plano dizia que o "Baixo" dos pools entrava no score: não entra, só na tabela de equivalência.) |
| Fluxo do agente / MCP | Mesma lista; qualquer mudança é nova versão de autoridade. |
| Testes | Snapshots de `test_equivalence_projection`, `test_unattributable_category_stays_visible_without_fake_service_price`, separação classe × score. |
| Análises já geradas | O resultado é salvo na geração (`ai_structured_output`) e o GET devolve o salvo. Corrigir o cálculo só ajuda análises novas. |

A visão por serviço é outra coisa e ganha recurso próprio. Calculada **na
leitura** a partir do payload salvo, ela corrige também as análises antigas.

## 3. Princípios

- **Acoplamento tem duas dimensões**, e a tela mostra as duas:
  - **exclusividade**: o serviço usa recurso ou funcionalidade que só existe
    nesta nuvem (catálogo, arquitetura, atributos do inventário);
  - **dependência**: o que precisa migrar junto ou antes, porque separar quebra
    ou degrada (banco acessado entre nuvens, balanceador longe das máquinas).
    Vem da topologia do inventário.
- **Uma linha por serviço da fatura**, os mesmos que o score lê. Pools ficam
  dentro do serviço de máquinas virtuais, não por destino nem como linha
  própria.
- **Uma despesa, uma linha.** Orçamento de categoria é recorte de cotação e não
  vira linha nesta visão.
- **`nivel` é de onde o score parte.** O nível mostrado é o lado da origem que
  `technical_evidence` usa, e `por_destino` é exatamente o que entra no eixo
  técnico do `decision_support`. Hipótese (classe, arquitetura, inventário,
  dependência) vai em `nivel_sugerido` + `sugestoes[]`, nunca em `nivel`, até o
  bloco C promovê-la com versão nova.
- **Toda nota diz de onde veio.** `fonte` do nível: `catalogo` | `informado`. Fonte de cada sugestão: `classe` | `arquitetura` |
  `inventario` | `dependencias`.
- **Inventário só sobe ou confirma, nunca inventa nem rebaixa** sem declaração
  do usuário. Sem inventário, a visão funciona igual, sem a dimensão de
  dependência.
- Função pura no domínio; rota fina. Backend calcula, IA narra.

## 4. Contrato

Implementado no bloco A. A fonte de verdade é
[`docs/endpoints/service-couplings.md`](docs/endpoints/service-couplings.md):

- `GET /v1/analyses/{job_id}/service-couplings` (payload salvo da análise) e
  `GET /v1/agent/submissions/{submission_id}/service-couplings` (snapshot do
  envio, atrás das flags do agente). Mesma função, sem LLM, sem gravação.
- Uma linha por serviço da fatura (`decision_workloads`), com `nivel`
  (lado da origem), `por_destino` (o que o eixo técnico usa), `sugestoes`,
  `nivel_sugerido`, `pools` dentro do serviço de máquinas virtuais, `resumo` e
  `reconciliacao`.
- `404` para o que não existe ou é de outro usuário; `409` para análise não
  concluída ou sem a lista de serviços.

O bloco B acrescenta, sem quebrar o contrato: `com_inventario`,
`resumo.migram_em_grupo`, `servicos[].regiao` e `servicos[].dependencias`:

```jsonc
"dependencias": {
  "nivel": "high",
  "grupo": "Pagamentos", "onda": 2,
  "migra_junto_com": [
    {"id": "service/compute/AmazonEC2", "nome": "AmazonEC2", "custo": 5965.42, "natureza": "dados"}
  ],
  "depende_de": [{"id": "service/networking/AmazonVPC", "nome": "AmazonVPC", "natureza": "encaminhamento"}],
  "dependentes": 3,
  "banco_compartilhado": true,
  "custo_do_grupo": 33033.52,
  "cobertura": 0.93
}
```

`dependencias` fica `null` sem inventário, ou quando o billing não tem
`resource_id` (Cost Explorer agregado) e não dá para ligar recurso a serviço.

## 5. Ondas

### Bloco A — endpoint, sem mudar score (feito)

**A1. `domain/services/acoplamento_por_servico.py` (puro).** (O nome evita
confusão com `topologia/acoplamento.py`, que agrupa unidades.)
- Uma linha por serviço de `decision_workloads(payload)`, a mesma função e os
  mesmos filtros que o score usa. Os serviços do payload somam o gasto do
  período (92.103,49 contra 92.103,48 no bill AWS), então a visão fecha por
  construção.
- `nivel` = `technical_evidence(serviço, None)`: o lado da origem, como o score
  o enxerga (tipo inferido sai desconhecido, como lá).
- `por_destino` = `technical_evidence(serviço, alvo único)` por cenário do
  `resumo_executivo`, com a regra do destino único do score (`unique_targets`,
  só renomeada de `_targets` para reuso; hash da autoridade atualizado sem subir
  versão).
- Sugestão `classe` quando `migration.fonte = "classe"`.
- Teste de propriedade nos dois bills reais: `por_destino` é igual, serviço a
  serviço, à evidência `lock_in` do eixo técnico do `decision_support`.

**A2. Reconciliação, no lugar do "resto de categoria".** O desenho original
emitia "Bancos de dados (demais)" com o resto do orçamento da categoria.
Medindo o payload, isso não faz sentido numa lista por serviço: cada dólar já
pertence a um serviço da fatura, e o orçamento de categoria é outro recorte dos
mesmos dólares, que só serve à cotação. A linha agregada some da visão. O que
sobra é `fora_do_escopo` (imposto, crédito, compromisso, custo zero) e a
`diferenca` contra o gasto, com `status`.

**A3. Pools dentro das máquinas virtuais.** Os pools viram `pools[]` do serviço
curado de `virtual_server` de maior custo (onde está o custo deles); sem esse
serviço, `pools_sem_servico`. Cada pool traz `arquitetura` e `burstable`
(`perfil_de_familia`), `regiao: null` no lugar de `global`, e
`destinos_sem_mesma_arquitetura` lido de `arquitetura_diferente` da cotação.
A sugestão `arquitetura` só aparece com essa evidência: depois do R5 os pools
Graviton são cotados em ARM nos três destinos, então ARM sozinho não é
acoplamento.

**A4. Rotas, schema e docs.** `schemas/service_couplings.py`,
`use_cases/analysis/consultar_acoplamento.py`, as duas rotas, doc do endpoint e
índices. Testes de endpoint: as duas rotas devolvem o mesmo corpo para o mesmo
payload; 404 de outro usuário; 409 para pendente e para payload sem lista;
flag desligada.

**Resultado no relatório do print (bill AWS real):** 24 serviços (eram 227
linhas na tabela por destino), baixo 6, médio 3, alto 0, a confirmar 15;
82.020,40 nas linhas + 10.083,09 fora do escopo = 92.103,48. Os 15 "a
confirmar" agora são lacuna real de catálogo, não artefato de tela (§9).

### Bloco B — inventário: dependência e exclusividade (feito)

A topologia já calcula o que precisa: dependências com natureza (`dados`,
`encaminhamento`, `servico`, `administrativo`, `declarada`) e acoplamento
(`forte` = migram juntas, `fraco` = ordem), grupos de movimentação, bancos
compartilhados, grupos grandes, criticidade e ondas
(`domain/services/topologia/`). O billing detalhado já liga recurso a serviço
(`CustoDeRecurso.servico`). Falta juntar as duas coisas por serviço da fatura.

**B1. Dependências por serviço, congeladas na análise.**
Na montagem da topologia (`topologia_no_payload.montar_topologia`), gravar
`topologia.detalhe.servicos`: para cada serviço da fatura e cada pool
(via `posicionamento`), os recursos correlacionados → unidades → grupo de
movimentação, e daí `migra_junto_com` (outros serviços/pools do mesmo grupo,
com custo e natureza), `depende_de` (dependências fracas: ordem),
`dependentes`, `banco_compartilhado`, `onda`, `custo_do_grupo`, `cobertura`.
- Fica em `detalhe`: persistido, fora do que o LLM e o agente recebem.
- Chave nova e aditiva: não muda a análise corrigida, então não sobe
  `VERSAO` da topologia nem invalida snapshot.
- Análise feita antes de B1 → `dependencias: null` com motivo "análise
  anterior ao eixo de dependência".

**B2. Nível de dependência (regra no domínio, com limiares nomeados).**
- `low`: sem dependência forte (isolado, ou só fraca/administrativa). Migra
  sozinho, respeitando a ordem.
- `medium`: em grupo forte com outros serviços ou pools. "Migra junto com N
  itens, US$ X/mês."
- `high`: o grupo contém item de exclusividade alta (o grupo inteiro herda a
  dificuldade); ou banco compartilhado consumido por grupos de fora
  (`BancoCompartilhado`); ou grupo grande (`GRUPO_GRANDE_*`, já usado na
  topologia).
Vira sugestão `dependencias` com o motivo escrito. Dependência rebaixada pela
topologia (ferramenta compartilhada, hub, balanceador compartilhado) não conta
como forte — a mesma regra que impede o grupo de engolir o ambiente inteiro.

**B3. Exclusividade pelos atributos do recurso.**
A topologia já normaliza banco (`motor`, `versao`, `classe`,
`alta_disponibilidade`) e instância (`tipo_maquina`, `vcpus`; `arquitetura`
só vem do AWS Config — nos demais, derivada do tipo pelo `perfil_de_familia`). Regras data-driven em
tabela `lockin_rules` (mesmo estilo de `sku_classification_rules`, sem if/else
por nuvem): atributo → nível + motivo. Ex.: motor `aurora-postgresql` → medium;
`postgres` gerenciado → low; Spanner/Cosmos/DynamoDB → high. Normalizadores
ganham só o que o export traz estruturado. Vira sugestão `inventario`,
ponderada por custo, com ids dos recursos em `evidencias[]`. Tipo inferido com
recurso correlacionado → o tipo do recurso confirma o `service_type`.

**B4. Visão e tela.**
A função do A1 lê `topologia.detalhe.servicos` quando existe: preenche
`dependencias`, as sugestões `dependencias`/`inventario`, `regiao` do recurso e
`resumo.migram_em_grupo`. Contrato do front atualizado: mostrar "migra junto
com" e o custo do grupo ao lado do nível.

### Bloco C — promover ao score (versionado)

**C1.** `technical_evidence` passa a ler exclusividade promovida: arquitetura
do pool e inventário (quando confirma ou sobe). A classe continua fora do
score, salvo decisão explícita.
**C2.** O eixo técnico do `decision_support` ganha o componente "dependências"
(nível de dependência ponderado por custo). Muda a política de decisão:
`policy_version` nova.
**C3.** Ondas: grupo com exclusividade alta vai para onda posterior junto com os
dependentes (`impor_topologia`).
**C4.** Próxima versão de autoridade (v3 se `feat/preco-regional` entrar
antes), `versoes.py`, nota para o MCP, before/after nos 6 relatórios + 1 com
inventário mock. Aqui `cross_cloud_equivalents` muda, e o endpoint continua
igual porque `nivel` já é "o que o score usa".

## 6. Critérios de aceite

1. Uma entrada por serviço; `resumo` soma o número de serviços. ✅
2. `soma_linhas + fora_do_escopo = custo_atual` (± centavos) ou
   `diferenca_de_escopo` explícita; nunca duplicata. ✅ (os dois bills reais fecham)
3. `por_destino` igual à evidência `lock_in` do eixo técnico do
   `decision_support`, serviço a serviço (teste de propriedade). ✅
4. Blocos A e B: `cross_cloud_equivalents`, `pricing_reconciliation`,
   `decision_support` e snapshots existentes **inalterados**. ✅ no A
5. Toda nota e sugestão com fonte e motivo; nenhuma sugestão sem evidência.
6. Com inventário mock: todo serviço com recurso correlacionado tem
   `dependencias`; `migra_junto_com` é simétrico dentro do grupo; dependência
   rebaixada pela topologia não gera `medium`/`high`.
7. Sem inventário: resultado igual ao do bloco A.
8. Análises antigas com `decision_workloads` respondem; sem ele, `409`. ✅
9. Gates de integração dos 2 CSVs verdes; cobertura ≥ 80% em domain/use_cases.

## 7. Dependências e ordem

- Bloco A feito sobre a develop com `feat/preco-regional` (`a429c7f`).
- Ordem: bloco A (merge em develop, tela corrigida) → B1 → B2 → B3 → B4 → C1 →
  C2 → C3 → C4.
- Nada deste plano toca Terraform.
- Um commit por onda em `feat/lockin-inventario`; merge só em `develop`.

## 8. Fora do escopo

- Acoplamento pelo código da aplicação (SDKs, chamadas de API).
- Eixo de dependência em billing sem `resource_id` (Cost Explorer agregado):
  sem ligação recurso → serviço, `dependencias` fica `null` e a tela diz por quê.
- Curadoria nova em `seed_service_metadata` além das lacunas achadas nos testes.
- Implementação da tela: o time de front recebe o contrato da §4.

## 9. Revisão do bloco A (13/10/2026)

**O que a implementação mudou no plano**
- A2 deixou de ser "resto de categoria" (ver A2): na visão por serviço, o
  orçamento de categoria é contagem dupla do mesmo dinheiro.
- A §2 dizia que o "Baixo" fixo dos pools entra no score. Não entra: o score lê
  os serviços da fatura, e o compute pela nota do catálogo do serviço de
  máquinas virtuais (médio no AmazonEC2 e no Compute Engine). Os pools só
  aparecem em `cross_cloud_equivalents`. A tela mostrava baixo para o compute
  que o score trata como médio; a visão nova mostra médio.
- ARM sozinho não virou sugestão: com o R5 os pools Graviton são cotados em ARM
  nos três destinos. A sugestão `arquitetura` exige a evidência
  `arquitetura_diferente` da cotação.

**O que os números mostram (lacunas reais, fora do bloco A)**
- **15 dos 24 serviços do bill AWS estão "a confirmar"**: o catálogo não tem
  `vendor_lock_in_level` para eles (AWS WAF, Amazon Translate…) ou o tipo foi
  inferido. No bill AWS nenhum ganhou sugestão de classe: a herança
  (`learn_class_defaults`, em `build_migration_context`) só entra quando o
  serviço não tem expectativa nenhuma. Serviço curado com `requires_refactoring`
  e sem lock-in fica sem nota, e há tipos sem nenhum exemplo curado com lock-in. Estender a herança a eles
  muda `migration.classe_migracao`, que a estratégia 7R usa: é mudança de
  decisão e precisa de versão. Fica para o bloco C ou para curadoria.
- **Classificação errada:** "Amazon Managed Streaming for Apache Kafka" sai
  inferido como `relational_database` no bill real e como `network` no mock.
  Mesma fila do item de Filestore e Source Repositories (regras de
  classificação).
- Bloco B é o que mais reduz o "a confirmar" com evidência: tipo do recurso
  confirma o `service_type` e os atributos dão a exclusividade.

## 10. Revisão do bloco B (13/10/2026)

**Feito**
- B1/B2: `domain/services/topologia/servicos_do_billing.py` liga cada linha de
  custo com `resource_id` ao recurso do inventário (`IndiceDeApelidos`), à
  unidade e ao grupo de movimentação. Nível: banco compartilhado ou grupo
  grande → alto; migra junto com outro serviço → médio; resto → baixo. A
  natureza do "migra junto" é a mais concreta entre mesma unidade, dados,
  encaminhamento, serviço, declarada, administrativa e mesmo grupo.
- Congelado na análise em `topologia.detalhe.servicos` (`montar_topologia`
  recebe os custos por recurso). Fica fora do que o LLM e o agente recebem; não
  muda a análise corrigida nem a versão da topologia.
- B4: a visão lê o bloco; médio e alto viram sugestão `dependencias`;
  `com_inventario`, `eixo_de_dependencia`, `resumo.migram_em_grupo`.

**Correção que o mock da OCI mostrou:** a visão copiava o filtro do score e
escondia gasto sem categoria (`unknown`): 5 dos 6 serviços do mock sumiam e
caíam em "fora do escopo" junto com imposto. Agora são linhas com
`entra_no_score: false`, e `fora_do_escopo` é só o financeiro. O filtro do score
foi exposto (`all_workloads`, `is_scored_workload`) sem mudar comportamento.

**Achados**
- No mock da OCI os serviços chegam como `DATABASE`, `BLOCK_STORAGE`,
  `OBJECT_STORAGE`… sem categoria: 5 de 6 ficam fora do score. É lacuna de
  classificação dos nomes de serviço da OCI, mesma fila do Filestore/Source
  Repositories e do MSK.
- No mock GCP com inventário, só o Compute Engine se liga a recurso (migra junto
  com o WordPress, imagem de marketplace cobrada como serviço); o Cloud SQL não
  casa. Vale conferir o apelido do Cloud SQL no normalizador do Asset Inventory.

**B3 (exclusividade pelos atributos do recurso), feito.** Mudança em relação
ao plano: as regras ficaram numa tabela de dados no domínio
(`domain/services/topologia/exclusividade.py`), não numa tabela `lockin_rules`
no banco. São 13, mudam por revisão de código e não por operação, e assim não
há migration nem seed a mais para o ambiente. Padrão do motor ou do tipo nativo
→ nível e motivo, a primeira que casa vence: DynamoDB, Spanner, Bigtable e
Cosmos DB alto; Aurora, Oracle e SQL Server médio; PostgreSQL, MySQL, MariaDB,
Redis, Valkey e Memcached baixo. Por serviço, vale o maior nível entre os
recursos, com a fração do custo. Vira a sugestão `inventario` (qualquer nível,
inclusive baixo, porque é evidência do que o cliente usa). No mock da OCI, o
Autonomous Database sai médio pela licença Oracle.

**Ficou para o bloco C:** o tipo do recurso confirmar o `service_type` de um
serviço inferido (muda o score), e a exclusividade do inventário entrar no
`technical_evidence`.
