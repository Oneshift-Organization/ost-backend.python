# BaseMigracao — NÃO APAGUE

Base aprovada em 13/10/2026 para a próxima sprint: a tela **Dependências**
(antiga "Acoplamento") e o **plano de migração por aplicação ou serviço**. É um
mock em HTML com os dados reais de uma análise local, não código de produto.
O que o backend precisa para ele virar produto está em
[`PLANO_ESCOPO_DE_MIGRACAO.md`](../PLANO_ESCOPO_DE_MIGRACAO.md).

## Como abrir

```bash
python3 -m http.server 8799 --bind 127.0.0.1 --directory BaseMigracao-NAO-APAGUE/mock
# http://127.0.0.1:8799/dependencias.html
```

Abrir o arquivo direto no navegador também funciona. Precisa de internet para a
fonte (Lexend) e os ícones (lucide).

Parâmetros úteis na URL:

| parâmetro | efeito |
|---|---|
| `theme=light` / `theme=dark` | tema |
| `inventario=1` | começa com o inventário já processado |
| `visao=aplicacao` | começa na visão por aplicação |
| `apps=checkout` / `servicos=AmazonEC2` | abre o plano com essa escolha |
| `servico=AmazonRDS` | abre o detalhe desse serviço |
| `painel=0` | sem painel aberto |
| `rapido=1` | processamento do inventário 4x mais rápido |

## O que foi decidido com o usuário

1. **Nome:** a aba e a coluna viram **Dependências** / **Dependência**, com
   níveis no feminino (Baixa, Média, Alta, A confirmar).
2. **Layout:** o mesmo padrão das outras abas (título, números do topo, busca,
   tabela, paginação) e a identidade do front (tokens de `app/globals.css`,
   Lexend). O detalhe abre num **painel fixo à direita, sem escurecer a página**;
   a lista se ajusta e continua clicável.
3. **Sem inventário:** tudo que vem do inventário fica sob uma **camada opaca
   com cadeado** e o aviso de que é preciso importar o inventário. Clicar abre
   o **modal de upload** (o mesmo de `inventory-upload-modal.tsx`, com a nuvem
   presa na da fatura).
4. **Coluna bloqueada:** etiqueta "Inventário" em cada linha e **cadeado com
   explicação no título da coluna**. A camada sobre a coluna inteira foi
   recusada.
5. **Processando:** a tela **não trava**. Os cadeados viram carregamento, o
   status da análise passa a **"Processando inventário"**, uma faixa mostra as
   duas etapas (ler o inventário, refazer a análise) e há **dois avisos** (toast
   e sino): "Inventário processado" e "Análise atualizada com o inventário".
6. **Duas visões:** **Serviços** (quais aplicações usam cada serviço, coluna
   "Aplicações") e **Aplicações** (o que cada aplicação usa, ambientes, onda,
   com quem migra junto).
7. **Planejar migração:** marcar aplicações e/ou serviços, filtrar por
   ambiente, e ver: custo hoje e estimado no destino; o que **vai junto
   obrigatoriamente** (com o motivo); o que **precisa estar no destino antes**;
   quem **fica para trás dependendo do que sai**; serviços do escopo; ordem por
   ondas. Os dois últimos grupos têm botão **Incluir**.
8. **Exportar** o plano: ideia aprovada, ainda sem formato definido.

## Pendências registradas

- **EC2 marcado não avisa que depende do RDS** (print 08). A topologia só guarda
  dependência *entre unidades*; dentro da mesma unidade (a VM e o banco da
  mesma aplicação) a aresta é descartada (`dependencias.py`, `registrar`:
  `if u == v: return`). Ao escolher um serviço, o plano leva as aplicações
  inteiras (com o RDS delas), mas não diz isso. Ver o plano, item E6.
- Os itens E1 a E5 do plano (rotas, custo por escopo, Terraform do escopo,
  pré-requisitos, limite das dependências).
- Decidir se o plano de migração fica salvo (para reabrir, exportar e gerar o
  Terraform).
- Exportar: formato (PDF, planilha) e conteúdo.

## Arquivos

| pasta | o quê |
|---|---|
| `mock/dependencias.html` | o mock pronto (abre sozinho) |
| `mock/template.html`, `mock/script.js` | o HTML/CSS e o script; o `build.py` junta os dois com os dados |
| `mock/build.py` | monta o `dependencias.html` a partir de `dados/` e dos logos do front |
| `mock/extrair_escopo.py` | extrai do cálculo real (`analisar_topologia` + custo por recurso) o grafo por aplicação |
| `dados/` | `service-couplings` e recortes do `/result` das análises locais; `escopo.json` é a saída do extrator |
| `prints/` | os estados principais |

Dados: análise local `87c721a9` (mock CUR AWS + inventário mock AWS Config,
gerados por `scripts/gerar_mocks_billing.py` e `scripts/gerar_mocks_inventario.py`).
Nenhum dado de cliente.

Para refazer o `escopo.json` com outra análise:

```bash
PYTHONPATH=. .venv/bin/python BaseMigracao-NAO-APAGUE/mock/extrair_escopo.py \
  <inventario_id> <report_id> <user_id> BaseMigracao-NAO-APAGUE/dados/escopo.json
cd BaseMigracao-NAO-APAGUE/mock && python3 build.py
```
