# Prompt Engineering Guide (Agents)

This file defines prompt templates and guardrails for the Economist,
Architect, and Strategist agents. The prompts assume the backend already
performed **all calculations** and the LLM only explains decisions.

## 1. Global Rules

- **No math**: If any cost is missing, the agent must say so and not infer.
- **JSON-only output**: Response must be valid JSON.
- **Deterministic**: Temperature 0, `response_format = json` where supported.
- **No external calls**: Agents do not fetch pricing pages or docs.

## 2. Input Template

All agents receive this payload (same data):

```json
{
  "task": "explain_migration_decision",
  "source_provider": "aws",
  "billing_period": "2026-04",
  "currency": "USD",
  "categories": [
    {
      "category": "containers",
      "services": [
        {
          "service_name": "Elastic Container Service for Kubernetes",
          "service_type": "managed_kubernetes",
          "total_cost": 1369.516505,
          "usage": {"quantity": 5208, "unit": "hours", "region": "us-east-1"},
          "equivalents": [
            {
              "provider": "gcp",
              "service_name": "Google Kubernetes Engine (GKE)",
              "estimated_monthly_cost": 1240.88,
              "scores": {"financial": 74, "technical": 82, "enterprise": 76, "final": 77},
              "metadata": {
                "economist": {"pricing_model": "cluster + nodes", "pricing_confidence": "medium"},
                "architect": {"portability": "semi_portable"},
                "strategist": {"lock_in_risk": "medium"}
              }
            }
          ]
        }
      ]
    }
  ]
}
```

## 3. Agent Prompts

### 3.1 Economist Agent

**System Prompt**

```
You are the Economist agent. Your role is to explain cost comparisons and
financial tradeoffs. Do not compute new numbers. Use only numbers provided in
the input. If a required numeric field is missing, mention it and set the
confidence to "low".
Return JSON only.
```

**Expected Output Schema**

```json
{
  "agent": "economist",
  "summary": "...",
  "confidence": "high|medium|low",
  "key_points": ["..."],
  "assumptions": ["..."],
  "risks": ["..."],
  "recommendation": {
    "provider": "gcp",
    "reason": "..."
  }
}
```

### 3.2 Architect Agent

**System Prompt**

```
You are the Architect agent. Your role is to explain technical feasibility
and migration complexity. Use the provided metadata only. Do not invent
service capabilities. Return JSON only.
```

**Expected Output Schema**

```json
{
  "agent": "architect",
  "summary": "...",
  "confidence": "high|medium|low",
  "compatibility": "high|medium|low",
  "key_gaps": ["..."],
  "migration_complexity": "low|medium|high",
  "recommendation": {
    "provider": "azure",
    "reason": "..."
  }
}
```

### 3.3 Strategist Agent

**System Prompt**

```
You are the Strategist agent. Your role is to explain business risk, vendor
lock-in, and compliance impacts. Use only provided metadata and scores.
Return JSON only.
```

**Expected Output Schema**

```json
{
  "agent": "strategist",
  "summary": "...",
  "confidence": "high|medium|low",
  "lock_in_risk": "low|medium|high",
  "business_impact": "low|medium|high",
  "recommendation": {
    "provider": "gcp",
    "reason": "..."
  }
}
```

## 4. Guardrails

- If `estimated_monthly_cost` is null for all equivalents, return
  `confidence: "low"` and explain that pricing data is missing.
- If `metadata` is missing, report the missing fields explicitly.
- Do not suggest splitting workload across multiple clouds unless explicit
  dependency metadata exists.
