# Refactor Plan (Spec-Driven)

## Status

- **Status**: Draft
- **Goal**: Remove hardcode, standardize ingestion, enrich pricing data, and
  deliver agent-ready payloads end-to-end.

## 1. Problem Statement

The current system can ingest multi-cloud billing files and generate a
`migration_context` JSON, but:

1. Many billing sources are aggregated (e.g., Cost Explorer pivoted CSV),
   losing usage metrics needed for deterministic price estimation.
2. The output equivalents often lack pricing and technical metadata.
3. Any provider-specific parsing or pricing enrichment requires manual rules.

We must refactor to a **data-driven** system with:
- A **canonical billing schema** (already exists).
- A **service ontology** (category + service_type) that is provider-agnostic.
- Pricing **catalog workers** that refresh SKU data from official APIs.
- A deterministic **estimation engine** in the backend.
- AI agents for explanation only.

## 2. Strategic Principles

1. **No math in LLM** — all calculations are backend-deterministic.
2. **No runtime scraping** — use official pricing APIs in scheduled workers.
3. **Cloud-agnostic pipeline** — normalize all providers to a single schema.
4. **Graceful degradation** — when metadata is missing, set confidence low,
   but do not fail the pipeline.
5. **JSON validation** — all LLM outputs must pass schema validation.

## 3. Phased Plan

### Phase 0 — FOCUS Alignment (schema dictionary)

**Objective**: Align the canonical schema with a FOCUS-like field dictionary
so cross-cloud normalization is explicit and testable.

**Deliverables**:
- FOCUS-like field dictionary for canonical billing columns.
- Mapping table for AWS/Azure/GCP/OCI → canonical fields.

**Key tasks**:
1. Define required FOCUS-like fields (provider, service, usage, cost, time).
2. Document provider-specific quirks and mapping overrides.

**Acceptance criteria**:
- Each provider has a mapping table to the canonical schema.
- Any non-FOCUS fields are explicitly documented with rationale.

---

### Phase 1 — Canonical Billing Ingestion

**Objective**: Preserve line-item granularity and standardize raw inputs.

**Deliverables**:
- Update parser flow to maintain line-item data for AWS CUR and equivalent
  detail formats (Azure EA, GCP export, OCI report).
- Introduce `billing_enriched` **persisted table** (see SDD spec).

**Key tasks**:
1. Enforce line-level ingestion (no pre-aggregation by service).
2. Normalize minimal fields: provider, usage_type, usage_amount, region,
   cost, currency.
3. Maintain raw_payload for audit.

**Acceptance criteria**:
- A CUR line with `UsageType` + `UsageQuantity` is preserved in output.
- Aggregated Cost Explorer CSV still supported (with lower confidence).

---

### Phase 2 — Ontology Mapping (service_type)

**Objective**: Map provider-specific usage types to canonical service types.

**Deliverables**:
- `service_ontology` table (provider + usage_type → category + service_type).
- Mapping coverage for top service families: compute, storage, db, containers.

**Key tasks**:
1. Build mapping rules for known usage_type patterns.
2. Persist unmapped usage_type rows to `unmapped_billing_lines`.
3. LLM classifier fallback only for unmapped usage_type and unknown SKU
   (never override known catalog data).

**Acceptance criteria**:
- >90% of EC2, RDS, S3 usage_type lines mapped to service_type.
- Unmapped lines are logged without breaking ingestion.

---

### Phase 3 — Pricing Catalog Workers

**Objective**: Maintain pricing data without runtime scraping.

**Deliverables**:
- Scheduled workers for AWS, Azure, GCP, OCI pricing catalogs.
- `cloud_sku_catalog` populated with price_per_unit, region, usage_unit.

**Key tasks**:
1. AWS Price List API collector.
2. Azure Retail Prices API collector.
3. GCP Billing Catalog collector.
4. OCI price list fallback (manual or semi-automated).

**Acceptance criteria**:
- Catalog refresh jobs complete weekly.
- Pricing data available for top service categories.

---

### Phase 4 — Estimation Engine

**Objective**: Calculate target cost using source usage metrics.

**Deliverables**:
- Deterministic cost estimation service.
- Per-service `pricing_complexity` classification.

**Key tasks**:
1. For `simple_unit_price` services: cost = usage_amount * price_per_unit.
2. For `composite_service` (Kubernetes, DB clusters): break into subcomponents
   or mark confidence as medium.
3. Set `pricing_source` and `pricing_confidence` for every estimate.

**Acceptance criteria**:
- Each equivalent includes estimated_monthly_cost or pricing_confidence=low.

---

### Phase 5 — Agent Payload Endpoints

**Objective**: Return complete JSON for LLM agents.

**Deliverables**:
- `/v1/analyses/migration-context` returns enriched payload.
- `/v1/analyses/{job_id}/agents-payload` returns JSON for Economist, Architect,
  Strategist.

**Key tasks**:
1. Populate metadata blocks per agent (economist/architect/strategist).
2. Add scoring (financial/technical/enterprise) and assumptions.
3. Validate agent responses against JSON schema (Pydantic) and apply
   deterministic fallback on invalid output (confidence low + error note).
4. Aggregate agent outputs in backend into a final recommendation.

**Acceptance criteria**:
- Agents receive JSON-only payload with no null metadata for mapped services.
- Invalid LLM outputs never reach clients (validated + fallback applied).
- Aggregated recommendation is deterministic for the same input payload.

---

### Phase Dependencies

| Phase | Depends on | Why |
|---|---|---|
| 0 | — | Canonical field dictionary first. |
| 1 | 0 | Ingestion needs canonical mapping. |
| 2 | 1 | Ontology mapping requires normalized line items. |
| 3 | 0 | Pricing catalogs depend on canonical field dictionary. |
| 4 | 2, 3 | Estimation needs ontology + pricing data. |
| 5 | 4 | Agent payload requires enriched estimates. |

---

## 4. Agent Output Aggregation (Backend)

Agent outputs are aggregated in the backend into a deterministic final
recommendation:

- Inputs: Economist, Architect, Strategist JSON outputs.
- Validation: each output must pass JSON schema; invalid → fallback response
  with `confidence="low"` and error note.
- Aggregation: weighted scoring on `scores.final` with tie-break by
  `confidence` (high > medium > low). If still tied, choose lowest
  `estimated_monthly_cost` when available.

---

## 5. Data Model Changes

See `docs/refactor/SDD-spec.md` for schemas. Core additions:

- `billing_enriched`
- `cloud_sku_catalog`
- `service_ontology`
- `service_equivalence`
- `unmapped_billing_lines`

## 6. Endpoint Contracts

### POST `/v1/billing-files`
- unchanged (ingest + job_id)

### POST `/v1/analyses/migration-context`
- must return enriched `migration_context` with equivalents metadata.

### GET `/v1/analyses/{job_id}/agents-payload`
- returns AI-ready JSON with assumptions + confidence.
- validates LLM outputs using JSON schema and applies fallback if invalid.

## 7. Prompt Engineering Checklist

See `docs/refactor/Prompt-engineering.md`.

- JSON-only output enforced.
- No calculations by LLM.
- Explain missing data explicitly.

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Missing usage metrics in billing export | Lower confidence + request CUR in UI |
| Pricing API changes | Worker versioning + schema validation |
| OCI pricing gaps | Manual fallback + confidence low |
| LLM classifier mislabels service | Only fallback on missing SKUs + human review queue |
| High latency | Async jobs + cached catalogs |

## 9. Next Actions

1. Implement `billing_enriched` persisted table + `service_ontology` mappings.
2. Add pricing collectors for AWS + Azure (minimum viable coverage).
3. Build estimation engine for `simple_unit_price` services.
4. Wire enriched metadata + schema validation into `migration_context` output.
