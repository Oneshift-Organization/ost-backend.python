# Spec-Driven Development (SDD) Spec

## 0. Context

We currently ingest multi-cloud billing files and return a `migration_context`
JSON for the agents. The existing flow works for multiple file formats but
has two gaps:

1. The upstream billing feeds are heterogeneous and often lack details.
2. The output metadata for equivalents is often `null` or incomplete for
   accurate migration scoring.

This spec defines the refactor goals, data contracts, and behavior required
to deliver a reliable, end-to-end pipeline that supports **deterministic cost
calculation** in the backend and **explainability-only** in the AI layer.

## 1. Goals (Product + Engineering)

### G1 — End-to-end, deterministic pipeline
The backend must produce a complete `migration_context` JSON where:
- All **source services** include usage details (when available).
- All **target equivalents** include **pricing and technical metadata**.
- The backend performs all numeric calculations (no math in LLM).

### G2 — Cloud-agnostic normalization
The ingestion pipeline must support AWS, Azure, GCP, OCI with minimal provider-
specific logic. Use a **canonical schema** and map to a **service ontology**.

### G3 — Non-fragile enrichment
Do **not** depend on runtime HTML scraping. Use:
- Official pricing APIs (AWS Price List, Azure Retail Prices, GCP Billing
  Catalog, OCI price list fallback).
- Scheduled workers to refresh pricing catalogs.
- Postgres JSONB to store per-provider metadata.

### G4 — Agent-ready payload
The `migration_context` should be ready to feed 3 agents (Economist, Architect,
Strategist) with confidence scores, assumptions, and risk flags.

## 2. Non-Goals

- We do **not** aim to compute live enterprise discounts (EA/EDP).
- We do **not** aim to derive dependencies from network traces in this phase.
- We do **not** use LLM for numeric computation or classification when SKU
  data is available.
- We do **not** use LLM to override known SKU catalog matches.

## 3. Canonical Data Contracts

### 3.1 Canonical billing row (already in system)

The parser outputs a canonical DataFrame (see `docs/data-model.md`).
This remains unchanged. The refactor introduces **enrichment stages**.

### 3.2 New intermediate contract: `billing_enriched` (persisted table)

`billing_enriched` merges raw billing rows with SKU metadata and service
ontology. It is a **persisted table** to enable auditability, reprocessing,
and deterministic downstream joins. Each row maps 1:1 to a canonical billing
line (by `source_line_hash`).

> **Serialization note**: any `Decimal` field in API responses must be emitted
> as **string** per [`api-conventions.md`](../api-conventions.md#serialização-json).

```json
{
  "provider": "aws",
  "service_name": "AmazonEC2",
  "service_type": "virtual_server",
  "category": "compute",
  "usage_type": "BoxUsage:m7g.2xlarge",
  "region": "us-east-1",
  "usage_amount": "18454.040000",
  "usage_unit": "Hrs",
  "cost": "4822.980000",
  "currency": "USD",
  "sku_id": "aws:ec2:us-east-1:m7g.2xlarge",
  "pricing_source": "sku_catalog",
  "pricing_confidence": "high",
  "scope": "line_item",
  "sku_metadata": {
    "instance_type": "m7g.2xlarge",
    "vcpus": 8,
    "ram_gb": 32,
    "architecture": "arm64"
  }
}
```

**Enums**:
- `pricing_source`: `sku_catalog | manual | fallback | missing`
- `pricing_confidence`: `high | medium | low | missing`
- `scope`: `line_item | global_zero | summary`

### 3.3 Agent payload: `migration_context`

```json
{
  "source_provider": "aws",
  "billing_period": "2026-04",
  "total_spend": "10388.592224",
  "currency": "USD",
  "categories": [
    {
      "category": "containers",
      "billing_total": "1637.044164",
      "services": [
        {
          "service_name": "Elastic Container Service for Kubernetes",
          "service_type": "managed_kubernetes",
          "total_cost": "1369.516505",
          "usage": {
            "quantity": "5208.000000",
            "unit": "hours",
            "region": "us-east-1"
          },
          "pricing_complexity": "composite_service",
          "equivalents": [
            {
              "provider": "gcp",
              "service_name": "Google Kubernetes Engine (GKE)",
              "category": "containers",
              "service_type": "managed_kubernetes",
              "estimated_monthly_cost": "1240.880000",
              "scores": {
                "financial": 74,
                "technical": 82,
                "enterprise": 76,
                "final": 77
              },
              "metadata": {
                "economist": { "pricing_model": "cluster + nodes", "cost_drivers": ["nodes"], "pricing_confidence": "medium", "assumptions": ["uses on-demand rates"] },
                "architect": { "portability": "semi_portable", "migration_complexity": "medium", "assumptions": ["no custom CNI"] },
                "strategist": { "lock_in_risk": "medium", "sla_review_required": true, "assumptions": ["no regulated data"] }
              }
            }
          ]
        }
      ]
    }
  ]
}
```

## 4. Required Tables

### 4.1 `billing_enriched`

Persisted enrichment rows (post-ontology + SKU join). This table is the
source for `migration_context` generation and agent payloads.

```sql
CREATE TABLE billing_enriched (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES billing_reports(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,

    provider VARCHAR(10) NOT NULL,
    billing_period VARCHAR(7) NOT NULL,
    service_name VARCHAR(200) NOT NULL,
    service_type VARCHAR(100),
    category VARCHAR(100),
    usage_type VARCHAR(200),
    region VARCHAR(100),

    usage_amount NUMERIC(18, 6),
    usage_unit VARCHAR(50),
    cost NUMERIC(18, 6) NOT NULL,
    currency VARCHAR(3) NOT NULL,

    sku_id TEXT,
    sku_metadata JSONB,
    pricing_source VARCHAR(20) NOT NULL,
    pricing_confidence VARCHAR(10) NOT NULL,
    scope VARCHAR(20) NOT NULL DEFAULT 'line_item',

    source_line_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_billing_enriched_user_period
  ON billing_enriched (user_id, billing_period);

CREATE INDEX ix_billing_enriched_report_id
  ON billing_enriched (report_id);
```

### 4.2 `cloud_sku_catalog`

Stores normalized SKU metadata for each provider.

```sql
CREATE TABLE cloud_sku_catalog (
    sku_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    service_name TEXT NOT NULL,
    service_type TEXT,
    category TEXT,
    region TEXT,
    usage_unit TEXT,
    price_per_unit NUMERIC(18, 6),
    currency TEXT NOT NULL,
    attributes JSONB,
    effective_date TIMESTAMP,
    collected_at TIMESTAMP NOT NULL DEFAULT now()
);
```

### 4.3 `service_ontology`

Maps raw billing usage types to canonical service types.

```sql
CREATE TABLE service_ontology (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    usage_type TEXT NOT NULL,
    service_name TEXT NOT NULL,
    category TEXT NOT NULL,
    service_type TEXT NOT NULL,
    confidence TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT true,
    deprecated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT now()
);
```

### 4.4 `service_equivalence`

Defines canonical equivalents.

```sql
CREATE TABLE service_equivalence (
    id UUID PRIMARY KEY,
    source_provider TEXT NOT NULL,
    source_service_type TEXT NOT NULL,
    target_provider TEXT NOT NULL,
    target_service_name TEXT NOT NULL,
    target_service_type TEXT NOT NULL,
    category TEXT NOT NULL,
    portability_level TEXT,
    default_migration_complexity TEXT,
    metadata_template JSONB,
    active BOOLEAN DEFAULT true
);
```

### 4.5 `unmapped_billing_lines`

Dead-letter rows when `usage_type` cannot be mapped.

```sql
CREATE TABLE unmapped_billing_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES billing_reports(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,

    provider VARCHAR(10) NOT NULL,
    billing_period VARCHAR(7) NOT NULL,
    usage_type VARCHAR(200) NOT NULL,
    service_name VARCHAR(200),
    region VARCHAR(100),

    reason TEXT NOT NULL,
    raw_payload JSONB NOT NULL,
    source_line_hash VARCHAR(64) NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_unmapped_billing_lines_user_period
  ON unmapped_billing_lines (user_id, billing_period);
```

### 4.6 `metadata_template` schema (inside `service_equivalence`)

`metadata_template` is a JSONB skeleton injected into agent payloads. Minimum
shape:

```json
{
  "economist": {
    "pricing_model": "...",
    "pricing_confidence": "high|medium|low|missing",
    "cost_drivers": ["..."],
    "assumptions": ["..."],
    "risks": ["..."]
  },
  "architect": {
    "portability": "portable|semi_portable|proprietary",
    "migration_complexity": "low|medium|high",
    "known_gaps": ["..."],
    "assumptions": ["..."]
  },
  "strategist": {
    "lock_in_risk": "low|medium|high",
    "sla_review_required": false,
    "assumptions": ["..."],
    "risks": ["..."]
  }
}
```

## 5. Behavior (Key Rules)

1. **Never** compute math inside LLM.
2. **Always** include `pricing_confidence` + `assumptions` in metadata.
3. If a usage type cannot be mapped, log it to `unmapped_billing_lines` and
   continue (no pipeline failure).
4. Pricing lookup order: `cloud_sku_catalog` → manual overrides → fallback.
5. If still missing, set `estimated_monthly_cost = null`,
   `pricing_source = "missing"`, `pricing_confidence = "low"`.
6. `scope` is propagated from `raw_payload.scope` when available
   (`global_zero`), else `line_item`.
7. LLM classification is **only** allowed as a fallback when there is no
   SKU mapping and no ontology mapping; never override known catalog data.

## 6. Acceptance Criteria

- The backend can ingest AWS CUR and produce `migration_context` with
  non-null metadata for at least **compute + storage + database + containers**.
- Pricing metadata is refreshed via worker (daily/weekly).
- Performance targets:
  - Typical report (≈100k lines): `migration_context` in < 5s.
  - Large report (≈1M lines): `migration_context` in < 60s.
- All endpoints return deterministic JSON.

## 7. Scoring Model (Deterministic)

- `financial`, `technical`, `enterprise`, `final` are integers in **0–100**.
- Final score formula (default weights):

```
final = round(
  0.40 * financial +
  0.35 * technical +
  0.25 * enterprise
)
```

If the product needs a 0–1 confidence, expose it as a separate
`confidence_score` field (0.0–1.0) to avoid mixing scales.
