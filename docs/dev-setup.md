# Dev setup

Como rodar o backend localmente. Sem cloud, sem CI, sem Supabase real.

> **Audiência:** dev novo no time. Para regras técnicas, ver
> [`architecture.md`](architecture.md). Para o que está implementado, ver
> [`endpoints/billing.md`](endpoints/billing.md).

## Pré-requisitos

- Python **3.12+** (suportado em `pyproject.toml`). Venv atual roda
  **3.14.4** — funciona; o `mypy` é configurado para `python_version = "3.12"`
  (compatibilidade alvo).
- **podman** (ou docker — comandos equivalentes).

## 1. venv + dependências

```bash
cd ost-backend.python
python3.14 -m venv .venv     # ou python3.12 — ambos OK
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

> Se preferir 3.12 (alinha com `mypy.python_version`):
> ```bash
> python3.12 -m venv .venv
> ```

## 2. Postgres local

```bash
podman run -d --name ost-pg \
  -p 5432:5432 \
  -e POSTGRES_USER=ost \
  -e POSTGRES_PASSWORD=ost \
  -e POSTGRES_DB=ost_dev \
  docker.io/library/postgres:16-alpine

# Verificar
podman exec ost-pg pg_isready -U ost
```

Se já existe e parou: `podman start ost-pg`.

## 3. Configurar `.env.dev`

Arquivo já existe na raiz do `ost-backend.python/`. Garantir:

```bash
APP_ENV=development
AUTH_ENABLED=false   # bypass de JWT — todo request vira usuário 00000000-0000-0000-0000-000000000001
DATABASE_URL=postgresql+asyncpg://ost:ost@127.0.0.1:5432/ost_dev
```

`AUTH_ENABLED=false` é **hard-blocked em produção** pelo `get_current_user` — não dá pra deixar
ligado por engano no prod (retorna 500 se `APP_ENV=production`).

Para análise de migração com IA real (em vez do mock), adicionar no `.env.dev`:
```bash
GEMINI_API_KEY=AIzaSy...
```
Sem a chave o sistema usa `MockLLMClient` — não faz nenhuma requisição externa.

## 4. Aplicar migrations

```bash
.venv/bin/alembic upgrade head
```

Cria `billing_reports` + `billing_items` com `NUMERIC(18,6)`, índices e FK CASCADE,
mais as tabelas de referência (`cloud_sku_catalog`, `cloud_services_comparation`,
`exchange_rates`, `cloud_price_points`).

## 4.1 Seeds de referência — **a ordem importa**

Sem eles a análise roda mas compara contra o vazio.

```bash
python -m app.infrastructure.database.seed_refactor
python -m app.infrastructure.database.seed_service_metadata
python -m app.infrastructure.database.seed_catalog_pareto
python -m app.infrastructure.database.seed_classification_rules
python -m app.infrastructure.database.seed_from_csv    # <- POR ÚLTIMO

# Taxas de referência por categoria não-compute. NÃO é opcional.
PYTHONPATH=. python scripts/sync_prices.py --providers categories
```

> ⚠️ **As taxas de categoria faltavam nesta lista.** Sem elas, storage, egresso
> e banco não são reprecificados no destino e `cobertura_analise` cai de ~0,80
> para **0,17** — sem erro nenhum, só um número pior. Descoberto em 13/09/2026
> subindo um ambiente do zero seguindo este documento ao pé da letra: três gates
> de integração falharam e a causa não era código.
>
> Elas vivem em `cloud_price_points` (não numa tabela própria), por isso entram
> pelo `sync_prices.py` e não por um módulo `seed_*`. É a razão de terem passado
> despercebidas aqui.

> ⚠️ **`seed_from_csv` tem que ser o último.** Ele carrega
> `cloud_services_mapping.csv` (816 linhas, a fonte autoritativa do DE-PARA, com
> tipos finos como `managed_relational_database`) e **limpa e recarrega** a tabela.
> O `seed_service_metadata` também escreve em `cloud_services_comparation`, mas a
> partir de uma lista inline de 120 tuplas com tipos grosseiros (`relational`).
> Na ordem errada, um ambiente novo — **inclusive produção** — fica com o tipo
> grosseiro e o DE-PARA por serviço perde precisão. Isso foi descoberto pelos
> gates de integração, não em revisão de código.

> ⚠️ **Regra ou taxa mudou? Rode de novo e re-enriqueça.** A classificação é
> gravada em `billing_enriched` na ingestão: re-seedar muda só os reports novos.
> Para os existentes, `python scripts/re_enrich_report.py <report_id>`. Foi o
> caso da correção do Balanced PD do GCP (SSD, não HDD) e da taxa de SSD do GCP
> (pd-balanced, não pd-ssd).

## 4.2 Preços oficiais (opcional, precisa de rede)

```bash
PYTHONPATH=. python -u scripts/sync_prices.py --providers aws,gcp,oci,categories-live
PYTHONPATH=. python -u scripts/sync_prices.py --providers azure   # o mais lento
```

Popula `cloud_price_points` das APIs públicas (AWS, GCP Catalog, Azure Retail,
OCI cetools), em cada região das zonas pedidas. Padrão: `virginia`,
`meio-oeste`, `sao-paulo`, `texas`, `california`; `--zonas todas` para as 11.
Roda **offline**, nunca no caminho crítico. O GCP exige `GCP_BILLING_API_KEY`;
sem ela sai vazio e os outros seguem. Cada provedor grava num commit próprio:
uma API fora do ar não descarta as outras.

Ao fim de cada sync, o ciclo de vida dos preços é recalculado: estado (vivo,
maduro, em decomposição, adubo) e score de cada ponto. O decaimento anda com o
relógio, então em ambiente de verdade isto também roda agendado, uma vez por dia:

```bash
PYTHONPATH=. python scripts/recalcular_ciclo_de_vida.py
python - < scripts/recalcular_ciclo_de_vida.py     # no pod
```

As regras (meia-vida, limiares, peso de cada fonte) estão nas tabelas
`price_lifecycle_rules` e `price_source_weights`, criadas pela migração
`c3d4e5f6a7b8`. Ver [`PLANO_CICLO_DE_VIDA_DO_PRECO.md`](../PLANO_CICLO_DE_VIDA_DO_PRECO.md).

Sem isso a análise funciona, mas os equivalentes ficam com `confianca_preco:
low` (catálogo seed). Com o sync de uma zona só, uma carga de outra zona é
cotada com preço de fora e sai `reference`, com o aviso "Preço de outra região
na cotação".

> ⚠️ A Retail API da Azure limita requisições (429) e derruba conexões. O
> cliente espera e tenta de novo, mas cinco regiões levam de 10 a 30 minutos.
> Rode a Azure separada.

## 5. Subir a API

```bash
.venv/bin/uvicorn app.main:app --reload --reload-dir app
```

Endpoints:
- http://127.0.0.1:8000/docs — Swagger UI
- http://127.0.0.1:8000/v1/health — sanity check

## 5.1 Inventário (opcional)

O inventário da infraestrutura fica atrás de `INVENTORY_ENABLED` (default
`false`). Para ligar em dev:

```bash
.venv/bin/alembic upgrade head          # tabelas inventories, inventory_resources, inventory_relations
INVENTORY_ENABLED=true .venv/bin/uvicorn app.main:app --reload --reload-dir app
```

Mocks coerentes com os de billing (as mesmas contas, projetos e recursos), para
testar o fluxo inteiro sem cliente real:

```bash
PYTHONPATH=. python scripts/gerar_mocks_billing.py --destino /tmp/mocks
PYTHONPATH=. python scripts/gerar_mocks_inventario.py --billing /tmp/mocks --destino /tmp/inv

curl -X POST http://127.0.0.1:8000/v1/inventories \
  -F "files=@/tmp/inv/gcp_asset_inventory_2025-12-31.json.gz"
# depois: GET /v1/inventories/{id}/topology, /correlation?report_id=, e
# POST /v1/analyses/start/{report_id} com {"inventory_id": "..."}
```

Catálogo completo e os comandos de export de cada nuvem:
[`endpoints/inventories.md`](endpoints/inventories.md).

## Testes

```bash
pytest                          # tudo (unitários + gates de integração)
pytest -m "not integration"     # só unitários — rápido, não precisa de banco
pytest -m integration           # só os gates da pipeline (precisa de Postgres + seeds)
```

Os gates de integração **pulam graciosamente** sem Postgres ou sem seed,
declarando o motivo. No CI (`.github/workflows/tests.yml`) um skip é tratado
como **falha de ambiente** — skip silencioso esconderia a rede de segurança caída.

Qualidade do request enviado ao agente (DE-PARA por custo, divergência vs
catálogo curado, cobertura de expectativa de migração):

```bash
python scripts/measure_request_quality.py --json /tmp/qualidade.json
```

## Smoke test rápido

```bash
# Upload de uma fixture (não precisa de auth em dev)
curl -X POST http://127.0.0.1:8000/v1/billing-files \
  -F "file=@app/tests/fixtures/billing/aws_cur2_sample.csv"

# Listar todos os jobs
curl http://127.0.0.1:8000/v1/billing-files | python3 -m json.tool

# Ver items do mais recente
JOB=$(curl -s "http://127.0.0.1:8000/v1/billing-files?limit=1" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['job_id'])")
curl "http://127.0.0.1:8000/v1/billing-files/$JOB/items" | python3 -m json.tool | head -50
```

## Operações úteis

### Inspecionar o banco

```bash
podman exec -it ost-pg psql -U ost -d ost_dev
# Dentro do psql:
#   \dt                                   -- listar tabelas
#   \d billing_items                      -- schema da tabela
#   SELECT count(*) FROM billing_items;
#   SELECT service, sum(cost) FROM billing_items GROUP BY service ORDER BY 2 DESC;
```

### Limpar billing data sem mexer no schema

```bash
podman exec ost-pg psql -U ost -d ost_dev -c "TRUNCATE billing_reports CASCADE;"
```

### Rodar testes

```bash
.venv/bin/pytest app/tests/unit/ -q                # unit only, rápido (~1s)
.venv/bin/pytest app/tests/unit/ --no-cov          # sem coverage report
.venv/bin/pytest -k "aws_parser"                   # filtrar por nome
```

### Lint / format

```bash
.venv/bin/ruff check app/         # lint
.venv/bin/ruff format app/        # formata
.venv/bin/ruff check app/ --fix   # auto-fix o que dá
```

## Stop tudo

```bash
# API: Ctrl+C (se em foreground) ou:
pkill -f "uvicorn app.main"

# Postgres:
podman stop ost-pg
```

## Troubleshooting

### "health=000" no curl

API não está rodando. Cheque `ps aux | grep uvicorn`.

### `db: "down"` no /health

Postgres não está acessível. `podman ps` deve mostrar `ost-pg: Up ...`. Se não:
`podman start ost-pg` e espere ~2s.

### `MissingGreenlet` em rotas que escrevem

SQLAlchemy async: alguém acessou uma coluna lazy (server-side default como
`updated_at`) após `flush`/`commit`. Solução: `await session.refresh(model)`
antes de ler. Já aplicado em `PgBillingReportRepository.save/update`.

### Job fica preso em `queued` ao falhar

Não deveria mais — `_run_ingest_isolated` grava o `FAILED` em uma session limpa.
Se acontecer, ver `/tmp/uvicorn.log` ou stdout para o traceback completo.

### `ProviderDetectionError: File format not recognized`

Adicione um parser novo em `app/infrastructure/file_ingestion/parsers/` e
registre em `detector.PARSERS`. Padrão: ler header (5-8 KB), checar tokens
únicos por content, retornar `True` em `can_parse`.

### Venv quebrado depois de atualizar Python do sistema

```bash
rm -rf .venv
python3.14 -m venv .venv     # ou python3.12
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Documentação relacionada

- [`architecture.md`](architecture.md) — stack, regras de DI, banco, CORS.
- [`api-conventions.md`](api-conventions.md) — contratos de rota.
- [`endpoints/billing.md`](endpoints/billing.md) — endpoints da Fase 1.
- [`parsers.md`](parsers.md) — formatos aceitos no upload, debug de detecção.
