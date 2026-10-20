# Excluir conta — contrato para o front

## O endpoint

```http
DELETE /v1/account
Authorization: Bearer <jwt>
```

Sem corpo e sem parâmetros: a conta apagada é sempre a do token.

### Resposta `204`

Sem corpo. A conta e todos os dados dela deixaram de existir.

### Erros

| status | `codigo` | `retryable` | quando |
|---|---|---|---|
| `401` | — | — | token ausente ou inválido |
| `502` | `EXCLUSAO_DE_CONTA_FALHOU` | `true` | o Supabase não removeu o usuário |

No `502` **nada foi apagado**: os dados só saem do banco se o usuário também
sair do Supabase.

## O que é apagado

| tabela-raiz | o que cai junto (FK `ON DELETE CASCADE`) |
|---|---|
| `billing_reports` | `billing_items`, `billing_enriched`, `unmapped_billing_lines` |
| `analysis_jobs` | — |
| `terraform_generation_jobs` | — |
| `inventories` | `inventory_resources`, `inventory_relations` |

Depois o usuário é removido do Supabase Auth
(`DELETE {SUPABASE_URL}/auth/v1/admin/users/{id}`, com a `service_role`).

**Tabela nova com `user_id` e sem pai** precisa entrar em `_ROOT_MODELS`, em
[`pg_account_repo.py`](../../app/infrastructure/repositories/pg_account_repo.py).
Sem isso a exclusão deixa dado para trás.

## Decisões

- **Irreversível.** Diferente de [`disp-status`](disp-status.md), não há
  lixeira: é a saída definitiva do usuário.
- **Dados antes do usuário, na mesma transação.** Se o Supabase falhar, a
  exceção desfaz a exclusão dos dados e a conta fica inteira para nova
  tentativa.
- **Idempotente.** Usuário já removido no Supabase (`404`) conta como sucesso.
- **Configuração.** Exige `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`. Sem
  elas a rota responde `502` e não apaga nada.

## O que o front faz depois do `204`

Encerrar a sessão local do Supabase (`signOut`) e mandar o usuário para a
landing. O token antigo continua assinado até expirar, mas não há mais dados
nem usuário por trás dele.
