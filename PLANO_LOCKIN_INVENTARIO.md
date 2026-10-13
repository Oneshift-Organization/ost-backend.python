# PLANO_LOCKIN_INVENTARIO — acoplamento por serviço, endpoint próprio, evidência do inventário

> Prioridade: **alta**. A tela "Acoplamento dos serviços" exibe dado
> inconsistente hoje. Decisão: **endpoint novo, só de leitura**, calculado do
> payload determinístico já salvo. A lista `cross_cloud_equivalents` (tabela de
> preço por destino) **não muda** nos blocos A e B. Só o bloco C, versionado,
> mexe no score.

## 1. O que a tela mostra hoje

Print de referência (relatório AWS): Baixo 32 · Médio 4 · Alto 0 · **A confirmar 32**.

| Sintoma | Causa |
|---|---|
| "Bancos de dados (agregado)" US$ 27.760 *A confirmar* acima de "AmazonRDS" US$ 27.068 *Médio*; "Do total" passa de 100% | A tela monta a visão por serviço a partir de `cross_cloud_equivalents`, que é uma tabela **por destino**. Para cada destino, a linha do RDS fica sem preço e fora da estimativa, e a linha "(agregado)" carrega a cotação da categoria inteira. **No preço não há conta dupla**; ela só aparece quando a tela junta as linhas. |
| Contadores somam 68 para ~30 serviços | A linha agregada existe uma vez por destino (`category/{key}/{provider}`) e a tela conta linhas, não serviços. |
| "A confirmar" na linha agregada | A linha agregada é sintética e não tem `source_metadata`; `technical_evidence` devolve `lock=None`. |
| "M7G · global", "C7G · global"… todos *Baixo* | `equivalence_projection._sources` fixa `vendor_lock_in_level: "low"` para todo pool de compute. É premissa (rehost), não evidência, e a tela não diferencia. M7G/C7G/M8G/R7G são ARM (Graviton). |
| "global" | Falta de região no billing agregado; `_sources` preenche `"global"`. |
| Serviço com nota só da classe → *A confirmar* | Por desenho, a nota herdada do `service_type` (`migration.fonte = "classe"`) não entra no score técnico (`test_class_hypothesis_remains_separate_from_technical_score`). A tela não tem como mostrar a hipótese. |

## 2. Por que não mexer na lista atual

| Consumidor de `cross_cloud_equivalents` | O que quebraria |
|---|---|
| `reconcile_pricing` (`pricing_reconciliation`) | A linha agregada é o que fecha a cotação do destino. Trocar o total pelo resto deixa a cotação sem dono. |
| `decision_support` (score técnico) | Lê o acoplamento por linha × destino. Mudar nível muda score e pode mudar o provedor recomendado. |
| Fluxo do agente / MCP | Mesma lista; qualquer mudança é nova versão de autoridade. |
| Testes | Snapshots de `test_equivalence_projection`, `test_unattributable_category_stays_visible_without_fake_service_price`, separação classe × score. |
| Análises já geradas | O resultado é salvo na geração (`ai_structured_output`) e o GET devolve o salvo. Corrigir o cálculo só ajuda análises novas. |

A visão por serviço é outra coisa e ganha recurso próprio. Calculada **na
leitura** a partir do payload salvo, ela corrige também as análises antigas.

## 3. Princípios

- **Uma linha por serviço de origem.** Pools uma vez, não por destino.
- **Uma despesa, uma linha.** Linha de categoria só mostra o **resto** que
  nenhum serviço listado cobre — apenas nesta visão.
- **`nivel` é o que o score usa.** O que a tela mostra como nível é exatamente o
  que entra em `decision_support`. Hipótese (classe, arquitetura, inventário)
  vai em `nivel_sugerido` + `fonte_sugestao`, nunca em `nivel`, até o bloco C
  promovê-la com versão nova.
- **Toda nota diz de onde veio**: `catalogo` | `premissa` | `declarado`; e
  sugestões: `classe` | `arquitetura` | `inventario`.
- **Inventário só sobe ou confirma, nunca inventa nem rebaixa** sem declaração
  do usuário.
- Função pura no domínio; rota fina. Backend calcula, IA narra.

## 4. Contrato

### Rotas

| Rota | Fonte do payload | Quem usa |
|---|---|---|
| `GET /v1/analyses/{job_id}/service-couplings` | `analysis_jobs.llm_input_payload` | pipeline e rota antiga do agente |
| `GET /v1/agent/submissions/{submission_id}/service-couplings` | `agent_snapshots.payload` do snapshot da submissão | caminho do snapshot (mesma flag das rotas `/agent`) |

Ambas chamam a mesma função `montar_acoplamento(payload)`. Não chamam LLM,
não gravam nada. Sem paginação: são dezenas de linhas e a tela pagina no
cliente; se passar de 500 linhas, seguir a convenção de paginação.

- `404`: não existe ou é de outro usuário.
- `409`: análise ainda não concluída, ou payload antigo sem `decision_workloads`
  (`detail` diz qual); o front mantém a tela atual nesse caso.

### Resposta

```json
{
  "versao": "acoplamento-v1",
  "moeda": "USD",
  "custo_atual": 120345.67,
  "resumo": {
    "servicos": 31, "low": 22, "medium": 4, "high": 0,
    "a_confirmar": 5, "a_confirmar_com_sugestao": 3
  },
  "servicos": [
    {
      "id": "service/banco_dados/AmazonRDS",
      "nome": "AmazonRDS", "tipo": "servico", "service_type": "relational_database",
      "regiao": null, "custo": 27068.10, "fracao": 0.2249,
      "nivel": "medium", "fonte": "catalogo",
      "nivel_sugerido": null, "fonte_sugestao": null,
      "por_destino": {"gcp": "medium", "azure": "medium", "oci": "medium"},
      "alertas": [], "evidencias": [{"fonte": "catalogo", "vendor_lock_in_level": "medium"}]
    },
    {
      "id": "pool/M7G/global",
      "nome": "M7G", "tipo": "pool", "regiao": null, "custo": 5965.42,
      "nivel": "low", "fonte": "premissa",
      "nivel_sugerido": "medium", "fonte_sugestao": "arquitetura",
      "alertas": ["Arquitetura ARM: o destino precisa de família ARM ou de recompilação."]
    },
    {
      "id": "category/banco_dados",
      "nome": "Bancos de dados (demais)", "tipo": "resto_categoria", "custo": 691.90,
      "nivel": null, "fonte": null,
      "nivel_sugerido": "medium", "fonte_sugestao": "classe"
    }
  ],
  "reconciliacao": {
    "soma_linhas": 118200.00, "fora_do_escopo": 2145.67,
    "diferenca": 0.00, "status": "fecha"
  }
}
```

- `nivel`: o nível do lado da origem que `technical_evidence` usa. `null` =
  "A confirmar".
- `por_destino`: o nível que o score usa para cada destino (pior entre origem e
  destino). Igual ao `vendor_lock_in_level` da linha correspondente em
  `cross_cloud_equivalents`. É a garantia de que tela e score não divergem.
- `regiao: null` quando o billing não traz região (hoje "global"). O front
  exibe "região não informada".
- `fora_do_escopo`: linhas financeiras e categorias que não migram (imposto,
  suporte, crédito, desconto, compromisso), as mesmas que `_sources` já exclui
  (`_BILLING_ONLY` + `is_financial_component`).
- `reconciliacao.status`: `fecha` (± centavos) ou `diferenca_de_escopo`, com o
  valor. Não força fechamento.

## 5. Ondas

### Bloco A — endpoint, sem mudar score (corrige a tela)

**A1. `domain/services/acoplamento.py` (puro).**
Entrada: payload determinístico. Reaproveita a mesma seleção de fontes de
`equivalence_projection._sources` (exclusões financeiras, `virtual_server`
coberto por pool), extraída para uma função compartilhada, sem duplicar regra.
- Serviço: uma entrada por `decision_workload`; `nivel`/`fonte` do
  `source_metadata` curado; `por_destino` via `technical_evidence`.
- Pool: uma entrada por pool; `nivel = low`, `fonte = premissa` (o mesmo que o
  score usa hoje).
- Hipótese de classe: `migration.fonte = "classe"` → `nivel_sugerido`.
- Teste de propriedade sobre os snapshots existentes: para toda linha de
  `cross_cloud_equivalents`, `por_destino[destino] == vendor_lock_in_level`.

**A2. Resto de categoria.**
`resto = custos_auxiliares[categoria] − Σ custo dos serviços listados com
service_type da categoria`.
- `resto` ≤ tolerância de centavos → sem linha.
- `resto < 0` (o serviço tem dimensões cobradas em outra categoria) → sem
  linha e `reconciliacao.status = diferenca_de_escopo` com o valor.
- Nome "X (demais)"; `nivel_sugerido` herda a classe dos serviços do mesmo tipo
  presentes no payload; nenhum → sem sugestão.

**A3. Pools: arquitetura e região.**
`perfil_de_familia` (R5 de PLANO_PRECO_REGIONAL): ARM → alerta +
`nivel_sugerido = medium`, `fonte_sugestao = arquitetura`; o `nivel` continua
`low` porque é o que o score usa. `"global"`/vazio → `regiao: null`.

**A4. Rotas, schema e docs.**
Schemas `ServiceCouplingsResponse` em `schemas/`; as duas rotas; doc em
`docs/endpoints/service-couplings.md`; nota de contrato para o front (contadores
vêm de `resumo`, `nivel` × `nivel_sugerido`, `regiao` nula). Integração: os 2
CSVs reais e um relatório com categoria de banco parcial.

Resultado esperado no relatório do print: ~30 linhas em vez de 68; sem
duplicata; Σ "Do total" ≤ 100%; "A confirmar" reduzido aos serviços sem
evidência nenhuma, a maioria com sugestão rotulada.

### Bloco B — inventário como evidência na visão (ainda sem mudar score)

**B1. Atributos de acoplamento no modelo canônico.**
Normalizadores (AWS Config, Asset Inventory, Resource Graph, OCI) gravam em
`Recurso.atributos` só o que é estruturado:
- banco: engine + versão, gerenciado × self-hosted, variante proprietária
  (Aurora, Spanner, Cosmos API) quando o export traz;
- instância: arquitetura, imagem de marketplace, licença BYOL;
- cluster k8s: gerenciado, add-ons do provedor;
- bucket/fila/função: tipo nativo sem equivalente aberto.

**B2. Regras por recurso, data-driven.**
Tabela `lockin_rules` (mesmo estilo de `sku_classification_rules`, sem if/else
por nuvem): atributo → nível + motivo. Ex.: `aurora-postgresql` → medium;
`postgres` gerenciado → low; Spanner/Cosmos/DynamoDB → high.

**B3. Correlação → serviço.**
`correlacao_inventario` já liga recurso ↔ chave de custo. Por serviço:
nível dos recursos ponderado por custo, `fonte_sugestao = inventario`,
`evidencias[]` com ids dos recursos, `cobertura_inventario` (% do custo do
serviço com recurso analisado). Tipo inferido com recurso correlacionado → o
tipo do recurso confirma o `service_type`. Pool ganha `regiao` do recurso.
O endpoint passa a aceitar `inventory_id` quando a análise foi feita com
inventário (o payload já guarda `topologia`).

**B4. Dependências.**
Serviço de nível alto com dependentes `forte` (DADOS) → alerta
`acoplamento_transitivo` nos dependentes. Não soma na nota.

### Bloco C — promover ao score (versionado)

**C1.** `_sources` e `technical_evidence` passam a ler as fontes promovidas:
arquitetura do pool e inventário (quando confirma ou sobe). A classe continua
fora do score, salvo decisão explícita.
**C2.** Ondas: recurso de nível alto vai para onda posterior com os dependentes
(`impor_topologia`).
**C3.** Próxima versão de autoridade (v3 se `feat/preco-regional` entrar
antes), `versoes.py`, nota para o MCP, before/after nos 6 relatórios + 1 com
inventário mock. Aqui `cross_cloud_equivalents` muda, e o endpoint continua
igual porque `nivel` já é "o que o score usa".

## 6. Critérios de aceite

1. Uma entrada por serviço/pool; `resumo` soma o número de serviços.
2. `soma_linhas + fora_do_escopo = custo_atual` (± centavos) ou
   `diferenca_de_escopo` explícita; nunca duplicata.
3. `por_destino` igual ao `vendor_lock_in_level` de `cross_cloud_equivalents`
   em 100% das linhas (teste de propriedade).
4. Blocos A e B: `cross_cloud_equivalents`, `pricing_reconciliation`,
   `decision_support` e snapshots existentes **inalterados**.
5. Toda nota e sugestão com fonte; nenhuma sugestão sem evidência.
6. Análises antigas com `decision_workloads` respondem; sem ele, `409`.
7. Gates de integração dos 2 CSVs verdes; cobertura ≥ 80% em domain/use_cases.

## 7. Dependências e ordem

- A3 usa `perfil_de_familia`, que está em `feat/preco-regional`. Fazer A1, A2 e
  A4 primeiro; A3 depois do merge dela em develop (ou rebase desta branch).
- Ordem: A1 → A2 → A4 → A3 (merge em develop, tela corrigida) → B1 → B2 → B3
  → B4 → C1 → C2 → C3.
- Um commit por onda em `feat/lockin-inventario`; merge só em `develop`.

## 8. Fora do escopo

- Acoplamento pelo código da aplicação (SDKs, chamadas de API).
- Curadoria nova em `seed_service_metadata` além das lacunas achadas nos testes.
- Implementação da tela: o time de front recebe o contrato da §4.
