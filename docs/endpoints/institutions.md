# Instituições — colaboração por publicação explícita

## Decisões e limites

- Novas rotas `/v1/institutions`; os filtros pessoais de billing, análises,
  inventário, MCP e Terraform não foram ampliados nem substituídos.
- Uma instituição compartilha **resumos publicados**, não faturas ou inventário.
  Não há migração automática das análises existentes para uma instituição.
- O próprio dono publica uma análise concluída, escolhendo a instituição.
  A cópia contém scores e custos já calculados, recomendação, cenários e
  ressalvas. Não dispara IA, reanálise ou recálculo e não concede ações de migração.
- A publicação é imutável e única por `(institution_id, source_job_id)`;
  repetir o envio devolve a mesma cópia. Uma nova análise exige nova publicação.
- Novas publicações usam `institution-dashboard-v2`, com projeção explícita dos campos.
  Prompts, inventário e fatura bruta não são copiados. O score determinístico e
  suas ausências têm precedência sobre o score legado na apresentação.
- `dashboard.agent_views` contém os três pareceres estruturados; `dashboard.workload_strategies`
  contém os 7 R’s por recurso, destino, justificativa e pendências. Não inclui evidências
  brutas, IDs de inventário, fingerprint ou comandos. Nomes de workloads e os textos
  dos pareceres fazem parte do conteúdo explicitamente compartilhado.
- Publicações `institution-summary-v1` continuam imutáveis e legíveis. Não há backfill
  nem leitura da análise pessoal ao consultar o painel. Para incluir os novos detalhes,
  o autor retira e publica novamente, com confirmação do escopo ampliado. Repetir POST
  sem retirar a publicação continua retornando a mesma cópia.
- Esta evolução só amplia o JSONB publicado; não exige nova migration nem chamada de IA.
- Nenhuma soma de publicações: diferentes análises podem cobrir a mesma fatura.

## Acesso

JWT Supabase obrigatório. Cada operação verifica a associação de `claims.sub`
à instituição. Sem associação: **404**, inclusive para IDs de publicações e
convites. Membro sem permissão para uma operação administrativa: **403**.
Esta é uma exceção **aditiva** à consulta pessoal por `user_id`, não uma
permissão para usar o ID de outro usuário nas APIs existentes.

| Ação | Administrador | Membro |
|---|---|---|
| Consultar instituição, membros e resumos | Sim | Sim |
| Publicar análise própria | Sim | Somente com `can_publish=true` |
| Retirar publicação | Qualquer publicação da instituição | Somente a própria |
| Gerar/revogar/listar convites; mudar perfis/permissões | Sim | Não |
| Renomear/excluir instituição; remover membros | Sim | Não |
| Sair da instituição | Se outro administrador permanecer | Sim |

`profile` é apenas o foco de apresentação: `administrative`, `financial`,
`strategic`, `economist`, `technical`. Não concede permissões. Um administrador
pode pré-visualizar qualquer foco. Não há visões personalizadas por IA.

## Contratos

Todas as coleções usam `limit` (1–100, padrão 50) e `offset >= 0`, com ordem
estável. Respostas não são cacheáveis. Nomes são limitados a 120 caracteres;
nome de exibição a 80. O nome é informado pelo usuário e não é identidade verificada.

| Método e sufixo | Corpo | Resultado |
|---|---|---|
| GET `/institutions` | — | Instituições do usuário, com sua associação |
| POST `/institutions` | `{name, display_name}` | 201, instituição e associação administrativa |
| POST `/institutions/memberships` | `{code, display_name}` | 201, associação de consulta |
| GET/PATCH/DELETE `/institutions/{id}` | PATCH: `{name}` | detalhe / 204 / 204 |
| GET `/institutions/{id}/members` | — | membros, sem e-mails nem tokens |
| PATCH `/institutions/{id}/members/{user_id}` | `{role, profile, can_publish}` | 204 |
| DELETE `/institutions/{id}/members/{user_id}` | — | 204 |
| GET/POST `/institutions/{id}/invites` | — | lista / 201 com código uma única vez |
| DELETE `/institutions/{id}/invites/{invite_id}` | — | 204, revoga convite |
| GET/POST `/institutions/{id}/publications` | POST: `{job_id}` | lista / 201 com cópia |
| GET/DELETE `/institutions/{id}/publications/{publication_id}` | — | cópia / 204 |

Campos extras em requisições são recusados. O cliente não fornece `user_id`,
snapshot ou permissão ao entrar. Convites usam 192 bits aleatórios, validade
de uma hora e uso único; o banco guarda somente SHA-256. ID da instituição
não é um convite. Convites usados, vencidos, revogados ou inexistentes têm
a mesma resposta 404. Remover/destituir o criador revoga seus convites pendentes.

## Concorrência e ciclo de vida

Operações por instituição usam lock da linha pai, com nova verificação da
associação após o lock. Isso serializa consumo de convite, retirada de acesso,
último administrador e publicação. Constraints garantem associação única e
publicação única. Entrada/criação e exclusão de conta também usam lock
transacional por usuário para não criar associações durante a limpeza.

- Sair/remover membro revoga acesso, mas preserva os resumos já publicados.
  O diálogo de publicação e o de saída informam essa retenção.
- Excluir instituição elimina associações, convites e cópias por cascade;
  **não elimina nenhuma análise pessoal**.
- Excluir a conta elimina suas associações, convites e publicações. O último
  administrador recebe **409** até transferir a administração ou excluir a
  instituição. Essa proteção foi aprovada explicitamente pelo usuário.
  É a única extensão comportamental do endpoint existente `DELETE /v1/account`.
- Revogação impede novas leituras; não apaga cópias que um membro tenha salvo
  fora da plataforma enquanto possuía acesso.

## Publicação em develop

1. Integrar a develop atual nas branches e passar gates de isolamento e CI.
2. Revisar/aprovar o backend; aplicar a migration `b9c0d1e2f3a4` **antes** de
   ativar a imagem nova. Ela cria quatro tabelas, sem backfill ou mudança nas
   tabelas pessoais. Conferir o head caso novas migrations tenham sido integradas.
3. Publicar backend, verificar health e rotas autenticadas.
4. Publicar frontend; validar com duas contas de teste e uma terceira externa:
   criar → convidar → entrar → publicar → consultar → revogar → receber 404.
5. Verificar preservação das análises pessoais e exclusão bloqueada do último admin.

Rollback da aplicação: voltar imagens anteriores e manter as tabelas.
**Não executar downgrade depois de haver dados sem plano de preservação**:
ele apaga as instituições e suas cópias. Não altera análises pessoais.

## Testes

- Unitários: separação perfil/permissão, projeção/imutabilidade, ausência de
  score, validação de payload, paginação, no-store e mapeamento de erros.
- Integração PostgreSQL: isolamento entre usuários/instituições, convite
  expirado/revogado/usado, publicação de análise alheia/em andamento, retirada,
  exclusão/limpeza e concorrência de convite e último administrador.
- UI: cadastro continua com OAuth; a escolha institucional vem imediatamente
  após autenticar e é opcional. A ajuda reutiliza os guias existentes; links
  antigos de exportação continuam válidos.
