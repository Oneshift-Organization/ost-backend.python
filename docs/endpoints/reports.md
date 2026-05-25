# Endpoints `/v1/reports`

Agregações e dashboards sobre dados de billing normalizados.

> Convenções gerais: [`api-conventions.md`](../api-conventions.md). Schema dos
> dados fonte: [`data-model.md`](../data-model.md).

Toda rota aqui exige `Authorization: Bearer <jwt>` (bypass em dev via
`AUTH_ENABLED=false`).

---

## `GET /v1/reports/billing-dashboard`

Dashboard consolidado do usuário: custo por provedor, por serviço e evolução
mensal.

### Query params

| Param | Default | Range | Descrição |
|---|---|---|---|
| `from_period` | — | `YYYY-MM` | Início do período (inclusivo). Opcional. |
| `to_period` | — | `YYYY-MM` | Fim do período (inclusivo). Opcional. |
| `top_services_limit` | 10 | 1..100 | Quantos serviços no topo por custo. |

### Response — 200 OK

```json
{
  "cost_by_provider": [
    { "label": "aws", "total": "1369.516505" },
    { "label": "azure", "total": "45.230000" }
  ],
  "cost_by_service": [
    { "label": "EC2", "total": "800.500000" },
    { "label": "S3", "total": "569.016505" }
  ],
  "monthly_trend": [
    { "label": "2026-03", "total": "500.000000" },
    { "label": "2026-04", "total": "1369.516505" }
  ],
  "total_cost": "1869.516505"
}
```

`total_cost` e todos os `total` em cada array são **strings** (preservam
precisão `Decimal`). Cliente que faz aritmética deve converter para
`BigDecimal`/`Decimal` na sua linguagem, nunca `float`.

### Ordenação

- `cost_by_provider`: maior custo primeiro.
- `cost_by_service`: maior custo primeiro (top spenders).
- `monthly_trend`: ordem cronológica crescente.

### Erros

| Status | Quando |
|---|---|
| 401 | Token ausente ou inválido |
| 422 | `from_period`/`to_period` fora do formato `YYYY-MM`, ou `top_services_limit` fora do range |

### Exemplo curl

```bash
curl -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/v1/reports/billing-dashboard?from_period=2026-01&to_period=2026-04&top_services_limit=5"
```

---

## Documentação relacionada

- [`../api-conventions.md`](../api-conventions.md) — serialização Decimal,
  paginação, status codes.
- [`../data-model.md`](../data-model.md) — schema dos `billing_items`.
- [`billing.md`](billing.md) — upload e ingestão de arquivos de billing.
