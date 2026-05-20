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

## 4. Aplicar migrations

```bash
.venv/bin/alembic upgrade head
```

Cria `billing_reports` + `billing_items` com `NUMERIC(18,6)`, índices e FK CASCADE.

## 5. Subir a API

```bash
.venv/bin/uvicorn app.main:app --reload --reload-dir app
```

Endpoints:
- http://127.0.0.1:8000/docs — Swagger UI
- http://127.0.0.1:8000/v1/health — sanity check

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
