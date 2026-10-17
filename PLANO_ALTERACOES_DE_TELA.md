# PLANO_ALTERACOES_DE_TELA — mudanças de tela decididas, para o front

> Criado em 17/10/2026. Cada item diz o que a tela mostra, de onde vem o dado e quando
> está pronto. O backend entrega o dado; o front desenha.

| # | mudança | origem | estado |
|---|---|---|---|
| 1 | Premissa "1 vCPU = 1 vCPU" visível | decisão 2.7 (`PLANO_ESTRATEGIA_SPRINT_TERRAFORM` §2) | ⏳ |
| 2 | Estado da reanálise na linha da lista | correção da duplicidade (17/10) | ⏳ |
| 3 | Nome do billing (alias) no lugar do arquivo | melhoria M9 (17/10) | ✅ front `5be3b28` |

## 1. Premissa "1 vCPU = 1 vCPU" visível (decisão 2.7)

**Por quê.** O empacotamento casa vCPU e memória da origem com o destino sem ajuste de
desempenho entre gerações de processador. Uma máquina mais nova pode render mais por
vCPU, e isso mexe no maior número da análise, o compute.

Não há fonte confiável para esse ajuste. O ganho que os provedores publicam é número de
marketing, depende da carga e não se compara entre nuvens. Por isso o usuário decidiu
manter 1 para 1 e dizer isso na tela, não só no registro de premissas.

**Backend (pequeno):** campo novo no payload, aditivo.

```json
"premissas": {
  "desempenho_por_vcpu": {
    "metodo": "1:1",
    "texto": "Cada vCPU da origem vale uma vCPU no destino, qualquer que seja a geração do processador."
  }
}
```

O texto também chega ao LLM e ao agente, e a narrativa não contradiz a tela. Precisa de
nota ao MCP, como todo campo novo.

**Front:**
- **Diálogo de tipos de instância** (`components/dashboard/analysis-result/instance-types-dialog.tsx`):
  - onde cada pool da origem aparece com o que o substitui no destino;
  - uma linha de rodapé com o texto curto.
- **Etapa de recomendação** (`steps/step-recomendacao.tsx`): perto da economia mensal, um
  ícone de informação com o texto longo.

Textos:
- **Curto:** "Cada vCPU da origem vale uma vCPU no destino, qualquer que seja a geração do
  processador."
- **Longo:** "Comparamos máquinas pela quantidade de vCPU e de memória. Processadores de
  geração mais nova podem render mais por vCPU, e isso não entra na conta: não há fonte
  confiável para medir esse ganho entre nuvens. Se a carga for limitada por CPU, a
  necessidade no destino pode ser menor."

**Análise antiga, sem o campo:** o front mostra o mesmo texto. A regra sempre foi 1 para
1.

**Pronto quando:** uma análise nova e uma antiga mostram o texto nos dois lugares.

## 2. Estado da reanálise na linha da lista (complemento da correção de 17/10)

**Por quê.** A lista passou a ter uma linha por billing. Refazer a análise (anexar
inventário ou reanalisar) cria uma revisão, e a linha continua mostrando a vigente até a
nova concluir. Hoje a linha não diz que há uma atualização em andamento, nem que ela
falhou. O sino e o aviso de fim já funcionam (`analysis-watcher`).

**Backend:** pronto. Cada item de `GET /v1/analyses` traz:
- `reanalise_job_id`;
- `reanalise_status` (`PENDING`, `PROCESSING` ou `FAILED`);
- `reanalise_erro`.

**Front:** na lista de análises e no menu de análises recentes (`analyses-dropdown.tsx`):

| `reanalise_status` | selo na linha | ao passar o mouse |
|---|---|---|
| `PENDING`, `PROCESSING` | "Atualizando" | "A análise está sendo refeita. Esta versão vale até a nova terminar." |
| `FAILED` | "A atualização falhou" | `reanalise_erro`, com link para a revisão que falhou |
| vazio | nada | — |

**Pronto quando:** anexar inventário mostra "Atualizando" na linha do billing e, no fim,
a linha troca para a análise nova, sem aparecer uma segunda linha.

## 3. Nome do billing no lugar do arquivo (M9) — ✅ 17/10

**Backend:** `PUT /v1/billing-files/{job_id}/alias`; as listas e o status trazem
`alias` e `nome_exibicao` ([`docs/endpoints/billing.md`](docs/endpoints/billing.md)).

**Front (feito, `5be3b28`):**
- **"Renomear"** no menu de cada análise (lista e grade), com diálogo. O nome em
  branco volta ao nome do arquivo, que aparece como dica.
- `nome_exibicao` na lista, na grade, no menu de análises recentes, no aviso de
  análise concluída, na trilha, na linha do tempo e na confirmação de exclusão.
- A busca acha pelo nome dado e pelo do arquivo.
- O formato e o campo "Nome" em "Dados do arquivo" continuam vindo do arquivo enviado.
- **Na importação:** campo "Nome da análise" no modal, preenchido com o nome do
  arquivo; só vai como `alias` no upload se a pessoa mudar.

O nome é do billing: renomear vale para todas as revisões da análise dele.
