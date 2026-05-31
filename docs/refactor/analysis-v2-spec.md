# Analysis V2 — SDD Spec

> Companion to [`sku-accuracy-spec.md`](sku-accuracy-spec.md) and
> [`pool-equivalence-spec.md`](pool-equivalence-spec.md).
> Defines the V2 analysis contract: richer response schema, data-only LLM prompt,
> resilient response parsing, and the legacy direct-input endpoint.

## 0. Context

The V1 analysis pipeline (`POST /v1/analyses/start/{report_id}`) requires a
CSV upload and enrichment pipeline before running the LLM. Two problems:

1. **Clients with pre-computed workloads** (e.g. internal tools, partner
   integrations) already have billing data + equivalents in JSON form and
   want to skip the upload/enrichment steps entirely.
2. **LLM output schema** (`AnalysisUIResponse`) is too thin — lacks per-provider
   scores, agent views (FinOps, Cloud Engineering, Risk), cross-cloud equivalents
   table, and the `oneshift_scores` block required by the dashboard.

Additionally, the orchestrator prompt was over-specified ("REGRAS OBRIGATÓRIAS…")
which constrained the model unnecessarily and made the output brittle. The V2
prompt passes only the data + output schema — the model reasons freely.

## 1. Goals

- **G1** — New unified response schema (`AnalysisV2Response`) used by all
  analysis endpoints (pipeline and legacy direct).
- **G2** — Data-only LLM prompt: `{ billing_data: {...}, output_schema: {...} }`.
  No instructions. Model reasons from data and schema.
- **G3** — Resilient response parser: tolerates field-name variations from the
  LLM (aliases, camelCase, partial responses) without crashing.
- **G4** — New endpoint `POST /v1/analyses/direct`: accepts the legacy JSON
  payload (pre-computed workloads + equivalents) and returns `AnalysisV2Response`
  directly (synchronous, no CSV upload required).
- **G5** — Backwards compat: existing `GET /{job_id}/result` returns V2 schema;
  `AnalysisUIResponse` is kept for internal use only (mock → V2 fallback).

## 2. Response Schema — `AnalysisV2Response`

```json
{
  "viability_score": 720,
  "oneshift_scores": {
    "financial_score": 82,
    "technical_score": 71,
    "enterprise_score": 65,
    "overall_score": 74
  },
  "provider_comparison": [
    {
      "provider": "AWS",
      "financial_score": 85,
      "technical_score": 78,
      "enterprise_score": 70,
      "overall_score": 78,
      "estimated_monthly_cost": 3869.0,
      "estimated_monthly_savings": 3956.0,
      "migration_complexity": "medium",
      "portability": "high",
      "confidence_level": "low",
      "strengths": ["Largest equivalent catalog", "Best compute cost_delta"],
      "weaknesses": ["Egress costs not catalogued"]
    }
  ],
  "cross_cloud_equivalents": [
    {
      "workload": "proj-id-001/E2/global",
      "source_provider": "gcp",
      "source_service": "Compute Engine",
      "target_provider": "aws",
      "target_service": "AmazonEC2 m7g.2xlarge",
      "equivalence_type": "hardware_match",
      "estimated_monthly_cost": 397.12,
      "cost_delta": -1573.74,
      "portability": "high",
      "migration_risk": "low",
      "requires_refactoring": false,
      "vendor_lock_in_level": "low"
    }
  ],
  "recommended_provider": "AWS",
  "recommendation": "MIGRATE",
  "recommendation_rationale": "...",
  "analysis_confidence": "low",
  "prompt_contract_version": "oneshift-operator-v2.1",
  "agent_views": {
    "finops": {
      "summary": "...",
      "confidence_level": "low",
      "key_findings": [],
      "financial_flags": []
    },
    "cloud_engineering": {
      "summary": "...",
      "confidence_level": "low",
      "migration_complexity": "medium",
      "technical_flags": [],
      "technical_recommendations": []
    },
    "risk_migration": {
      "summary": "...",
      "confidence_level": "low",
      "risk_flags": [],
      "blocked_or_uncertain_items": [],
      "recommended_migration_sequence": []
    }
  },
  "savings_summary": {
    "total_monthly_savings_usd": 3956.33,
    "percentage_reduction": 14.21,
    "payback_period_months": 6,
    "confidence_level": "low"
  },
  "flags": {},
  "strategic_notes": [],
  "action_plan": []
}
```

**Pydantic model location:** `app/schemas/analysis_v2.py`

## 3. Resilient Parser

The LLM may return field name variants. The parser handles this in a two-pass
approach:

**Pass 1 — key normalization** before Pydantic validation:

| LLM variant | Canonical field |
|---|---|
| `providers` / `cloud_providers` / `provider_analysis` | `provider_comparison` |
| `equivalents` / `cross_cloud` / `cloud_equivalents` | `cross_cloud_equivalents` |
| `agents` / `agent_analysis` / `views` | `agent_views` |
| `viability` / `score` | `viability_score` |
| `confidence` | `analysis_confidence` |
| `savings` / `monthly_savings` | `savings_summary` |
| `notes` | `strategic_notes` |
| `plan` | `action_plan` |
| `finops_view` / `economist` | `agent_views.finops` |
| `engineering_view` / `architect` | `agent_views.cloud_engineering` |
| `risk_view` / `strategist` | `agent_views.risk_migration` |

**Pass 2 — Pydantic with `extra="ignore"` + full defaults**: any field not
normalized still parses; missing fields use safe defaults (empty lists, `"low"`,
`0`, etc.). Never raises — returns a degraded but valid `AnalysisV2Response`.

**Implementation:** `AnalysisV2Response.resilient_parse(raw: dict) -> AnalysisV2Response`

## 4. Data-Only Prompt

V1 prompt (removed):
```
Você é um especialista sênior em FinOps… REGRAS OBRIGATÓRIAS: 1. NÃO realize cálculos…
```

V2 prompt (data + schema only):
```python
payload = {
    "billing_data": context_payload,       # the actual billing/workload data
    "output_schema": V2.model_json_schema() # what to return
}
return json.dumps(payload, default=str)
```

The model reads the data and the target schema. No instructions on tone, language
or rules. Simpler, less brittle, more generalizable.

## 5. New Endpoint — `POST /v1/analyses/direct`

Accepts pre-computed workload JSON (legacy format) and returns V2 synchronously.
No CSV upload, no enrichment pipeline.

**Request body:** `LegacyAnalysisRequest`
```json
{
  "source_provider": "aws",
  "billing_period": "2026-03",
  "currency": "USD",
  "total_spend": 1800,
  "company_context": { ... },
  "workloads": [{ "canonical_service": "...", "equivalents": [...] }]
}
```

**Response:** `AnalysisV2Response` (HTTP 200, synchronous — data is already
computed, no DB pipeline needed).

**Flow:**
```
POST /v1/analyses/direct
  → LegacyAnalysisRequest validated
  → convert to billing_data dict
  → OrchestratorV2.generate(data) → LLM
  → AnalysisV2Response.resilient_parse(raw)
  → HTTP 200 AnalysisV2Response
```

## 6. Existing Endpoint Updates

| Endpoint | Before | After |
|---|---|---|
| `GET /{job_id}/result` | `result: AnalysisUIResponse` | `result: AnalysisV2Response` |
| `GET /by-external-id/{id}` | same | same |
| `run_analysis_pipeline` | produces `AnalysisUIResponse` | produces `AnalysisV2Response` |
| `LLMOrchestrator.generate_report` | V1 verbose prompt + V1 schema | V2 data-only prompt + V2 schema |
| `MockLLMClient.generate` | returns V1 shape | returns V2 shape |

## 7. Work Units

| # | Unit | Touches |
|---|---|---|
| V1 | `AnalysisV2Response` schema + `LegacyAnalysisRequest` + `resilient_parse` | `app/schemas/analysis_v2.py` (new) |
| V2 | Orchestrator: data-only prompt, V2 response model | `app/infrastructure/llm/orchestrator.py` |
| V3 | MockLLMClient: return V2 | `app/infrastructure/llm/mock_client.py` |
| V4 | `run_analysis_pipeline`: use V2 | `app/use_cases/analysis/run_analysis_pipeline.py` |
| V5 | Update `AnalysisJobResponse.result` to V2; add `POST /direct` endpoint | `app/schemas/analysis.py`, `app/api/v1/routes/analysis.py` |

## 8. Acceptance Criteria

- `POST /v1/analyses/direct` with the example legacy JSON returns 200
  `AnalysisV2Response` with non-empty `provider_comparison` and `agent_views`.
- `GET /{job_id}/result` for a pipeline-run job returns `result` as
  `AnalysisV2Response` shape.
- Resilient parser: a response with `"providers"` instead of
  `"provider_comparison"` parses correctly.
- Full test suite green; mock returns V2 shape.

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Data-only prompt produces off-schema output | Resilient parser + safe defaults; never 500 |
| Legacy format missing `company_context` | All `LegacyAnalysisRequest` fields optional with defaults |
| LLM latency in sync `/direct` endpoint | 120s timeout; consider async job pattern for large payloads |
| `AnalysisUIResponse` usages break | Replace all references with V2; keep V1 model deprecated |
