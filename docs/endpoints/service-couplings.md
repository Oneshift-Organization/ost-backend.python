# Endpoints `…/service-couplings` — acoplamento por serviço

A tela "Acoplamento dos serviços" lista, para cada serviço da fatura, o quanto
ele depende de recursos exclusivos da nuvem atual. Estas rotas entregam essa
visão pronta: **uma linha por serviço**, com o nível que o score usa e, à parte,
as hipóteses que ele não usa.

Na política `score-7rs-v4`, lock-in continua no eixo técnico, junto com adaptação,
compatibilidade funcional e dependências. A dimensão `dependencias` do inventário
pontua separadamente, somente na fração correlacionada. Sem inventário, permanece
desconhecida. Hipóteses de esforço por classe curada são explicitadas no detalhamento
do score e não promovidas a fatos de acoplamento nem a estratégias confirmadas.
O score pode avaliar conservadoramente alternativas de mesma capacidade; esta
rota usa a mesma evidência em resultados v4, mas continua sem escolher um serviço
de destino quando há múltiplas alternativas. Resultados anteriores mantêm a
interpretação antiga (destinos ambíguos sem classificação).
Consultar o ledger de `decision_support` para as regras e evidências de cada nota.

> Plano e decisões: [`PLANO_LOCKIN_INVENTARIO.md`](../../PLANO_LOCKIN_INVENTARIO.md).
> Convenções gerais: [`api-conventions.md`](../api-conventions.md).

## Por que existe

A tela montava essa visão a partir de `cross_cloud_equivalents`, que é a
tabela de **preço por destino**. Daí vinham três erros, todos de leitura e não
de cálculo:

| sintoma na tela | causa |
|---|---|
| "Bancos de dados (agregado)" ao lado de "AmazonRDS", e o "Do total" passando de 100% | a linha agregada carrega a cotação da categoria inteira, que já inclui o RDS. Para o preço está certo (em cada destino só uma das duas entra na estimativa); numa lista por serviço, conta duas vezes |
| contadores somando 68 para cerca de 30 serviços | a tabela tem uma linha por serviço **e por destino** |
| pools de compute (`M7G · global`) como "Baixo" | a tabela fixa acoplamento baixo para pool; o score usa a nota do catálogo do serviço de máquinas virtuais (médio no AmazonEC2) |

`cross_cloud_equivalents` continua igual: ela fecha a reconciliação de preço e
alimenta o Terraform. A tela de acoplamento passa a ler daqui.

## Rotas

| Rota | De onde vem o payload | Observação |
|---|---|---|
| `GET /v1/analyses/{job_id}/service-couplings` | o payload salvo da análise (`llm_input_payload`) | fluxo da tela e rota antiga do agente |
| `GET /v1/agent/submissions/{submission_id}/service-couplings` | o snapshot que o agente leu | só com `AGENT_SNAPSHOT_ENABLED` e `AGENT_FLOW_ENABLED`; erros no envelope `{codigo, mensagem, retryable}` |

As duas chamam a mesma função (`domain/services/acoplamento_por_servico.py`),
não chamam LLM e não gravam nada. Como o cálculo é feito na leitura, **análises
antigas também saem corrigidas**, desde que tenham a lista de serviços.

Sem paginação: são dezenas de linhas (24 no bill AWS de referência) e a tela
pagina no cliente.

Erros no envelope `{"detail": {"codigo", "mensagem", "retryable"}}`:

| Status | `codigo` | Quando | `retryable` |
|---|---|---|---|
| 404 | `ANALISE_NAO_ENCONTRADA` / `ENVIO_NAO_ENCONTRADO` | não existe ou é de outro usuário | não |
| 409 | `ANALISE_NAO_CONCLUIDA` | a análise ainda está rodando | **sim** |
| 409 | `ANALISE_FALHOU` | a análise terminou com falha | não |
| 409 | `ANALISE_SEM_SERVICOS` | análise anterior à lista de serviços (`decision_workloads`); a tela mantém o comportamento antigo | não |
| 404 | `SNAPSHOT_DESLIGADO` | só na rota do agente, com as flags desligadas | não |

## Resposta

Exemplo real, recortado, do bill AWS de referência (Cost Explorer, mar/2026):

```jsonc
{
  "versao": "acoplamento-v1",
  "moeda": "USD",
  "provedor_origem": "aws",
  "custo_atual": 92103.48,
  "resumo": {
    "servicos": 24, "low": 6, "medium": 3, "high": 0,
    "a_confirmar": 15, "a_confirmar_com_sugestao": 0,
    "migram_em_grupo": 0, "fora_do_score": 0
  },
  "servicos": [
    {
      "id": "service/compute/AmazonEC2",
      "nome": "AmazonEC2", "categoria": "compute", "service_type": "virtual_server",
      "tipo_inferido": false, "custo": 33298.93, "fracao": 0.3615,
      "nivel": "medium", "fonte": "catalogo",
      "nivel_sugerido": null, "sugestoes": [],
      "por_destino": {"azure": "medium", "gcp": "medium", "oci": "medium"},
      "equivalente_por_destino": {
        "azure": "Azure Virtual Machines", "gcp": "Compute Engine",
        "oci": "Oracle Cloud Infrastructure Compute"
      },
      "refatoracao_por_destino": {"azure": null, "gcp": false, "oci": null},
      "classe_migracao": "replatform", "esforco": "medio",
      "pools": [
        {
          "familia": "M7G", "regiao": "us-east-1", "custo": 5965.42,
          "arquitetura": "arm", "burstable": false, "coberto_por_compromisso": false,
          "destinos_sem_mesma_arquitetura": [], "sugestoes": []
        }
      ],
      "alertas": []
    },
    {
      "id": "service/database/AmazonRDS",
      "nome": "AmazonRDS", "categoria": "database",
      "service_type": "managed_relational_database",
      "tipo_inferido": false, "custo": 27068.10, "fracao": 0.2939,
      "nivel": "medium", "fonte": "catalogo",
      "nivel_sugerido": null, "sugestoes": [],
      "por_destino": {"azure": null, "gcp": "medium", "oci": null},
      "pools": [], "alertas": []
    }
  ],
  "pools_sem_servico": [],
  "reconciliacao": {
    "soma_linhas": 82020.40, "fora_do_escopo": 10083.09,
    "custo_atual": 92103.48, "diferenca": -0.01, "status": "fecha"
  },
  "com_inventario": false,
  "eixo_de_dependencia": false
}
```

Com inventário (mock OCI de São Paulo, análise com `inventory_id`), um serviço
ganha a dimensão de dependência:

```jsonc
{
  "nome": "DATABASE", "custo": 999.94, "entra_no_score": false,
  "nivel": null, "nivel_sugerido": "medium",
  "sugestoes": [
    {"nivel": "medium", "fonte": "inventario",
     "motivo": "Oracle: migra, mas licença e edição mudam a conta e limitam o destino."},
    {"nivel": "high", "fonte": "dependencias",
     "motivo": "Banco acessado por outras aplicações: migram juntos, ou exigem janela conjunta ou replicação."}
  ],
  "dependencias": {
    "nivel": "high",
    "motivo": "Banco acessado por outras aplicações: migram juntos, ou exigem janela conjunta ou replicação.",
    "grupos": ["adb-producao + checkout (dev) + relatorios (dev) e mais 1"],
    "ondas": [2],
    "migra_junto_com": [
      {"servico": "COMPUTE", "custo": 346.99, "natureza": "dados"},
      {"servico": "BLOCK_STORAGE", "custo": 51.0, "natureza": "dados"}
    ],
    "depende_de": [],
    "dependentes": 2,
    "banco_compartilhado": true, "grupo_grande": false,
    "custo_do_grupo": 1397.93, "cobertura": 1.0,
    "regioes": ["sa-saopaulo-1"],
    "exclusividade": {
      "nivel": "medium",
      "motivo": "Oracle: migra, mas licença e edição mudam a conta e limitam o destino.",
      "fracao": 1.0,
      "recursos": ["ocid1.autonomousdatabase.oc1.sa-saopaulo-1.adbprod"]
    }
  },
  "alertas": ["Categoria não identificada: fica fora do score até ser classificada."]
}
```

O `nivel_sugerido` é o da exclusividade (Oracle, médio). O "migra junto" alto fica
listado, mas é dependência entre serviços, não da nuvem: não entra no nível (lote
v4, decisão de 14/10).

Um serviço só com hipótese (bill GCP de referência):

```jsonc
{
  "nome": "Kubernetes Engine", "tipo_inferido": true,
  "nivel": null, "fonte": null, "nivel_sugerido": "low",
  "sugestoes": [{
    "nivel": "low", "fonte": "classe",
    "motivo": "Herdado dos serviços curados do mesmo tipo (managed_kubernetes); fora do score até a curadoria confirmar."
  }],
  "alertas": ["Tipo de serviço inferido pelo classificador; a nota só vale depois de confirmado."]
}
```

### Campos

| campo | significado |
|---|---|
| `servicos[]` | um por serviço da fatura: os que o score técnico lê e o gasto sem categoria identificada. Ordenados por custo |
| `entra_no_score` | `false` para gasto sem categoria (`unknown`/`unclassified`): é listado porque é dinheiro real, mas o score não o lê, então `nivel` fica `null` e `por_destino` vazio |
| `nivel` | acoplamento do lado da origem: o ponto de onde o score parte. `null` = **a confirmar** (sem nota no catálogo, ou tipo inferido) |
| `fonte` | `catalogo` (metadado curado) ou `informado` (contrato direto, `/analyses/direct`) |
| `por_destino` | o nível que o eixo técnico do score usa em cada cenário: o pior entre origem e destino. `null` quando o destino não tem mapeamento único (ausente ou ambíguo), e o score trata como desconhecido |
| `equivalente_por_destino` | o serviço equivalente em cada cenário, o mesmo alvo que o score avalia. `null` sem mapeamento único |
| `refatoracao_por_destino` | se a migração para aquele destino exige refatorar a aplicação: `true` quando a origem ou o destino exige; `false` só quando os dois lados dizem que não; `null` quando falta um dos lados. É a evidência do fator `refactoring` do eixo técnico |
| `classe_migracao` / `esforco` | do catálogo: a classe 7R de partida (`rehost`, `replatform`, `refactor`) e o esforço. `null` fora do score |
| `sugestoes[]` | hipóteses que o score **não** usa, cada uma com `fonte` e `motivo`: `classe` (nota herdada do tipo de serviço, só quando os serviços curados do tipo concordam), `arquitetura` (pool sem família da mesma arquitetura em algum destino), `inventario` (exclusividade pelos atributos dos recursos: motor do banco, tipo nativo; qualquer nível, inclusive baixo), `dependencias` (o inventário mostra que migra junto: médio; banco compartilhado ou grupo grande: alto; dependência baixa não vira sugestão; fica listada, mas não entra no `nivel_sugerido`) |
| `dependencias` | só com inventário: o que **migra junto** (mesma unidade, ou dependência forte de dados ou encaminhamento), o que precisa ir **antes** (`depende_de`, dependência de ordem), quantos serviços dependem deste, grupo e onda de migração (`ondas` vazio = grupo independente), custo do grupo, `cobertura` (fração do custo do serviço com recurso no inventário), regiões e `exclusividade` (o maior nível que as regras deram aos recursos do serviço, com motivo, fração do custo e até 5 recursos; regras em `domain/services/topologia/exclusividade.py`). `null` sem inventário, ou quando nenhum recurso do serviço foi achado nele (billing sem `resource_id`, como o Cost Explorer agregado) |
| `nivel_sugerido` | o nível que a evidência além do catálogo daria, só quando difere do `nivel`. Inventário e classe pela regra do lote v4 ([`lock_in_efetivo.py`](../../app/domain/services/lock_in_efetivo.py)): o recurso proprietário sobe ou preenche; descer, ou afirmar baixo sem catálogo, exige 90% do custo do serviço visto no inventário e correlação por ID; a classe só vale sem catálogo e sem inventário. A arquitetura só sobe ou preenche. "Migra junto" não conta |
| `alertas[]` | também avisa quando os serviços curados do tipo discordam do lock-in ("de médio a alto"): a nota do serviço depende de curadoria |
| `pools[]` | os pools de compute, dentro do serviço de máquinas virtuais onde está o custo deles. `regiao: null` quando o billing não informa (antes aparecia como `global`) |
| `pools_sem_servico[]` | pools sem um serviço curado de máquinas virtuais para ancorar; não se inventa dono |
| `resumo` | os contadores da tela. `low + medium + high + a_confirmar = servicos`; `migram_em_grupo` (com `migra_junto_com`) e `fora_do_score` |
| `com_inventario` / `eixo_de_dependencia` | a análise usou inventário / e ligou recurso a serviço (análise com inventário feita antes desta versão tem o primeiro sem o segundo) |
| `reconciliacao` | `soma_linhas + fora_do_escopo` = soma dos serviços do payload; `diferenca` = `custo_atual` menos essa soma. `fora_do_escopo` é só o financeiro: imposto, crédito, desconto, compromisso, suporte e custo zero. `status`: `fecha` (± 1 centavo por serviço), `diferenca_de_escopo` ou `sem_custo_atual` |

### Para o front

- Contadores: de `resumo`. Nunca contar linhas de `cross_cloud_equivalents`.
- "Do total": `fracao`. A soma das linhas fica ≤ 100%; o resto é
  `fora_do_escopo`.
- Badge: `nivel`. Quando `nivel` é `null` e há `nivel_sugerido`, mostrar
  "A confirmar · provável <nível>" com o `motivo` no detalhe.
- Pools: expandir dentro da linha de máquinas virtuais; `regiao: null` →
  "região não informada".
- Com `dependencias`: mostrar "migra junto com …" e o custo do grupo ao lado
  do nível; `depende_de` como "depois de …".
- `entra_no_score: false`: badge "A confirmar" com a nota "categoria não
  identificada".
- Adaptação e equivalentes: de `refatoracao_por_destino` e
  `equivalente_por_destino`. Não casar `cross_cloud_equivalents` pelo nome: as
  máquinas virtuais aparecem lá por pool, e o serviço some.

## Garantias

- Teste de propriedade nos dois bills reais: `por_destino` e
  `refatoracao_por_destino` são iguais, serviço a serviço, às evidências
  `lock_in` e `refactoring` do eixo técnico do `decision_support`.
- `cross_cloud_equivalents`, `pricing_reconciliation` e `decision_support` não
  mudam com esta visão (teste de não regressão).
- Isolamento por usuário em todas as consultas; 404 esconde a existência.
