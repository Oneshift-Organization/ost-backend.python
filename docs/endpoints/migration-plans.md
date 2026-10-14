# Aplicações e plano de migração

`GET /v1/analyses/{job_id}/applications` · `POST /v1/analyses/{job_id}/migration-plans`

Fonte da tela **Dependências** quando a análise tem inventário: a visão por
aplicação, a coluna "Aplicações" da visão por serviço e o painel **Planejar
migração**. Plano e motivação: [`PLANO_ESCOPO_DE_MIGRACAO.md`](../../PLANO_ESCOPO_DE_MIGRACAO.md).

Só leitura: não grava, não chama LLM, não muda score, preço nem o contrato do
agente. O inventário da análise é lido de novo e a topologia recalculada; o
`fingerprint` tem que bater com o congelado no payload.

## Vocabulário

| termo | o que é |
|---|---|
| **aplicação** | o valor da tag de aplicação (`chave_aplicacao`, detectada: `app`, `application`, `service`…). Sem tag, cada unidade conta como uma |
| **unidade** | recursos da mesma aplicação, no mesmo ambiente, conta e região |
| **grupo de migração** | unidades ligadas por dependência forte (banco compartilhado, encaminhamento): migram juntas |
| **ordem** | a onda da topologia: `1` vai primeiro, quem depende dela vem depois; `null` não depende de ninguém ("livre"). Na tela: coluna **"Ordem de migração"** com ícone de informação |
| **natureza** | `dados` (acessa banco/cache), `servico` (chama uma porta de aplicação), `encaminhamento` (balanceador) |
| **ferramenta** | acesso (bastion): fora das ondas, do catálogo e do plano |

## `GET …/applications`

```jsonc
{
  "versao": "escopo-de-migracao-v1",
  "moeda": "USD",
  "provedor_origem": "aws",
  "provedor_recomendado": "oci",
  "inventario_id": "…",
  "chave_aplicacao": "app",
  "aplicacoes": [{
    "nome": "checkout", "ambientes": ["data", "prod"], "regioes": ["us-east-1"],
    "custo": 5521.18, "recursos": 48,
    "ordens": [2],                       // vazio = livre
    "migra_junto_com": ["auth", "pagamentos"],
    "servicos": [{ "nome": "AmazonRDS", "custo": 2971.4 }, …]
  }],
  "servicos": [{
    "nome": "AmazonEC2",
    "aplicacoes": [{ "nome": "pagamentos", "custo": 4239.46 }, …],
    "sem_aplicacao": 7476.3,             // custo que não casa com aplicação
    "depende_de": [{ "servico": "AmazonRDS", "natureza": "dados", "portas": ["tcp/5432"] }],
    "usado_por":  [{ "servico": "Amazon Elastic Load Balancing", "natureza": "encaminhamento", "portas": ["tcp/8080"] }]
  }],
  "grupos": [{ "id": "…", "nome": "auth (prod) · us-east-1 + checkout (prod) + pagamentos (prod)", "ordem": 2, "aplicacoes": […], "custo": 28513.56 }],
  "ferramentas": ["bastion"],
  "sem_aplicacao": 15089.75,
  "limite_das_dependencias": "Dependências vêm das regras de rede …",
  "premissas": [{ "campo": "chave_aplicacao", "valor": "app", "base": "…" }, { "campo": "ordem", "valor": null, "base": "…" }]
}
```

`depende_de` / `usado_por` vêm das regras de rede **no nível do recurso**,
inclusive dentro da mesma aplicação: é o que faz o EC2 mostrar que acessa o RDS.

## `POST …/migration-plans`

Corpo:

```json
{ "aplicacoes": ["checkout"], "servicos": [], "ambiente": "prod", "incluir_grupos": [] }
```

- `aplicacoes` e/ou `servicos`: ao menos um. Escolher um serviço leva as
  **aplicações inteiras** que o usam (uma VM sem o banco da própria aplicação não
  funciona no destino).
- `ambiente`: `null` = todos.
- `incluir_grupos`: ids de grupos de `antes`/`para_tras` que o usuário incluiu
  (botão **Incluir**).

Resposta (campos principais):

| campo | o que a tela mostra |
|---|---|
| `aplicacoes`, `recursos`, `custo` | resumo do escopo (custo hoje, moeda da análise) |
| `destino[]` | por provedor: `custo_estimado`, `custo_origem_cotado` (a parte com cotação), `sem_cotacao`, `variacao` |
| `servicos[]` | serviço, custo hoje, `destino` por provedor (`null` = sem cotação) e `metodos` |
| `vao_junto[]` | **Vão junto obrigatoriamente**: unidade, aplicação, custo, `motivo` pronto ("Compartilha banco com checkout (prod) (tcp/5432)"), `incluida_por_voce` |
| `servicos_que_vem_junto[]` | só quando a escolha tem serviço: o que entrou e por quê ("AmazonEC2 acessa dados em AmazonRDS (tcp/5432)") |
| `antes[]` | **Precisam estar no destino antes**: grupos que o escopo chama, com `naturezas`, `portas`, `ordem`, `custo`; `nos_dois_sentidos` quando também chamam o escopo |
| `para_tras[]` | **Ficam para trás e dependem do que sai**: grupos que chamam o escopo |
| `ligacoes[]` | como os serviços do escopo se chamam |
| `ordens[]` | grupos do escopo por ordem de migração |
| `fora_do_plano[]` | custo dos serviços escolhidos que não casa com aplicação |

### Custo no destino

Cada recurso do escopo é cotado pelo que é, com o preço que a própria análise usou:

| método | quando |
|---|---|
| `pool` | instância: taxa do pool da família e região dela (destino ÷ origem do pool) |
| `pool_da_familia` | instância sem pool na região: média da família |
| `media_dos_pools` | instância sem a família nos pools: média de todos |
| `categoria` | disco (SSD/HDD), banco, cache e bucket: razão de preço da categoria |
| *(vazio)* | balanceador, cluster gerenciado, filas…: sem cotação, fora da comparação |

### Exportar

`POST …/migration-plans?formato=xlsx` devolve o mesmo plano em planilha
(`Content-Disposition: attachment`), com as abas Resumo, Vão junto, Antes, Para
trás, Serviços, Ligações e Ordem.

## Fatura e inventário: a correspondência

As duas respostas trazem `correspondencia`, que diz se o inventário casa com a fatura:

| `status` | quando | o que muda |
|---|---|---|
| `ok` | o inventário cobre ao menos 80% do custo com ID de recurso | nada |
| `parcial` | cobre menos; `contas_sem_inventario` diz o que falta | a tela avisa; custo e plano só da parte coberta |
| `sem_id_de_recurso` | fatura resumida (Cost Explorer, Cost Table): nenhuma linha traz o ID do recurso | `liga_custo=false`: aplicações e o que migra junto vêm só do inventário, sem custo; plano só por aplicação (por serviço dá `422 ESCOLHA_INVALIDA`) |

Inventário de **outra conta** não chega aqui: o `POST /v1/analyses/start` recusa com
`422 INVENTARIO_NAO_CORRESPONDE` antes de criar o job. Na AWS e na Azure, contas sem
nenhuma em comum já bastam (nome coincidente de bucket ou banco não é o mesmo recurso);
no GCP e na OCI a fatura traz outra noção de conta (conta de faturamento, tenant), então
vale só "nenhum recurso casou".

## Erros

Envelope `{codigo, mensagem, retryable}`.

| HTTP | código | quando |
|---|---|---|
| 404 | `INVENTARIO_DESLIGADO` | `INVENTORY_ENABLED=false` no ambiente |
| 404 | `ANALISE_NAO_ENCONTRADA` | não existe ou é de outro usuário |
| 409 | `ANALISE_NAO_CONCLUIDA` | ainda rodando (`retryable=true`) |
| 409 | `ANALISE_FALHOU` | terminou com falha |
| 409 | `INVENTARIO_AUSENTE` | análise sem inventário: a tela mostra o bloqueio e o upload |
| 409 | `INVENTARIO_NAO_PROCESSADO` | o inventário da análise não está `done` (`retryable=true`) |
| 409 | `TOPOLOGIA_DIVERGENTE` | o inventário mudou desde a análise |
| 409 | `INVENTARIO_NAO_CORRESPONDE` | análise feita antes da validação com inventário de outra conta; vem com `contas_da_fatura` e `contas_do_inventario` |
| 422 | `ESCOLHA_INVALIDA` | escolha vazia, ou com aplicação, serviço ou grupo que a análise não tem |

## Limites

- Dependências mostram o que a configuração de rede permite, não o tráfego real.
- Custo por aplicação precisa de fatura detalhada com ID de recurso (CUR,
  BigQuery, Azure, OCI). O que não casa fica em `sem_aplicacao`, nunca rateado.
- Sem tag de aplicação em pelo menos metade dos recursos, cada unidade vira uma
  "aplicação" com o nome da unidade.
