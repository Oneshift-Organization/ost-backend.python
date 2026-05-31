# Deploy Plan — Cloud Intelligence Platform (Backend Python)

> Guia completo de deploy em produção: pré-requisitos, variáveis de ambiente,
> migrações, seeds, startup e verificação.
>
> Stack: Python 3.12+ · FastAPI · SQLAlchemy async · asyncpg · Alembic · PostgreSQL 16

---

## Sumário

1. [Pré-requisitos](#1-pré-requisitos)
2. [Variáveis de Ambiente](#2-variáveis-de-ambiente)
3. [Instalação de Dependências](#3-instalação-de-dependências)
4. [Migrações de Banco](#4-migrações-de-banco)
5. [Seeds de Dados](#5-seeds-de-dados)
6. [Subindo a Aplicação](#6-subindo-a-aplicação)
7. [Verificação Pós-Deploy](#7-verificação-pós-deploy)
8. [Ordem Completa em Um Bloco](#8-ordem-completa-em-um-bloco)
9. [Referência alembic.ini](#9-referência-alembicini)

---

## 1. Pré-requisitos

| Requisito | Versão mínima | Observação |
|---|---|---|
| Python | 3.12 | 3.14 recomendado (usado em dev) |
| PostgreSQL | 16 | OCI compartilhado em produção |
| pip / venv | — | Sem Docker obrigatório |

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 2. Variáveis de Ambiente

Em produção copie `.env.example` para `.env` e preencha todos os valores.
A aplicação lê por padrão `.env.dev`; **em produção defina as variáveis
diretamente no ambiente do container/servidor** — não suba arquivos `.env`
no repositório.

```bash
cp .env.example .env
# edite .env com os valores reais antes de continuar
```

### 2.1 Aplicação

| Variável | Obrigatória | Valor esperado | O que faz |
|---|---|---|---|
| `APP_ENV` | ✅ | `production` | Ativa modo produção: desabilita `/docs`, `/redoc`, `/openapi.json` |
| `LOG_LEVEL` | — | `INFO` | Nível de log structlog: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `SECRET_KEY` | ✅ | string aleatória 32+ chars | Usado para assinatura de tokens internos; gere com `openssl rand -hex 32` |
| `AUTH_ENABLED` | ✅ | `true` | **Nunca defina `false` em produção.** Ativa validação de JWT Supabase RS256 |
| `CORS_ORIGINS` | ✅ | JSON array de URLs | Ex.: `["https://app.oneshift.com.br"]` — **`["*"]` é bloqueado em startup** |
| `MAX_UPLOAD_SIZE_MB` | — | `50` | Tamanho máximo de CSV de billing para upload |

### 2.2 Banco de Dados

| Variável | Obrigatória | Valor esperado | O que faz |
|---|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://user:pass@host:5432/dbname` | URL de conexão async. Driver asyncpg obrigatório. |
| `DB_POOL_SIZE` | — | `10` | Tamanho do connection pool SQLAlchemy |
| `DB_ECHO` | — | `false` | Loga todas as queries SQL — **nunca `true` em produção** |

> **Nota:** `alembic.ini` tem `sqlalchemy.url` vazio de propósito. O Alembic
> lê `DATABASE_URL` do ambiente via `get_settings()` em `migrations/env.py`.

### 2.3 Supabase (Autenticação)

| Variável | Obrigatória | Valor esperado | O que faz |
|---|---|---|---|
| `SUPABASE_URL` | ✅ | `https://<project>.supabase.co` | URL do projeto Supabase |
| `SUPABASE_ANON_KEY` | ✅ | JWT público do projeto | Chave pública usada no frontend para autenticar requests |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | JWT service role | Chave de admin para operações server-side (criação de usuário, etc.) |
| `SUPABASE_JWT_AUDIENCE` | — | `authenticated` | Audience do JWT RS256 emitido pelo Supabase |

> O backend **apenas valida** JWTs emitidos pelo Supabase — não emite tokens.
> A chave pública RS256 é obtida automaticamente via JWKS a partir de `SUPABASE_URL`.

### 2.4 LLM (Análise de Migração)

Defina **apenas um** dos três providers abaixo. `LLM_PROVIDER` seleciona qual será usado.

| Variável | Obrigatória | Valor | O que faz |
|---|---|---|---|
| `LLM_PROVIDER` | ✅ | `anthropic` \| `gemini` \| `openrouter` \| `mock` | Provider ativo. Use `anthropic` em produção. `mock` apenas para testes sem API key. |
| `ANTHROPIC_API_KEY` | Se `anthropic` | `sk-ant-api03-...` | Chave da API Anthropic (Claude). Gere em console.anthropic.com |
| `ANTHROPIC_MODEL` | — | `claude-sonnet-4-6` | Modelo Claude. Padrão recomendado. `claude-opus-4-8` para máxima qualidade. |
| `GEMINI_API_KEY` | Se `gemini` | `AIza...` | Chave da API Google Gemini |
| `GEMINI_MODEL` | — | `gemini-2.5-flash` | Modelo Gemini a usar |
| `OPENROUTER_API_KEY` | Se `openrouter` | `sk-or-...` | Chave OpenRouter (suporta múltiplos modelos free/paid) |
| `OPENROUTER_MODEL` | — | `deepseek/deepseek-v4-flash:free` | Modelo via OpenRouter |

### 2.5 Criptografia de Credenciais

| Variável | Obrigatória | Valor | O que faz |
|---|---|---|---|
| `CLOUD_CREDS_FERNET_KEY` | ✅ | chave Fernet base64 | Criptografa credenciais cloud armazenadas no banco. Gere com `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

> **⚠️ Guarde essa chave com segurança.** Perder a chave torna as credenciais
> armazenadas irrecuperáveis.

### 2.6 Credenciais Cloud (Opcional — para sync de catálogo)

Usadas apenas pelo worker `sync_gcp_catalog` para atualizar preços. Em MVP podem ficar vazias.

| Variável | Obrigatória | O que faz |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Se sync GCP | Caminho para JSON de service account GCP (catalog sync) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Se sync AWS | Credenciais AWS para Price List API |
| `AZURE_SUBSCRIPTION_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Se sync Azure | Credenciais Azure para pricing API |
| `OCI_USER_OCID` / `OCI_TENANCY_OCID` / `OCI_FINGERPRINT` / `OCI_REGION` | Se sync OCI | Credenciais OCI para price list |

---

## 3. Instalação de Dependências

```bash
# runtime
pip install -r requirements.txt

# desenvolvimento/CI
pip install -r requirements-dev.txt
```

**Dependências críticas incluídas:**
- `fastapi` + `uvicorn[standard]` + `gunicorn` — servidor HTTP
- `sqlalchemy[asyncio]` + `asyncpg` — ORM async + driver PostgreSQL
- `alembic` — migrations
- `anthropic>=0.40` — cliente Claude (LLM principal em produção)
- `rapidfuzz>=3.9` — fuzzy matching da Matching Ladder L4
- `pandas` + `pandera` — parsing e validação de CSVs de billing

---

## 4. Migrações de Banco

O banco precisa existir antes das migrações. `alembic.ini` tem
`sqlalchemy.url` vazio — o Alembic lê `DATABASE_URL` do ambiente.

```bash
# Verificar head atual (deve ser d3c4e5f6a7b8 em produção nova)
alembic current

# Aplicar todas as migrações pendentes
alembic upgrade head
```

### Histórico de migrações (ordem de aplicação)

| Migration | O que cria/altera |
|---|---|
| `0001_initial_billing_schema` | `billing_reports`, `billing_items` |
| `0002_add_service_metadata` | `cloud_services_metadata`, `cloud_services_comparation` |
| `0003_add_doc_origin` | `doc_origin` em `cloud_services_metadata` |
| `0004_fix_unique_constraint` | Corrige constraint em `cloud_services_comparation` |
| `0005_drop_unique_constraint` | Remove constraint problemática |
| `0006_add_docs_costs` | `docs_costs` em `cloud_services_metadata` |
| `0007_add_enrichment_tables` | `billing_enriched`, `service_ontology`, `unmapped_billing_lines`, `cloud_sku_catalog` |
| `0009_add_ai_structured_output` | `ai_structured_output` em `analysis_jobs` |
| `1de8e80200f7_add_vcpus_ram_gb_to_sku_catalog` | `vcpus`, `ram_gb` em `cloud_sku_catalog` |
| `71989ed74c64_add_external_id_to_billing_reports_and_` | `external_id` em reports e analysis_jobs |
| `8f40871ac57e_add_analysis_jobs_table` | `analysis_jobs` |
| `a1f6761f2452_add_project_id_to_billing_enriched` | `project_id` em `billing_enriched` |
| `b1a2c3d4e5f6_add_component_type_to_sku_catalog` | `component_type` em `cloud_sku_catalog` |
| `c2b3d4e5f6a7_add_sku_classification_rules` | tabela `sku_classification_rules` |
| `d3c4e5f6a7b8_add_matching_fields_to_billing_enriched` | `component_type`, `mapping_confidence`, `match_layer` em `billing_enriched` |

> **HEAD atual:** `d3c4e5f6a7b8`

---

## 5. Seeds de Dados

> **Seeds são idempotentes** — todos usam `ON CONFLICT DO NOTHING` ou
> `DELETE + INSERT`. Podem ser re-executados sem risco.
>
> **Ordem importa:** `service_metadata` → `catalog_pareto` → `classification_rules` → `from_csv`

### 5.1 Seed obrigatório — Metadados de serviço cross-cloud

```bash
python -m app.infrastructure.database.seed_service_metadata
```

**Popula:** `cloud_services_metadata` (40 linhas) + `cloud_services_comparation` (816 linhas)

**O que é:** Catálogo curado de serviços equivalentes entre clouds com
portabilidade, lock-in e links de documentação. Necessário para a Layer 3
(L3) do Matching Ladder e para os endpoints `/agents-payload`.

---

### 5.2 Seed obrigatório — Catálogo de SKUs (hardware + preço)

```bash
python -m app.infrastructure.database.seed_catalog_pareto
```

**Popula:** `cloud_sku_catalog` (~95 SKUs: AWS EC2/RDS, GCP Compute/Cloud SQL, OCI Compute/MySQL)

**O que é:** SKUs com vcpus, ram_gb, price_per_unit e component_type.
Necessário para a equivalência de hardware (`match_pool`) e para o `EquivalenceMatcher`.
Sem este seed os equivalentes de custo ficam vazios.

---

### 5.3 Seed obrigatório — Regras de classificação (Matching Ladder)

```bash
python -m app.infrastructure.database.seed_classification_rules
```

**Popula:** `sku_classification_rules` (81 regras: GCP + AWS)

**O que é:** Regras glob/regex para o Matching Ladder L2 — ex.:
`*Instance Core running in*` → `core`, `*cloud sql*` → `database`,
`*egress*` → `network_egress`. Sem este seed praticamente todos os SKUs
caem em orphan (coverage < 5%).

---

### 5.4 Seed obrigatório — Mapeamento CSV cross-cloud

```bash
python -m app.infrastructure.database.seed_from_csv
```

**Popula:** `cloud_services_comparation` a partir de
`app/infrastructure/database/models/cloud_services_mapping.csv` (816 linhas)

**O que é:** Mapeamento canônico de equivalência entre serviços cross-cloud
(ex.: Amazon EC2 ↔ Compute Engine ↔ Azure VM ↔ OCI Compute). Substitui todos
os dados existentes (TRUNCATE + INSERT). Deve ser rodado após `seed_service_metadata`.

---

### 5.5 Seed opcional — Ontologia de serviços e SKUs mock

```bash
# Apenas se quiser dados mock para testes de EquivalenceMatcher
python -m app.infrastructure.database.seed_mock_catalog
```

**Popula:** `cloud_sku_catalog` (4 SKUs base) + `service_ontology` (entradas básicas)

**Quando usar:** Apenas em ambiente de testes. Em produção o `seed_catalog_pareto`
já é suficiente.

---

### 5.6 Seed opcional — Dados de refactor/testes E2E

```bash
# Apenas se precisar de dados de test E2E
python -m app.infrastructure.database.seed_refactor
```

**Popula:** `service_ontology` + `cloud_sku_catalog` com dados de teste para
validação do pipeline E2E. **Não usar em produção com dados reais.**

---

## 6. Subindo a Aplicação

### Desenvolvimento

```bash
uvicorn app.main:app --reload --reload-dir app --port 8000
```

### Produção (gunicorn + uvicorn workers)

```bash
gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 4 \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --keepalive 5 \
  --access-logfile - \
  --error-logfile -
```

> **Recomendação de workers:** `(2 × CPU_cores) + 1`
> Para 2 vCPUs → 5 workers. Para 4 vCPUs → 9 workers.

### Como container (sem Docker oficial — adaptar ao seu orquestrador)

```bash
# Exemplo de entrypoint
gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers ${GUNICORN_WORKERS:-4} \
  --bind 0.0.0.0:${PORT:-8000} \
  --timeout 120
```

---

## 7. Verificação Pós-Deploy

```bash
# 1. Health check
curl https://<seu-dominio>/v1/health
# Esperado: {"status":"ok","db":"ok"}

# 2. Verificar migrações aplicadas
alembic current
# Esperado: d3c4e5f6a7b8 (head)

# 3. Verificar seeds
python -c "
import asyncio
from sqlalchemy import text
from app.infrastructure.database.session import get_sessionmaker

async def check():
    sm = get_sessionmaker()
    async with sm() as s:
        for tbl in ['cloud_sku_catalog','sku_classification_rules',
                    'cloud_services_metadata','cloud_services_comparation']:
            n = (await s.execute(text(f'SELECT COUNT(*) FROM {tbl}'))).scalar()
            print(f'{tbl}: {n} rows')
asyncio.run(check())
"
# Esperado mínimo:
#   cloud_sku_catalog: >= 95
#   sku_classification_rules: >= 81
#   cloud_services_metadata: >= 40
#   cloud_services_comparation: >= 816

# 4. Verificar LLM configurado
python -c "
from app.core.config import get_settings
s = get_settings()
print('provider:', s.llm_provider)
print('key_set:', bool(s.anthropic_api_key or s.gemini_api_key or s.openrouter_api_key))
"
```

---

## 8. Ordem Completa em Um Bloco

```bash
# ── Configuração ──────────────────────────────────────────────────────────
cp .env.example .env
# EDITE .env antes de continuar

# ── Dependências ──────────────────────────────────────────────────────────
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ── Banco ─────────────────────────────────────────────────────────────────
alembic upgrade head

# ── Seeds (ordem obrigatória) ─────────────────────────────────────────────
python -m app.infrastructure.database.seed_service_metadata
python -m app.infrastructure.database.seed_catalog_pareto
python -m app.infrastructure.database.seed_classification_rules
python -m app.infrastructure.database.seed_from_csv

# ── Verificação ───────────────────────────────────────────────────────────
curl http://localhost:8000/v1/health   # após subir a aplicação

# ── Startup ───────────────────────────────────────────────────────────────
gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 4 \
  --bind 0.0.0.0:8000 \
  --timeout 120
```

---

## 9. Referência alembic.ini

```ini
[alembic]
script_location = app/infrastructure/database/migrations
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url =        # ← INTENCIONAL: Alembic lê DATABASE_URL do ambiente
```

> **Por que `sqlalchemy.url` vazio?**
> O arquivo `migrations/env.py` chama `get_settings()` e injeta `DATABASE_URL`
> dinamicamente. Isso evita credenciais hardcoded no `.ini` e garante que
> qualquer `alembic upgrade head` use a mesma conexão configurada no ambiente.

### Criar nova migration

```bash
alembic revision --autogenerate -m "descricao_snake_case"
alembic upgrade head
```

### Rollback de emergência

```bash
alembic downgrade -1        # volta 1 migration
alembic downgrade base      # volta tudo (CUIDADO em produção)
```

---

## Checklist de Deploy

- [ ] `APP_ENV=production` definido
- [ ] `AUTH_ENABLED=true` definido
- [ ] `SECRET_KEY` gerado com `openssl rand -hex 32`
- [ ] `DATABASE_URL` apontando para PostgreSQL de produção
- [ ] `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` configurados
- [ ] `LLM_PROVIDER=anthropic` e `ANTHROPIC_API_KEY` configurados
- [ ] `CLOUD_CREDS_FERNET_KEY` gerado e armazenado com segurança
- [ ] `CORS_ORIGINS` listando apenas domínios de produção (sem `*`)
- [ ] `alembic upgrade head` executado com sucesso
- [ ] 4 seeds obrigatórios executados (verificar contagens)
- [ ] `/v1/health` retorna `{"status":"ok","db":"ok"}`
- [ ] `gcp-service-account.json` **NÃO** está no repositório (chave revogada se exposta)
