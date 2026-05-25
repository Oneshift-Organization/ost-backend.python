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

## 3. Canonical Data Contracts

### 3.1 Canonical billing row (already in system)

The parser outputs a canonical DataFrame (see `docs/data-model.md`).
This remains unchanged. The refactor introduces **enrichment stages**.

### 3.2 New intermediate contract: `billing_enriched`

`billing_enriched` merges raw billing rows with SKU metadata and service
ontology.

```json
{
  "provider": "aws",
  "service_name": "AmazonEC2",
  "service_type": "virtual_server",
  "category": "compute",
  "usage_type": "BoxUsage:m7g.2xlarge",
  "region": "us-east-1",
  "usage_amount": 18454.04,
  "usage_unit": "Hrs",
  "cost": 4822.98,
  "currency": "USD",
  "sku_metadata": {
    "instance_type": "m7g.2xlarge",
    "vcpus": 8,
    "ram_gb": 32,
    "architecture": "arm64"
  }
}
```

### 3.3 Agent payload: `migration_context`

```json
{
  "source_provider": "aws",
  "billing_period": "2026-04",
  "total_spend": 10388.592224,
  "currency": "USD",
  "categories": [
    {
      "category": "containers",
      "billing_total": 1637.044164,
      "services": [
        {
          "service_name": "Elastic Container Service for Kubernetes",
          "service_type": "managed_kubernetes",
          "total_cost": 1369.516505,
          "usage": {
            "quantity": 5208,
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
              "estimated_monthly_cost": 1240.88,
              "scores": {
                "financial": 74,
                "technical": 82,
                "enterprise": 76,
                "final": 77
              },
              "metadata": {
                "economist": { "pricing_model": "cluster + nodes", "cost_drivers": ["nodes"] },
                "architect": { "portability": "semi_portable", "migration_complexity": "medium" },
                "strategist": { "lock_in_risk": "medium", "sla_review_required": true }
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

### 4.1 `cloud_sku_catalog`

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
    price_per_unit NUMERIC,
    currency TEXT NOT NULL,
    attributes JSONB,
    effective_date TIMESTAMP,
    collected_at TIMESTAMP NOT NULL DEFAULT now()
);
```

### 4.2 `service_ontology`

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
    created_at TIMESTAMP DEFAULT now()
);
```

### 4.3 `service_equivalence`

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

## 5. Behavior (Key Rules)

1. **Never** compute math inside LLM.
2. **Always** include `pricing_confidence` + `assumptions` in metadata.
3. If a usage type cannot be mapped, log it to an `unmapped_billing_lines`
   table (dead-letter) and continue.
4. If pricing metadata missing, fallback to cached `cloud_sku_catalog`.
5. If still missing, mark `estimated_monthly_cost` as `null` and set
   `pricing_confidence: "low"`.

## 6. Acceptance Criteria

- The backend can ingest AWS CUR and produce `migration_context` with
  non-null metadata for at least **compute + storage + database + containers**.
- Pricing metadata is refreshed via worker (daily/weekly).
- `migration_context` is produced in < 5s for a typical monthly report.
- All endpoints return deterministic JSON.
