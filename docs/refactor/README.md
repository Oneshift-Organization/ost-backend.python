# Refactor Plan (SDD)

This folder contains the spec-driven refactor plan for the billing ingestion,
pricing enrichment, and agent payload pipeline.

Files:

- **precision-engine-v3-spec.md** ← master atual (fase PRECISÃO)
  - Truth Layer (FX + preços oficiais + tiers), Category Calculators,
    packing ótimo, ledger de premissas, payback real. Sucede as specs de
    matching (implementadas) como documento guarda-chuva.
- Refactor-plan.md
  - Phased plan, deliverables, risks, and acceptance criteria.
- SDD-spec.md
  - Spec-driven requirements, data contracts, and endpoint schemas.
- sku-accuracy-spec.md  ✅ implementada (coverage GCP 98,6% / AWS 96,0%)
  - Matching Ladder (L0–L5), `component_type`, `sku_classification_rules`, and the
    `sku_coverage_ratio ≥ 95%` accuracy gate.
- pool-equivalence-spec.md  ✅ implementada (E1–E7)
  - Cross-cloud DE-PARA per Capacity Pool: `match_pool`, nested `equivalents`,
    FOCUS alignment, LLM selects-not-invents.
- Prompt-engineering.md
  - Prompt templates, guardrails, and JSON schemas for agent calls.
