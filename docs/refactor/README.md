# Refactor Plan (SDD)

This folder contains the spec-driven refactor plan for the billing ingestion,
pricing enrichment, and agent payload pipeline.

Files:

- Refactor-plan.md
  - Phased plan, deliverables, risks, and acceptance criteria.
- SDD-spec.md
  - Spec-driven requirements, data contracts, and endpoint schemas.
- sku-accuracy-spec.md
  - Matching Ladder (L0–L5), `component_type`, `sku_classification_rules`, and the
    `sku_coverage_ratio ≥ 95%` accuracy gate.
- pool-equivalence-spec.md
  - Cross-cloud DE-PARA per Capacity Pool: `match_pool`, nested `equivalents`,
    FOCUS alignment, LLM selects-not-invents.
- Prompt-engineering.md
  - Prompt templates, guardrails, and JSON schemas for agent calls.
