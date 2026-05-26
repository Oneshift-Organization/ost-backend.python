# Capacity Pool Model Implementation Plan

Este documento detalha o plano técnico para migrar a pipeline de billing de uma abordagem SKU 1:1 para o **Capacity Pool Model** (Pool de Capacidade), lidando robustamente com descontos e garantindo integridade matemática.

## Fase 1: Domain & Entities

### 1. Atualizar Entidades de Domínio (`app/domain/entities/workload_pool.py`)
- Criar dataclass/Pydantic model `WorkloadPool` contendo:
  - `id`: Identificador único (ex: hash da pool key).
  - `project_id`: Identificador do projeto (GCP) ou account (AWS).
  - `family`: Família da máquina (ex: `E2`, `N2`, `M5`).
  - `region`: Região (ex: `us-central1`).
  - `capacity`: dict com `vcpus` e `ram_gib`.
  - `costs`: dict com `compute_base` (custos atrelados a Core/RAM + descontos) e `os_licensing`.
  - `auxiliary`: dict para métricas como `storage_gib`, `network_egress`, `gke_control_plane`.
- Criar a entidade `AggregationKey` para o agrupamento (ProjectID, MachineFamily, Region).

## Fase 2: Atualizar Ontologia/Metadata

### 1. Refatoração do `CloudSku` / Catálogo
- Atualizar banco de dados e enum para ter `component_type`:
  - Enums: `core`, `ram`, `os_license`, `storage`, `network`, `gke_control_plane`, `unclassified`.
- Refatorar o repositório de catálogos (ex: `PgCloudSkuRepository`) e as buscas para suportar a classificação de itens não apenas baseada na string, mas guardando a "role" (component_type) do SKU no banco.

## Fase 3: A Camada de Agregação (`billing_aggregator.py`)

### 1. Criar `app/services/billing_aggregator.py`
- Criar função central `aggregate_billing_data(enriched_lines: list[BillingLine], cloud_sku_catalog) -> tuple[list[WorkloadPool], dict]`.
- Lógica:
  1. Identificar custos órfãos/não mapeados (`unclassified`) e criar o bucket **Orphan**.
  2. Identificar janela de uso e calcular `n_distinct_days`.
  3. Agrupar linhas de Compute pelo `AggregationKey`.
  4. Para itens de Compute Core (`core`): 
     - Adicionar vCPU Capacity: `Sum(CoreUsage) / usage_hours_sum`
     - Adicionar Cost no `compute_base`.
  5. Para itens de RAM (`ram`):
     - Adicionar RAM Capacity: `Sum(RamUsage) / usage_hours_sum`
     - Adicionar Cost no `compute_base`.
  6. Para itens de Licença de OS (`os_license`):
     - Adicionar custo em `os_licensing`.
  7. Lidar com Descontos (SUD/CUD/negativos):
     - Atrelar diretamente ao `compute_base` do respectivo pool.
  8. Para itens Auxiliares: adicionar custos separadamente no bucket `auxiliary` e gerar a métrica `gke_control_plane_cost` agregada.

## Fase 4: Integração com LLM Orchestrator

### 1. Modificar payload enviado ao LLM (`app/infrastructure/llm/orchestrator.py`)
- Em vez de enviar a lista de SKUs, enviar a lista convertida para JSON do `WorkloadPool`.
- Estrutura JSON a enviar:
  ```json
  {
    "project_metrics": {
      "project_id": "...",
      "compute_pools": [
        {
          "family": "...",
          "region": "...",
          "capacity": {"vcpus": 10.5, "ram_gib": 42.0},
          "costs": {"compute_base": 1500.0, "os_licensing": 120.0}
        }
      ],
      "auxiliary_costs": {
        "gke_control_plane": 73.0
      },
      "orphan_costs": {"total": 50.0, "skus": ["SKU_1"]}
    }
  }
  ```

### 2. Atualizar o Prompt do LLM
- Adicionar instrução sobre os custos "Orphan" (não categorizados) para que o LLM não tente mapear e deduzir capacidade computacional deles.
- Atualizar o prompt para interpretar os Pools em vez de linhas individuais.

## Fase 5: Alertas e Governança

### 1. Regra de Limite de 5% de Orphan Cost
- No pipeline (ex: em `build_billing_enriched.py` ou `analysis_usecase.py`), logo após agregar:
  - Calcular: `orphan_ratio = orphan_cost / total_cost`
  - Se `orphan_ratio > 0.05`, criar uma flag de Warning no Job Analysis e logar com Structlog alertando a necessidade de curadoria da ontologia.

### 2. Mock Fallback para Custo Desconhecido (Sem Travar)
- Garantir que linhas de SKUs não reconhecidos são silenciosamente agrupadas em `orphan` sem levantar exceções, permitindo a continuidade do fluxo.

## Critérios de Aceite (DoR/DoD)
- [ ] Módulos de teste atualizados.
- [ ] Agregação lida corretamente com descontos negativos.
- [ ] Nenhuma linha é dropada: `sum(pools.costs) + sum(auxiliary) + sum(orphan) == total_csv_cost`.
- [ ] LLM recebe o novo formato reduzido e retorna a migração mapeando Pools para instâncias Target reais baseadas nas capacidades do pool.
