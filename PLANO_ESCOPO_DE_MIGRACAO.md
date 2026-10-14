# PLANO_ESCOPO_DE_MIGRACAO — migrar uma aplicação ou um serviço, com tudo que vai junto

> Escrito em 13/10/2026 para a **sprint de Terraform (14/10)**. Base visual
> aprovada: pasta `BaseMigracao-NAO-APAGUE/` na branch `docs/base-migracao` (mock
> com dados reais, prints e decisões; não apagar).
>
> ## Status — 13/10/2026
>
> | bloco | estado |
> |---|---|
> | **E1** — rotas `applications` e `migration-plans` | ✅ feito, contrato em [`docs/endpoints/migration-plans.md`](docs/endpoints/migration-plans.md) |
> | **E2** — custo no destino por escopo | ✅ feito: instância pela taxa do pool dela, disco/banco/bucket pela categoria, o resto declarado sem cotação |
> | **E3** — Terraform do escopo | ⬜ sprint de Terraform |
> | **E4** — pré-requisitos | ✅ em parte: sem tag, cada unidade vira "aplicação"; custo sem recurso fica em `sem_aplicacao`. Falta a mensagem própria para fatura resumida (Cost Explorer) |
> | **E5** — limite das dependências | ✅ `limite_das_dependencias` nas duas respostas |
> | **E6** — dependência entre serviços na mesma aplicação | ✅ `topologia/dependencias_de_servico.py`; não muda unidades, grupos, ondas nem Terraform |
> | **E7** — exportar | ✅ `?formato=pdf` (para circular: passos pré/principal/pós, ordem, pendências) e `?formato=xlsx` (para executar: cada recurso com status); persistência do plano ainda a decidir |
> | Front | ⬜ tela Dependências na develop do front (ver §4) |

## 1. O pedido

Poder dizer **"quero migrar só a aplicação A"** ou **"quero migrar o EKS e todas as
aplicações dele"** e receber o plano: o que vai junto obrigatoriamente, o que
precisa estar no destino antes, quem fica para trás dependendo do que sai, quanto
custa hoje e no destino, em que ordem, e o Terraform só desse escopo.

As duas perguntas de leitura que a tela responde:

- em quais serviços a aplicação A aparece;
- quais aplicações usam o serviço B.

## 2. O que o backend já tem

Tudo calculado hoje na análise com inventário (`domain/services/topologia/`):

| peça | onde | o que é |
|---|---|---|
| Unidade | `unidades.py` | recursos da mesma aplicação (tag `app`, `application`, `service`…), no mesmo ambiente, conta e região; anexos juntos |
| Dependência | `dependencias.py` | entre unidades, das regras de rede e declarações: **forte** (dados, encaminhamento: migram juntas), **fraca** (ordem), **nenhuma** (administrativo); com portas e evidência |
| Grupo de movimentação | `acoplamento.py` | unidades unidas pelo que é forte; ferramenta (bastion) e hub filtrados antes |
| Onda | `ondas.py` | ordem pelas dependências fracas entre grupos; grupo sem dependência fica livre |
| Custo por recurso | `PgBillingItemRepository.custos_por_recurso` + `IndiceDeApelidos` | fatura detalhada → recurso → unidade → aplicação × serviço |

Medido no mock com inventário (análise local `87c721a9`): 8 aplicações (mais o
bastion), 29 unidades, 24 grupos, 3 ondas, 46 dependências (5 fortes, 24 fracas,
17 administrativas). 16% do gasto (US$ 15,1 mil) não casa com nenhuma aplicação.

O que **não** existe: nada disso sai por aplicação. O `service-couplings` só
agrega por serviço (a lista de vizinhos é a união de todos os grupos do serviço).

## 3. Blocos

### E1. Duas rotas de leitura

1. `GET /v1/analyses/{job_id}/applications` — o grafo por aplicação:
   aplicações (ambientes, regiões, custo, serviços com custo, unidades, ondas,
   com quem migra junto), grupos, dependências entre unidades, custo sem
   aplicação por serviço, premissas (chave de aplicação detectada, limite das
   dependências).
2. `POST /v1/analyses/{job_id}/migration-plans` — recebe a escolha
   (`aplicacoes`, `servicos`, `ambiente`, `incluir_grupos`) e devolve o plano
   fechado:
   - `escopo`: unidades, aplicações, serviços, recursos;
   - `vao_junto`: unidade + motivo + evidência (dependência forte);
   - `antes`: grupos que o escopo chama (fraca, escopo → fora), com natureza,
     portas, onda e custo;
   - `para_tras`: grupos que chamam o escopo (fraca, fora → escopo); conversa
     nos dois sentidos aparece uma vez, em `antes`;
   - `custos`: hoje, no destino por provedor, método por linha, sem cotação;
   - `ondas` do escopo e `fora` (custo dos serviços escolhidos que não casa com
     aplicação).

Regras:

- **O cálculo fica no backend.** No mock o fechamento roda no navegador
  (`mock/script.js`, `scope()`); vira função pura em
  `domain/services/topologia/escopo.py`, com testes de propriedade.
- Só leitura: não muda score, preço, decisão nem o contrato do MCP. Isolamento
  por `user_id`, 404 para análise de outro usuário.
- Análise sem inventário responde `409 INVENTARIO_AUSENTE` (`retryable=false`):
  a tela mostra o cadeado e o upload.
- A topologia é recalculada do inventário (determinística) e o `fingerprint`
  tem que bater com o congelado no payload da análise; se não bater, 409.
- Casos de teste a partir do mock: `checkout` (prod) puxa auth e pagamentos
  (banco compartilhado, tcp/5432), precisa do catalogo antes (tcp/8080) e deixa
  o monitoramento para trás (tcp/5432); EKS entra em 4 aplicações e puxa auth e
  pagamentos.

### E2. Custo no destino por escopo

O compute é cotado por família e região, não por aplicação. O mock aplica a
cotação de cada serviço na proporção da parte dele no escopo; banco e
armazenamento de objetos usam a razão de preço da categoria (banco na OCI: 30
contra 62); o resto aparece como "sem cotação".

O certo: cotar **as instâncias do próprio escopo**, que o inventário lista uma a
uma (tipo de máquina por recurso), com o mesmo empacotador pela capacidade-hora
([`PLANO_EMPACOTAMENTO.md`](PLANO_EMPACOTAMENTO.md)). Categorias pela razão de
taxa, com o método em cada linha. O que não tem cotação fica fora da comparação,
declarado.

### E3. Terraform do escopo

Hoje o gerador sempre gera a análise inteira. Passa a aceitar o plano:

- só as unidades do escopo, mais a fundação de rede que elas usam (VPC,
  subredes, grupos de segurança);
- na ordem das ondas;
- o README do pacote lista a conectividade entre nuvens a prever (`antes` e
  `para_tras` que não entraram).

Cruza com os itens já marcados para a sprint: aviso de empacotamento (mostrar
quantas máquinas e que fração delas o uso ocupa), isolamento entre subredes na
Azure, banco fora da AWS sem subredes espelhadas, disco fantasma do Filestore e
a decisão 23 (o envio do agente pode gerar Terraform?).

### E4. Pré-requisitos e degradação

- **Tag de aplicação:** sem ela (cobertura abaixo de 50%) não existe
  "aplicação". A rota devolve unidades e grupos pela rede, com aviso, e a tela
  sugere declarar a chave (`declaracoes.chave_aplicacao`).
- **Fatura detalhada com ID de recurso:** com a fatura resumida do Cost
  Explorer não dá para dividir o custo por aplicação. A rota devolve o grafo
  sem custo por aplicação, com o motivo.
- **Sem aplicação:** o custo que não casa (16% no mock) aparece separado, nunca
  rateado.

### E5. Limite das dependências

Elas mostram o que a configuração de rede permite, não o tráfego real. A
premissa vai nas duas respostas e a tela avisa.

### E6. Dependência entre serviços dentro da aplicação (achado do mock)

Marcar o **EC2** não avisa que ele depende do **RDS**. A dependência só existe
entre unidades: dentro da mesma unidade (a VM e o banco da mesma aplicação), a
aresta é descartada (`dependencias.py`, `registrar`: `if u == v: return`).

Correção proposta, sem mudar unidades, grupos nem ondas (logo sem mudar o
Terraform nem a topologia congelada):

- guardar os pares internos à unidade como **dependências internas**, mapeadas
  para pares de serviço (`AmazonEC2 → AmazonRDS`, natureza, portas);
- no plano: "Ao escolher EC2, entram também: AmazonRDS (as VMs acessam o banco,
  tcp/5432)";
- no detalhe do serviço: "Depende de" e "É usado por", entre serviços.

A decidir: oferecer o modo **"só o serviço"** (só os recursos daquele serviço),
em que o RDS que fica vira `antes`/acesso entre nuvens. Recomendação: começar com
aplicações inteiras (o padrão do mock) e deixar o modo parcial para depois.

### E7. Exportar e persistência (decisões)

- **Exportar** o plano: aprovado como ideia; falta o formato (PDF, planilha) e o
  conteúdo.
- **Salvar o plano** (para reabrir, exportar e gerar o Terraform): decidir se
  vira entidade (`migration_plans`, com migration) ou se o POST basta.

## 4. Front (próxima sprint, depois das rotas)

Do mock aprovado (`BaseMigracao-NAO-APAGUE/README.md`, decisões 1 a 8):

- aba e coluna renomeadas para **Dependências**;
- a coluna de onda se chama **"Ordem de migração"**, com ícone de informação: "Em que
  etapa a aplicação migra. A 1 vai primeiro; quem depende dela vem depois. Livre:
  não depende de ninguém." (pedido do usuário: "onda" sozinho não diz nada);
- bloqueio do que vem do inventário, com upload pelo próprio cadeado;
- processamento sem travar a tela, status "Processando inventário" e avisos no
  sino. O `AnalysisWatcher` hoje só acompanha análises: precisa acompanhar
  inventários também;
- visões Serviços e Aplicações e o painel de plano lendo as rotas do E1.

Ponto do fluxo atual: anexar inventário a uma análise pronta cria **outra
análise** do mesmo billing (`POST /v1/analyses/start/{report_id}` com
`inventory_id`), o que roda o LLM de novo e deixa a análise antiga na lista.
Funciona para a primeira versão; se o custo pesar, o backend pode completar a
análise existente com o inventário.

## 5. Ordem

1. E6 (dependências internas) e a função pura do E1, com testes nos mocks com
   inventário.
2. As duas rotas do E1, com E4 e E5.
3. E2 (custo do escopo pelas instâncias).
4. E3 (Terraform do escopo), junto com os itens já marcados da sprint.
5. Front.
