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
- `somente_os_servicos` (padrão `false`, 18/10): `true` migra **só os serviços
  escolhidos**, nas unidades que os usam, sem o resto das aplicações nem os grupos.
  Não combina com `aplicacoes` nem com `incluir_grupos` (422 com o motivo). Cada
  dependência que o recorte corta volta em `fronteira`.

As três escolhas da tela:

| a tela escolhe | corpo |
|---|---|
| tudo | sem plano (o Terraform sem `escopo`) |
| uma aplicação (ou várias) | `{"aplicacoes": ["checkout"]}`: a aplicação com o que migra junto |
| um serviço sozinho | `{"servicos": ["AmazonRDS"], "somente_os_servicos": true}` |

`fronteira[]` (só com `somente_os_servicos`): uma entrada por dependência cortada.

```json
{ "sentido": "entra", "de": "AmazonEC2 (checkout)", "para": "AmazonRDS (checkout)",
  "natureza": "dados", "portas": ["tcp/5432"], "forte": true,
  "texto": "AmazonEC2 (checkout) fica na origem e acessa dados em AmazonRDS (checkout), que migra (tcp/5432): o endereço de AmazonRDS (checkout) muda, e sem conexão privada entre as nuvens ela para. Troque o endereço na configuração de AmazonEC2 (checkout) e garanta que o destino aceite a entrada a partir da rede de origem." }
```

- `sentido`: `sai` (o que migra chama o que fica) ou `entra` (o que fica chama o que
  migra). `forte`: dados ou encaminhamento, que param a aplicação sem conexão
  privada entre as nuvens. `texto`: pronto para exibir; os fortes vêm primeiro.
- A dependência vem das regras de rede do inventário, no nível do recurso, também
  para recurso sem linha própria na fatura (o serviço pelo tipo: a VM do
  monitoramento que lê o banco).

Resposta (campos principais):

| campo | o que a tela mostra |
|---|---|
| `aplicacoes`, `recursos`, `custo` | resumo do escopo (custo hoje, moeda da análise) |
| `destino[]` | por provedor: `custo_estimado`, `custo_origem_cotado` (a parte com cotação), `sem_cotacao`, `variacao` |
| `servicos[]` | serviço, custo hoje, `destino` por provedor (`null` = sem cotação), `metodos` e `sem_cotacao` (provedor → motivo pronto para exibir, ex. "a análise ainda não cota balanceador de carga") |
| `vao_junto[]` | **Vão junto obrigatoriamente**: unidade, aplicação, custo, `motivo` pronto ("Compartilha banco com checkout (prod) (tcp/5432)"), `incluida_por_voce` |
| `servicos_que_vem_junto[]` | só quando a escolha tem serviço: o que entrou e por quê ("AmazonEC2 acessa dados em AmazonRDS (tcp/5432)") |
| `antes[]` | **Precisam estar no destino antes**: grupos que o escopo chama, com `naturezas`, `portas`, `ordem`, `custo`, `ligado_a` (os grupos do plano com que conversa); `nos_dois_sentidos` quando também chamam o escopo |
| `para_tras[]` | **Podem vir depois**: grupos que usam o que vai migrar (chamam o escopo); incluídos, vão para a pós-migração |
| `incluidos[]` | os grupos de `incluir_grupos`, cada um com `lado`: `antes` = **pré-migração** (o plano o chama), `depois` = **pós-migração** (ele chama o plano), `sem_ligacao` (o que o ligava ao plano saiu). Mais `ordem`, `custo`, `aplicacoes`, `ligado_a` (pré: quem do plano o chama; pós: quem do plano ele chama), `naturezas`, `portas`. O lado sai do sentido das ligações, não de onde o usuário clicou: o mesmo pedido dá o mesmo plano |
| `grupos_do_plano[]` | cada grupo do plano com `bloco` (`pre`, `principal`, `pos`), `ordem` (etapa da conta) e `custo`: a tela monta os **passos** (um por bloco e etapa) daqui |
| `unidades_do_plano[]` | o que é cada unidade: `nome`, `aplicacao`, `ambiente`, `regiao`, `conta`, `grupo`, `papel` (`escolhido`, `vai_junto`, `incluido`), `motivo` (por que vai junto), `custo`, `conteudo` (quantos recursos de cada tipo), os 10 `recursos` mais caros e `recursos_total` (a planilha tem todos) |
| `etapas_da_conta` | quantas etapas a ordem de migração da conta tem: a tela mostra onde o plano cai e o que ficou de fora em cada uma |
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

Dois arquivos, para dois públicos, montados da mesma leitura
(`app/infrastructure/exports/plano_documento.py`) e na ordem em que a tela mostra:

- `?formato=pdf`: **para circular entre pessoas.** Resumo (custo hoje, estimado no
  destino recomendado, destinos), o plano em **passos** numerados do começo ao fim
  (pré-migração, principal e pós-migração; dentro de cada bloco, um passo por
  etapa da conta), a ordem de migração da conta com o que ficou de fora em cada
  etapa (e por que o plano começa depois da 1ª, quando começa), o que está fora
  do plano e o que fazer, custo por serviço com o motivo de cada "sem cotação",
  ligações entre serviços e, no anexo, o que migra por unidade e as premissas.
- `?formato=xlsx`: **para quem executa.** Abas Resumo, Plano (passo, bloco,
  etapa, papel, por quê), Recursos (cada recurso a migrar, com identificador e
  as colunas Status, Responsável e Observações para a equipe preencher), Fora do
  plano (com a coluna Decisão), Ordem, Serviços e Ligações.

Os dois vêm com `Content-Disposition: attachment` e nome
`plano-de-migracao-<escolha>-<AAAAMMDD-HHMM>.<pdf|xlsx>`.

### Terraform do escopo

`POST /v1/terraform-generations` aceita o campo opcional `escopo`, com a mesma
escolha do `POST …/migration-plans`. Sem ele, o pacote é da análise inteira, como
sempre foi.

```json
{
  "analysis_job_id": "…",
  "target_provider": "gcp",
  "region": "us-central1",
  "network": {"mode": "create", "cidr": "10.50.0.0/16"},
  "escopo": {"aplicacoes": ["checkout"], "ambiente": "prod", "incluir_grupos": []}
}
```

`escopo` aceita `aplicacoes`, `servicos`, `ambiente`, `incluir_grupos` e
`somente_os_servicos`; campo desconhecido é 422. O plano é fechado pelas mesmas regras da tela, e o payload da
análise é recortado antes de derivar o pacote
(`app/domain/services/terraform_topologia/escopo.py`):

| parte | no pacote do escopo |
|---|---|
| compute | só os pools com máquina no escopo, na proporção dela: vCPUs das fatias de subrede do escopo sobre as do pool. Pool sem posicionamento (fatura sem ID de recurso): máquinas do escopo sobre as da conta, na mesma família e região, pelo inventário |
| subredes e grupos | os que as máquinas e os bancos do escopo usam (o espelho já cria só esses) |
| bancos | os do escopo, pela classe |
| discos, buckets, banco estimado | pelo custo das linhas do escopo na fatura; o armazenamento medido do inventário é da conta e sai |
| rede, segredos, serverless… | saem: o pacote não os provisiona |

O README do pacote ganha a seção **Escopo deste pacote**: aplicações, ordem de
migração, **conectividade entre nuvens a prever** (os grupos que vão `antes` e os
que ficam `para_tras`, com ordem e portas) e os pools da análise que não entraram.

Bancos do escopo (corrigido em 18/10): o recorte mantém os grupos de banco medidos
na fatura, na quantidade do escopo. Antes ele os tirava junto com o armazenamento
medido, e o pacote caía num banco genérico, fora da rede nova e com endpoint
público.

Com `"somente_os_servicos": true` no `escopo`, o pacote tem só os recursos dos
serviços escolhidos, e a fronteira volta de três jeitos:

- `warnings` da geração, **no topo**: o resumo (quantas dependências atravessam,
  quantas fortes), o que liberar na origem a partir da rede do destino, o aviso do
  banco fora da AWS (só a AWS anexa ao banco o grupo espelhado: nas outras nuvens o
  banco fica na subrede de dados, e a porta precisa ser liberada a partir da rede de
  origem) e as primeiras oito ligações;
- `unsupported_items`: serviço escolhido que o pacote não cria (balanceador, cache,
  Kubernetes: o Terraform cria máquina, disco, banco e bucket);
- README, seção **O que fica do outro lado (fronteira)**: todas as ligações, as
  fortes marcadas.

Erros: os mesmos da tabela [Erros](#erros) (o escopo usa o inventário), mais os da
geração. `INVENTARIO_DESLIGADO` também quando `INVENTORY_ENABLED=false`.

## Fatura e inventário: a correspondência

As duas respostas trazem `correspondencia`, que diz se o inventário casa com a fatura:

| `status` | quando | o que muda |
|---|---|---|
| `ok` | o inventário cobre ao menos 80% do custo com ID de recurso | nada |
| `parcial` | cobre menos; `contas_sem_inventario` diz o que falta | a tela avisa; custo e plano só da parte coberta |
| `estimado` | a fatura não traz o ID (formato detalhado legado da AWS, Cost Table do GCP, CUR sem IDs), mas traz o tipo de uso, e o custo foi ligado aos recursos do inventário pelo tipo | `liga_custo=true`; `fracao_estimada` diz quanto do gasto foi ligado. A tela marca o custo como estimado |
| `sem_id_de_recurso` | a fatura não traz o ID e nada dela pôde ser ligado pelo tipo | `liga_custo=false`: aplicações e o que migra junto vêm só do inventário, sem custo |

**Custo pelo tipo** (`app/domain/services/atribuicao_por_tipo.py`): cada linha sem ID
vai para os recursos do inventário do mesmo tipo — máquina pelo tipo
(`BoxUsage:t4g.xlarge`), banco pela classe, cluster, NAT, balanceador, disco pelo tipo
e pelo tamanho, bucket quando é um só —, na mesma conta e região. No GCP (Cost Table, export
padrão do BigQuery) a descrição do SKU decide: `N2 Instance Core`/`Ram` vai às máquinas da
família `n2-`, dividido pelos vCPUs e pela memória de cada uma; `Balanced PD Capacity` aos
discos `pd-balanced` pelo tamanho; `Cloud SQL for PostgreSQL` ao banco Postgres. Vários candidatos
iguais dividem igual; candidatos diferentes sem medida (vários buckets) não recebem.
A mesma regra cobre a máquina ou o disco com ID que não está no inventário (nó trocado
pelo autoscaling): conta como coberto pelo tipo, e a mensagem do `ok`/`parcial` diz
quanto. Tudo que entra assim sai como estimado; o medido não muda.

**Plano por serviço sem ID:** escolher um serviço traz as unidades que têm custo dele
ou que têm recurso do tipo que o gera (bucket → `AmazonS3`, banco → `AmazonRDS`),
mesmo quando o custo não pôde ser dividido. Não há mais `422` por fatura sem ID.

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
