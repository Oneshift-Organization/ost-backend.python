# Depreciar análise — contrato para o front

**Substitui o `DELETE /v1/analysis-jobs/{job_id}` que foi pedido.** Ver §4 para o
porquê.

## O endpoint

```http
PUT /v1/analyses/{job_id}/disp-status
Authorization: Bearer <jwt>
Content-Type: application/json

{ "disp_status": 0 }
```

| campo | tipo | valores |
|---|---|---|
| `disp_status` | int | `0` = depreciada · `1` = ativa |

É o **único** campo aceito. O corpo usa `extra="forbid"`: qualquer outra chave
devolve `422`. A rota muda visibilidade e nada mais.

### Resposta `200`

```json
{
  "job_id": "62d67cd7-2b80-4e61-9e72-2f17505d262c",
  "disp_status": 0,
  "disp_status_updated_at": "2026-09-13T06:20:41"
}
```

### Erros

| status | `codigo` | quando |
|---|---|---|
| `404` | `ANALISE_NAO_ENCONTRADA` | não existe **ou** é de outro usuário |
| `422` | — | `disp_status` fora de `{0,1}`, ausente, ou campo extra |

O `404` para análise de outro usuário é deliberado: um `403` confirmaria que ela
existe.

**Idempotente.** Mandar `0` duas vezes devolve `200` das duas — o front pode
reenviar num retry sem tratar caso especial.

## O que muda na listagem

```http
GET /v1/analyses                              → só as ativas (default)
GET /v1/analyses?incluir_depreciadas=true     → todas, para uma tela de lixeira
```

**O front não precisa filtrar nada.** Depois do `PUT` com `0`, a análise já não
volta no `GET /v1/analyses`. É isso que faz a operação parecer exclusão.

Cada item da listagem passa a trazer `disp_status`, útil só na visão de lixeira.

## Fluxo sugerido na tela

1. Usuário clica em excluir → `PUT .../disp-status` com `{"disp_status": 0}`.
2. Recebeu `200` → tira o card da lista (ou refaz o `GET`).
3. Oferecer **desfazer** por alguns segundos → `{"disp_status": 1}`.

O passo 3 é o que um `DELETE` não permitiria, e é barato: mesma rota, outro
valor.

## Ciclo de vida completo

```
ativa ──PUT 0──> depreciada ──30 dias──> apagada do banco
  ^                   │
  └──────PUT 1────────┘
```

O expurgo é `scripts/expurgar_analises.py`, rodado por cron. Sem `--confirmar`
ele só lista o que faria: apagar é irreversível.

`disp_status_updated_at` é o que ancora a carência. Uma análise com
`disp_status = 0` e data nula **não** é expurgada — não há de quando contar.

## 4. Por que não é um `DELETE`

Apagar na hora levaria junto o `llm_input_payload`, que é a fonte de:

- a geração de Terraform (`POST /v1/terraform-generations` lê dele);
- qualquer reanálise ou reprocessamento futuro;
- a auditoria de como aquele número foi produzido.

Quem excluísse por engano perderia o pacote de Terraform que já tinha baixado,
sem nenhuma forma de voltar. Com a depreciação, o usuário tem um mês.

## 5. Efeito colateral que vale saber

A análise depreciada **libera o relatório** para uma nova análise, mesmo que ela
estivesse travada em `PENDING`. O índice de idempotência passou a considerar
`disp_status = 1`.

Sem isso, um job travado deixava o usuário num beco: ele "excluía" a análise da
tela e continuava sem conseguir rodar outra para o mesmo relatório, sem nenhuma
mensagem que explicasse por quê.
