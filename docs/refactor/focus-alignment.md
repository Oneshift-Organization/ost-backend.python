# FOCUS Alignment & Capacity Pool Model

This document outlines the alignment of the Cloud Intelligence Platform with the **FinOps Open Cost and Usage Specification (FOCUS)** and the architectural transition to the **Capacity Pool Model**.

## 1. The Core Problem: 1:1 Matching vs. Cloud Billing Reality
Previous architectures assumed a 1:1 mapping between a billing CSV line and a Virtual Machine (e.g., one line = one `e2-standard-8`). 
**Reality:** Cloud providers (especially GCP) bill components atomically. A VM generates separate lines for `Core`, `RAM`, `OS License`, etc., aggregated at the Project level.

Attempting to match a "1 vCPU" component to a full AWS VM (vCPU + RAM) leads to mathematically flawed savings estimates.

## 2. The Solution: Capacity Pool Model
Instead of matching individual SKUs, the backend aggregates granular usage into logical **Capacity Pools** before equivalence matching or LLM inference.

### 2.1 Aggregation Keys (FOCUS Aligned)
Pools are grouped by:
- `ProjectId`
- `Region`
- `MachineFamily` (e.g., E2, N2, T3)

### 2.2 Concurrency over Raw Usage
Cloud bills in hours. To understand capacity, we calculate concurrency:
- `Hours in Window` = `UsageEnd` - `UsageStart` (Fallback to 730 hours for a full month)
- `vCPUs` = `Sum(CoreUsageHours) / Hours in Window`
- `RAM (GiB)` = `Sum(RamUsageGibHours) / Hours in Window`

### 2.3 Taxonomies and Buckets
Data is classified into Universal Buckets aligned with FOCUS `ServiceCategory` and `ResourceType`:
1. **Compute Pool (CPU/RAM):** Core and RAM usage.
2. **Compute Base (OS License):** Windows, RHEL, SLES, Ubuntu Pro. Count of licensed vCPUs.
3. **Containers (Control Plane):** EKS/GKE/OKE cluster management fees.
4. **Storage & Network:** Volumes, egress, etc.
5. **Unmapped (Orphan):** Unknown SKUs (taxes, support, edge cases) tracked for transparency but excluded from capacity calculations.

### 2.4 Discounts (SUD/CUD)
Negative costs (discounts) are applied **only to costs**, never to usage quantities. This preserves the mathematical integrity of the capacity calculation.

## 3. The New LLM Architecture
The LLM no longer receives raw SKUs. It receives structured `WorkloadPool` objects. The LLM acts as a Senior Cloud Architect, designing the optimal mix of target instances (e.g., AWS M5 or OCI Flex) to satisfy the aggregated pool capacity, preventing hallucinations and preserving factual pricing comparison.

### 3.1 FOCUS Column Mapping in our System

| FOCUS Column | Our Field | Provider Source |
|---|---|---|
| `ServiceCategory` | `category` (ontology) | Compute, Storage, Network, Database |
| `ServiceName` | `service_name` | Mapped from provider CSV |
| `ResourceType` | `service_type` | virtual_server, infrastructure_component, etc. |
| `SubAccountId` | `project_id` (new) | `raw_payload["Project ID"]` (GCP) |
| `SubAccountName` | — | `raw_payload["Project name"]` (GCP) |
| `SkuId` | `sku_id` | Provider-specific SKU ID |
| `RegionId` | `region` | Provider region field |
| `BilledCost` | `cost` | Decimal from parser |
| `EffectiveCost` | `cost` (post-discount) | `cost_discounted` when available |
| `BillingCurrency` | `currency` | ISO-4217 |
| `ConsumedQuantity` | `usage_amount` | Provider usage amount |
| `ConsumedUnit` | `usage_unit` | hour, gibibyte hour, etc. |
| `ChargeCategory` | `scope` | line_item, global_zero, tax |
| `Tags` | `tags` | raw_payload (provider metadata) |

## 4. Implementation Status

### Domain Entities (app/domain/entities/)
- `WorkloadPool` — capacity pool with PoolKey, PoolCapacity, PoolCosts ✅
- `AuxiliaryCosts` — GKE, storage, network, orphan ✅
- `BillingEnriched` — now has `project_id` field ✅

### Aggregation Engine (app/services/)
- `BillingAggregator` — classifies items, groups into pools, handles orphans ✅

### Pipeline Integration
- `BuildMigrationContextUseCase` — generates pools alongside workloads ✅
- `LLMOrchestrator` — receives pools JSON instead of raw SKUs ✅
- `MockLLMClient` — updated to parse pool-based payload ✅

### E2E Test Results (GCP Report 5d9bc55e)
- **3 compute pools** identified: E2 (29.5 vCPU / 118.1 GiB), N2, N1
- **Compute cost in pools**: $4,703.75 (16.9% of total)
- **Mapped (compute + aux)**: $13,394.82 (48.1%)
- **Orphan**: $14,430.31 (51.9%) — includes GKE, Cloud SQL, unclassified services
- **LLM output**: Migrate to AWS with ~$705/month savings estimate (directional, low confidence)

### Next Steps
1. Expand catalog coverage to reduce orphan rate below 5% threshold
2. Properly detect GKE control plane in classification
3. Implement `usage_start`/`usage_end` based hours window calculation  
4. Add subscription/commitment discount handling (SUD/CUD) 
5. Replace mock pricing with real pricing workers
