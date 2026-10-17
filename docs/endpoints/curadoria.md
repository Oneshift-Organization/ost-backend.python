# Curadoria de nomes de serviço — `/v1/admin/service-name-suggestions`

Fatura como fonte, etapa A (decisão 2.11 de 17/10/2026). As faturas trazem nomes de
serviço que o DE-PARA não reconhece; a plataforma junta esses nomes numa fila e sugere a
linha do DE-PARA mais parecida. **Quem aprova é a curadoria da OneShift**, nunca o
cliente: o DE-PARA é de todos, e a sugestão nasce das faturas de vários clientes.

O console é a própria tela do Swagger (`/docs`, grupo "curadoria").

## Quem acessa

| configuração | efeito |
|---|---|
| `CURADORES` | e-mails da OneShift, separados por vírgula. Só eles acessam as rotas abaixo; os outros recebem `403 ACESSO_DE_CURADORIA` |
| `CURADORIA_USUARIOS_EXCLUIDOS` | UUIDs de contas de teste, cujas faturas não entram na coleta. Arquivo com "mock" no nome também fica fora |
| dev local com `AUTH_ENABLED=false` | o usuário local é curador |

## Rotas

```http
POST  /v1/admin/service-name-suggestion-collections      → lê as faturas e atualiza a fila (201)
GET   /v1/admin/service-name-suggestions?status_da_sugestao=pendente&provider=aws
PATCH /v1/admin/service-name-suggestions/{id}            → decide
```

Corpo da decisão:

```json
{ "status": "aprovada", "alvo": "Amazon MSK", "observacao": "nome longo do MSK" }
```

| `status` | exige | efeito |
|---|---|---|
| `aprovada` | `alvo`: uma linha do DE-PARA do mesmo provedor (senão `422 ALVO_FORA_DO_DE_PARA`) | o nome vira apelido da linha: as análises novas reconhecem o serviço (tipo, equivalentes, lock-in) |
| `recusada` | — | sai da fila e não volta na coleta seguinte (marketplace, serviço sem par) |
| `pendente` | — | devolve à fila |

## O que a fila guarda

Só o nome, o provedor, quantas faturas e quantos clientes distintos o trazem, a sugestão
e a decisão. Nenhum identificador de cliente, conta, custo ou recurso.

## O que entra na fila

Um nome que nenhum caminho da análise reconhece:
- linha do DE-PARA;
- apelido fixo ou aprovado;
- dica de tipo;
- escolha pelo uso (nome ambíguo como "Storage");
- componente financeiro.

A sugestão é a linha do DE-PARA mais parecida (palavras em comum, ou a sigla da linha
batendo com as iniciais do nome), com semelhança de pelo menos 0,6
(`app/domain/services/sugestao_de_nome.py`).

## Efeito na análise

O apelido aprovado vale para análises **novas**. As antigas não mudam, porque o payload
é congelado. Aprovar muda o tipo do serviço, e com ele os equivalentes, o lock-in e o
score. O custo total não muda.
