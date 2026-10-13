# PLANO_LOCKIN_INVENTARIO — acoplamento consistente, com evidência do inventário

> Prioridade: **alta**. A tela "Acoplamento dos serviços" exibe dado
> inconsistente hoje (ver §1). Parte do conserto (L1–L3) não depende do
> inventário e vem primeiro; o inventário (L4–L7) reduz o "A confirmar" que
> sobrar.

## 1. O que a tela mostra hoje e por que está errado

Print de referência (relatório AWS): Baixo 32 · Médio 4 · Alto 0 · **A confirmar 32**.

| Sintoma | Causa no código |
|---|---|
| "Bancos de dados (agregado)" US$ 27.760 *A confirmar* logo acima de "AmazonRDS" US$ 27.068 *Médio* | `equivalence_pricing.attach_category_prices` cria uma linha sintética `"{categoria} (agregado)"` quando o orçamento da categoria não fecha centavo a centavo com os serviços curados. A linha não tem `source_metadata`, então `technical_evidence` devolve `lock=None` → "A confirmar". |
| A mesma despesa aparece duas vezes (23% + 22%), soma do "Do total" passa de 100% | A linha agregada carrega o custo da **categoria inteira**, que já inclui o RDS listado em separado. |
| Contadores inflados (68 linhas para ~30 serviços) | Linha agregada é por **provedor de destino** (`category/{key}/{provider}`); a contagem é workload × destino, não serviço. |
| "M7G · global" *Baixo* | `equivalence_projection._sources` fixa `vendor_lock_in_level: "low"` para todo pool de compute. É premissa, não evidência — e a tela não distingue. |
| Serviços com tipo inferido → *A confirmar* | `technical_evidence` devolve `(None, None)` quando `type_source == "inferido"`. Correto como regra, mas nada tenta resolver depois. |

Conclusão: dos 32 "A confirmar", parte é **artefato de apresentação** (linhas
agregadas duplicadas por destino), parte é **falta de evidência real**. O
primeiro se resolve sem inventário; o segundo é onde o inventário entra.

## 2. Princípios

- **Acoplamento é propriedade do serviço de origem**, não do par origem×destino.
  A tela de acoplamento lista serviço da fatura uma vez só.
- **Uma despesa, uma linha.** Linha agregada só existe para o **resto** da
  categoria (o que não foi atribuído a serviço curado), nunca para o total.
- **Toda nota diz de onde veio**: `catalogo` | `classe` (herdada do
  service_type) | `inventario` | `premissa` | `declarado`. Premissa fixa
  (compute = low) passa a ser rotulada como premissa.
- **Inventário só sobe ou confirma, nunca inventa.** Sem recurso correlacionado
  a nota continua a do catálogo; a ausência continua "A confirmar".
- Backend calcula, IA narra. Mudança de nota → nova versão de autoridade
  (`agent-authority-v3`).

## 3. Ondas

### Bloco A — consistência (sem inventário)

**L1. Linha agregada = resto, não total.**
`attach_category_prices` passa a emitir só `base − Σ membros atribuídos`. Se o
resto for ≤ tolerância de centavos, nenhuma linha agregada. Teste: soma de
`source_monthly_cost` das linhas da tela = custo atual (± centavos).

**L2. Visão de acoplamento por serviço de origem.**
Novo bloco `acoplamento_servicos` no payload (domínio puro,
`domain/services/acoplamento.py`): uma entrada por serviço/pool de origem com
`nivel`, `fonte`, `custo`, `fracao`, `evidencias[]`. Nível = pior entre as
fontes conhecidas (mesma regra conservadora de `learn_class_defaults`). Os
contadores da tela leem daqui, não das linhas × destino. `cross_cloud_equivalents`
continua igual para a tabela de equivalência.

**L3. Linha agregada herda a classe.**
O resto agregado tem `service_type` conhecido (`relational_database`,
`network_egress`, `block_storage`…). Aplicar `learn_class_defaults` →
`fonte="classe"`. Egresso/discos deixam de ser "A confirmar" sem inventar nada:
é o mesmo padrão já usado para serviços sem curadoria. Pool de compute passa a
`fonte="premissa"`.

**L3b. Pools de compute (as linhas "M7G · global", "C7G · global"…).**
- Nota vem do perfil de família (`perfil_de_familia`): x86 → baixo
  (`fonte=premissa`); **ARM (Graviton/Ampere/Axion/Cobalt) → médio** com motivo
  "exige destino ARM ou recompilação", rebaixado a baixo só se o destino
  selecionado tiver família ARM equivalente. Hoje todos saem "Baixo" fixo.
- "global" não é região: é a falta dela no billing agregado. A tela recebe
  `regiao: null` + rótulo "região não informada"; com inventário, a região vem
  do recurso correlacionado.
- Pool listado uma vez (não por destino) — coberto por L2.

Meta do bloco A no relatório do print: "A confirmar" cai de 32 para só os
serviços de tipo inferido sem classe.

### Bloco B — inventário como evidência

**L4. Atributos de acoplamento no modelo canônico.**
Normalizadores (AWS Config, Asset Inventory, Resource Graph, OCI) extraem para
`Recurso.atributos` só o que é estruturado:
- banco: engine + versão, gerenciado vs. self-hosted, extensões/feature
  proprietária quando o export traz (Aurora, Spanner, Cosmos API);
- instância: arquitetura, imagem proprietária/marketplace, licença BYOL;
- cluster k8s: gerenciado, add-ons do provedor;
- bucket/fila/função: tipo nativo sem equivalente aberto.
Nada de texto livre.

**L5. Regras de acoplamento por recurso** (data-driven, tabela
`lockin_rules` no mesmo estilo de `sku_classification_rules`, sem if/else por
nuvem): atributo → nível + motivo. Ex.: engine `aurora-postgresql` → médio
("compatível com Postgres, features Aurora não"); `postgres` gerenciado → baixo;
Spanner/Cosmos/DynamoDB → alto. Resultado vai em `evidencias[]` com o id do
recurso.

**L6. Correlação → serviço da fatura.**
`correlacao_inventario` já liga recurso ↔ chave de custo. Agregar o nível dos
recursos por serviço de origem, **ponderado por custo**, e:
- serviço "A confirmar" com recursos correlacionados → ganha nota, `fonte=inventario`;
- serviço com nota de catálogo → inventário pode **subir** (achou feature
  proprietária) ou **confirmar**; nunca baixar abaixo do catálogo sem
  declaração do usuário;
- tipo inferido com recurso correlacionado → o tipo do recurso confirma o
  `service_type` (resolve a regra `inferido → None`).
Cobertura exibida: "% do custo do serviço com recurso analisado".

**L7. Dependências pesam.**
Grafo do inventário: serviço de nível alto com dependentes `forte` (DADOS)
propaga um alerta `acoplamento_transitivo` para os dependentes e entra na ordem
de ondas (recurso alto vai para onda posterior com os dependentes). Não muda a
nota do dependente — vira evidência e alerta, para não contar duas vezes.

### Bloco C — saída

**L8. Score e autoridade.** `decision_support` lê `acoplamento_servicos`
(ponderado por custo) no lugar da lista por destino. `agent-authority-v3`,
`versoes.py`, nota para o MCP (campo novo, fontes). Snapshot E2E e
before/after nos 6 relatórios + 1 com inventário mock.

**L9. Docs.** `docs/precision-engine.md` (seção acoplamento),
`docs/endpoints/inventories.md` (efeito no acoplamento), contrato do front
(`fonte`, `cobertura`, contadores).

## 4. Fora do escopo

- Nota de acoplamento por código da aplicação (SDK, chamadas de API) — exigiria
  scanner de repositório.
- Mudar o catálogo curado (`seed_service_metadata`) além de corrigir lacunas
  achadas nos testes.
- Front: só o contrato; implementação da tela é do time de front.

## 5. Critérios de aceite

1. Σ custo das linhas de acoplamento = custo atual (± centavos), sem duplicata.
2. Cada serviço aparece uma vez; contadores somam o número de serviços.
3. Toda nota tem `fonte`; nenhuma nota sem evidência ou premissa declarada.
4. Com inventário mock: "A confirmar" ≤ serviços sem recurso correlacionado e
   sem classe; nenhuma nota abaixo do catálogo sem declaração.
5. Sem inventário: resultado do bloco A idêntico (inventário é opcional).
6. Gates de integração dos 2 CSVs verdes; cobertura ≥ 80% em domain/use_cases.

## 6. Ordem e entrega

L1 → L2 → L3 (merge em develop, já corrige a tela) → L4 → L5 → L6 → L7 → L8 → L9.
Um commit por onda em `feat/lockin-inventario`; merge só em `develop`.
