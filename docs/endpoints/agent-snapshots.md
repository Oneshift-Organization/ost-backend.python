# Endpoints `/v1/agent/snapshots` e `/v1/agent/submissions`

O caminho do agente (MCP): ler um **snapshot** gravado do payload
determinístico e enviar a análise **apontando para ele**. O envio é corrigido
contra exatamente o que o agente leu, nunca contra "a última análise" do
relatório, e não toca nas análises feitas pela tela.

> Plano, decisões e revisões: [`PLANO_SNAPSHOT_AGENTE.md`](../../PLANO_SNAPSHOT_AGENTE.md).
> Convenções gerais: [`api-conventions.md`](../api-conventions.md). Tabelas:
> [`data-model.md`](../data-model.md#snapshot-do-agente).

Toda rota exige `Authorization: Bearer <jwt>` e só existe com
**`AGENT_SNAPSHOT_ENABLED=true`** e `AGENT_FLOW_ENABLED=true`. Com qualquer um
desligado, responde `404 SNAPSHOT_DESLIGADO`. Isolamento por usuário: snapshot
ou envio de outro usuário responde 404, igual a "não existe".

Erros no envelope da API, em `detail`: `{"codigo", "mensagem", "retryable"}`.
Erro de validação do corpo vem no formato padrão do FastAPI (`422`, `detail` em
lista) e não é repetível.

## Fluxo

```
GET  /v1/billing-files                                  → report_id
POST /v1/agent/snapshots {report_id, inventory_id?}     → snapshot_id + payload
POST /v1/agent/snapshots/{snapshot_id}/submissions      → análise corrigida
GET  /v1/agent/submissions/{submission_id}              → recuperar um resultado
GET  /v1/agent/submissions?report_id=|snapshot_id=      → revisões
```

---

## `POST /v1/agent/snapshots`

Monta o payload determinístico (o mesmo do `GET /v1/analyses/{id}/request`
recalculado, sem LLM), grava e devolve. **Grava: não é uma leitura.**

```json
{"report_id": "…", "inventory_id": null}
```

**`201`** cria; **`200`** reaproveita (mesmo corpo de resposta):

```jsonc
{
  "snapshot": {
    "snapshot_id": "…", "report_id": "…", "inventory_id": null,
    "fingerprint": "…",           // SHA-256 de {versions, payload completo}
    "payload_fingerprint": "…",   // SHA-256 do JSON canônico do "payload" abaixo
    "versions": {"contract": "agent-snapshot-v1", "decision_policy": "score-7rs-v3",
                 "topology": null, "authority": "agent-authority-v2"},
    "created_at": "…", "expires_at": "…", "reused": false
  },
  "payload": { /* as chaves do /request; topologia compacta */ },
  "pools_sem_equivalente": []
}
```

- **Reaproveitamento:** mesmo usuário, `report_id`, `inventory_id` e
  `fingerprint` devolvem o mesmo `snapshot_id`, com a validade (24 h) renovada,
  **mesmo que estivesse vencido**: fingerprint igual significa conteúdo e
  versões iguais aos atuais. Qualquer diferença (preço, câmbio, reprocessamento,
  declarações do inventário, versão do backend) gera outro snapshot.
- **Representação entregue vs. snapshot interno:** o agente recebe a topologia
  compacta; o backend guarda o payload completo, com o detalhe de rede.
- **JSON canônico:** chaves ordenadas, sem espaços, UTF-8.

| Status | `codigo` | `retryable` |
|---|---|---|
| 404 | `SNAPSHOT_DESLIGADO` | não |
| 404 | `REPORT_NAO_ENCONTRADO` | não |
| 409 | `REPORT_NAO_PRONTO` (+ `status_report`) | sim |
| 422 | `REPORT_FALHOU` (+ `status_report`) | não |
| 404 ou 422 | `CONTEXTO_INDISPONIVEL` | não |
| 404 | `INVENTARIO_DESLIGADO` | não |
| 404 | `INVENTARIO_NAO_ENCONTRADO` | não |
| 409 | `INVENTARIO_NAO_PROCESSADO` | sim |
| 422 | `INVENTARIO_DE_OUTRA_NUVEM` | não |

## `POST /v1/agent/snapshots/{snapshot_id}/submissions`

O corpo de `POST /v1/agent/analyses/{id}/result` mais o campo opcional
`parecer`:

```jsonc
{
  "recommendation": "…", "recommendation_rationale": "…",
  "strategic_notes": [], "action_plan": [], "sugestoes": [],
  "payback": null, "provider_comparison": [],
  "parecer": {"posicao": "diverge", "motivo": "…", "riscos": ["…"]}
}
```

**Payback.** Com `payback.premissa_custo_migracao_usd`, o backend calcula
`payback.meses = ⌈premissa ÷ economia mensal do provedor selecionado⌉` e
sobrescreve o `meses` enviado (conta em `campos_sobrescritos`). A resposta traz
`economia_mensal_usd` e `calculado_pelo_backend: true`. Sem economia,
`meses: null` e `motivo_backend`. Desde `agent-authority-v2`.

`parecer.posicao` é `concorda` ou `diverge` em relação a
`decision_support.selected_provider`; `motivo` tem de 1 a 2.000 caracteres;
`riscos`, até 20 itens de até 300. O parecer **não passa pela autoridade** e
volta intacto em `analysis.parecer_agente`.

**Idempotência pelo conteúdo:** o mesmo corpo validado no mesmo snapshot é o
mesmo envio. Repetir depois de timeout ou reinício é seguro. Corpo diferente é
uma revisão nova (1, 2, 3…). Ordem das verificações:

1. snapshot do usuário (lock de linha) → senão `404`;
2. **mesmo corpo já gravado → `200` com o gravado e `Idempotent-Replayed: true`,
   mesmo com o snapshot vencido ou desatualizado**;
3. vencido → `410`;
4. versão, inventário ou relatório mudaram → `409`;
5. aplica a autoridade sobre o payload do snapshot e grava → `201`.

```jsonc
{
  "submission": {
    "submission_id": "…", "snapshot_id": "…", "report_id": "…", "revision": 1,
    "fingerprint": "…", "request_hash": "…", "versions": { … },
    "campos_sobrescritos": 14, "created_at": "…"
  },
  "analysis": { /* análise corrigida; é ela que o agente narra */ }
}
```

| Status | `codigo` | Quando |
|---|---|---|
| 404 | `SNAPSHOT_DESLIGADO` | flags desligadas |
| 404 | `SNAPSHOT_NAO_ENCONTRADO` | não existe, de outro usuário, ou relatório apagado |
| 410 | `SNAPSHOT_EXPIRADO` | vencido, e o corpo não foi enviado antes |
| 409 | `SNAPSHOT_DESATUALIZADO` | `motivo`: `versao` (+ `versoes_divergentes`), `inventario` ou `relatorio` |
| 422 | (FastAPI) | corpo inválido, por exemplo `payback` sem premissa |

Nenhum é repetível. O envio **não** percebe sync de preço ou reprocessamento
administrativo dentro da validade: é corrigido contra o snapshot, que continua
coerente com o que o agente leu.

## `GET /v1/agent/submissions/{submission_id}`

O mesmo corpo da resposta do envio. `404 ENVIO_NAO_ENCONTRADO` quando não
existe ou é de outro usuário.

## `GET /v1/agent/submissions`

`report_id` e/ou `snapshot_id`, `limit` de 1 a 100 (20). Do mais recente para o
mais antigo, com as revisões. Cada item:

```jsonc
{
  "submission": { /* o mesmo bloco do envio */ },
  "analysis_summary": {"recommended_provider": "OCI", "recommendation": "…",
                       "analysis_confidence": "medium", "sugestoes": 3}
}
```

## Garantias e manutenção

- Concorrência no banco: reaproveitamento por `INSERT … ON CONFLICT` no índice
  de conteúdo; envio com `SELECT … FOR UPDATE` no snapshot e unicidade de
  (`snapshot_id`, `request_hash`) e de (`snapshot_id`, `revision`).
- Nenhuma análise de `/v1/analyses` é alterada. Snapshots e envios não aparecem
  na lista de análises da tela e não geram Terraform (decisão de produto
  pendente).
- A varredura periódica apaga snapshots vencidos há mais de
  `AGENT_SNAPSHOT_RETENTION_DAYS` (7) **sem envio**. Com envio, ficam.
- Apagar o relatório (ou a conta) apaga snapshots e envios em cascata.
- A rota antiga `POST /v1/agent/analyses/{report_id}/result` está deprecated:
  grava por cima da análise da tela. Só com relatório de teste.

## Reproduzir

```bash
PYTHONPATH=. python scripts/gerar_mocks_billing.py --destino /tmp/mocks
PYTHONPATH=. python scripts/gerar_mocks_inventario.py --billing /tmp/mocks --destino /tmp/inv
python scripts/e2e_snapshot_agente.py --url http://localhost:8000 \
    --mocks /tmp/mocks --inventarios /tmp/inv --exemplos /tmp/exemplos
```
