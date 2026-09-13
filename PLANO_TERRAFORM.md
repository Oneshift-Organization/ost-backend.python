# Plano — geração de Terraform a partir da migração escolhida

**Base:** especificação do Ronaldo (11–12/09) + verificação contra o código real.
**Regra que continua valendo:** o caminho legado não é tocado. Tudo nasce ao lado,
atrás de flag, desligado.

> ## Status — 2026-09-13
>
> | bloco | estado |
> |---|---|
> | **0** — `sku_id` no payload | ✅ feito |
> | **0b** — object storage como categoria | ✅ feito |
> | **1** — schemas, entidade, tabela, migration | ✅ feito |
> | **2** — derivador determinístico | ✅ feito |
> | **3** — renderer GCP + ZIP | ✅ feito |
> | **4** — rotas + flag + reaper | ✅ feito |
> | **6** — `terraform fmt` | ✅ feito (`validate` fora do escopo, §6.1) |
> | **7** — procedência no pacote | ✅ feito |
> | **5** — blueprint pelo modelo | ⬜ pendente — **opcional por desenho** |
>
> O Bloco 5 é o único que falta, e o pacote funciona sem ele: o modelo entra
> para melhorar nomes lógicos e o texto do README, não para decidir
> infraestrutura. Precisa da `NVIDIA_API_KEY` no ambiente local para ser
> testado de verdade.
>
> **Antes de ligar em dev:** `alembic upgrade head` (tabela nova), re-seed de
> `sku_classification_rules` e `scripts/re_enrich_report.py` nos relatórios
> existentes (object storage mudou de balde), e `TERRAFORM_GENERATION_ENABLED=true`.

---

## 1. Veredito sobre a especificação recebida

### Endossado sem ressalva

| decisão dele | por que está certa |
|---|---|
| Endpoints e tabela próprios | Análise e Terraform têm ciclos de vida diferentes. `analysis_jobs` não deve carregar arquivo `.tf`. |
| **A IA não escreve HCL** | Ela compõe um blueprint, o Pydantic valida, templates nossos escrevem. É a separação epistêmica do `PLANO_API_V2` aplicada a outro domínio. |
| Flag nascendo `false` com 404 | Mesmo padrão do `AGENT_FLOW_ENABLED`, já em produção. |
| Sem `plan`, sem `apply`, sem credencial de nuvem | A plataforma gera código, não provisiona. |
| Allowlist de recursos, sem `shell=True`, sem provisioners, timeout | Correto e suficiente. |
| **Faltou informação → vira variável + `assumptions`, nunca valor inventado** | É a regra `SEM_BASE` do plano anterior. É a linha mais importante do documento dele. |
| Reutilizar a NVIDIA já integrada | Verificado: `nvidia_client.py` existe em `develop`, com `temperature=0`, `response_format=json_object` e `enable_thinking=False` — exatamente a configuração que ele propôs. O `_extract_json` dele já trata JSON cercado por crase. |

### Corrigido

| ponto | o que muda |
|---|---|
| "Reconstruir o `MigrationContext` pelo `report_id`" | **Não.** Recalcular é caro e pode dar números **diferentes dos que o usuário aprovou na tela** (preço muda, câmbio muda, catálogo muda). A fonte é o `llm_input_payload` já gravado no job. |
| Envelope `{codigo, message, retryable}` | Nosso contrato é `mensagem`, em português, já em produção nas rotas de agente. |
| `TERRAFORM_MAX_OUTPUT_TOKENS` | Hoje `_MAX_OUTPUT_TOKENS = 16384` é constante de módulo no `nvidia_client.py`. Precisa ser parametrizado. |
| Object Storage no MVP | **Não é derivável.** `AuxiliaryCosts` tem quatro categorias: `storage_ssd`, `storage_hdd`, `network_egress`, `database`. Não há `object_storage`. Fora do escopo até alguém criar a categoria na agregação. |
| `BackgroundTasks` sem mais nada | Precisa do reaper desde o dia um. Já pagamos esse preço com `analysis_jobs`: restart deixa job preso em `PROCESSING`. |
| `terraform init` no pod | Baixa o provider do GCP (>200 MB) a cada geração. Exige `TF_PLUGIN_CACHE_DIR` em volume ou mirror, mais egress para a HashiCorp. Ver §6, decisão pendente. |
| Subprocess no processo web | `terraform` roda no mesmo processo que atende requisição. Tem que ir para executor. |
| 20 passos até segunda | Não cabe. Ordem de blocos em §5 põe algo demonstrável cedo. |

---

## 2. A decisão arquitetural central

O desenho dele é:

```
análise → NVIDIA → blueprint → templates → .tf
```

O problema: **a maior parte do blueprint não é opinião, é conta.** Mandar o modelo
"compor a infraestrutura" a partir de dados que já determinam a resposta convida
exatamente o erro que o resto do desenho tenta evitar — e torna a saída
não-determinística, porque duas execuções podem agrupar diferente.

O desenho proposto:

```
análise → DERIVAÇÃO (backend, pura)  ─┐
                                      ├→ merge com autoridade → templates → .tf
          NVIDIA (só o julgamento)  ─┘
```

### Quem decide o quê

Reusa as categorias de `domain/services/authority.py`:

| informação | categoria | quem decide | de onde sai |
|---|---|---|---|
| provedor de destino | — | **usuário** | request |
| região, CIDR, ambiente, nome do projeto | — | **usuário** | request |
| shape da instância (`n2-standard-8`) | `DERIVACAO` | **backend** | `EquivalentOption.sku_id` (§3) |
| quantidade de instâncias | `DERIVACAO` | **backend** | `equivalentes[].instancias` |
| vCPU / RAM por instância | `DERIVACAO` | **backend** | `correspondencia_tecnica` |
| GB de disco | `ESTIMATIVA` | **backend, com premissa** | custo ÷ taxa `gb_month` |
| vCPU de banco gerenciado | `ESTIMATIVA` | **backend, com premissa** | custo ÷ taxa `db_vcpu_month` |
| topologia de rede | `SEM_BASE` | **usuário** | o bill não contém rede |
| engine e versão do banco | `SEM_BASE` | **variável + assumption** | não existe no bill |
| nomes lógicos, agrupamento, ordem | `JULGAMENTO` | **modelo** | NVIDIA |
| o que comentar no README | `JULGAMENTO` | **modelo** | NVIDIA |

**Por que isso importa:** com a derivação no backend, se a NVIDIA estiver fora do
ar ainda sai um `.tf` válido — com nomes genéricos, mas correto. O modelo deixa
de ser ponto único de falha para virar melhoria de qualidade. E o mesmo relatório
gera sempre o mesmo `.tf`, que é o mínimo que se espera de um artefato que o
cliente vai aplicar na infraestrutura dele.

---

## 3. Bloco 0 — o `sku_id` precisa chegar ao payload

**Pré-requisito de tudo. Sem ele não há `machine_type`.**

O matcher calcula o shape e o guarda em `EquivalentOption.sku_id`
(`equivalence_matcher.py:51`) — `m6i.2xlarge` na AWS, `n2-standard-8` no GCP,
vindos do `sku_key` do `cloud_price_points`. Mas `EquivalentService`
(`domain/analysis.py:53`) **não tem esse campo**, e as quatro conversões em
`build_migration_context.py` (linhas 223, 399, 478, 495) o descartam em silêncio.

O que sobra no payload é `nome_servico`, que é rótulo humano — `"Amazon EC2"`,
`"Compute Engine N2"` — não shape.

É a mesma falha que a perda de proveniência de preço: dois dataclasses parecidos
em módulos diferentes, e a conversão entre eles some com um campo sem avisar.

**Trabalho:** campo novo em `EquivalentService`, propagação nas quatro conversões,
emissão no `_serialize_equivalent`, e um teste de caracterização fixando que o
payload carrega o shape. Aditivo — nenhum consumidor atual quebra.

---

## 4. O que o `.tf` precisa e a análise não tem

Registrado aqui para não ser descoberto tarde:

| falta | saída |
|---|---|
| rede (VCN/VPC, subnet, security list) | usuário informa CIDR; o resto é template |
| região de destino real | usuário escolhe — **mas a cotação usa região fixa** (`us-central1` no GCP, dívida §10.4 do `PLANO_API_V2`). Consequência: **o README gerado não pode citar economia**, ou cita número de outra região. |
| imagem de SO, chave SSH, tags | variáveis com default vazio |
| engine e versão do banco | variável + assumption |
| object storage | fora do MVP (§1) |

**O risco central:** o `.tf` é um artefato que *parece* preciso. Onde estimamos,
ele vai parecer afirmação. Por isso o Bloco 7.

---

## 5. Blocos, em ordem, com o porquê

Ordenados para que cada bloco seja demonstrável sozinho e o risco caia cedo.

| # | bloco | por que nesta posição | dep. |
|---|---|---|---|
| **0** | `sku_id` no payload | sem shape não existe `machine_type`; bloqueia todo o resto | — |
| **1** | Schemas, entidade, tabela, migration, repo | contrato antes de código; é o passo 1 dele e não depende de decisão nenhuma | — |
| **2** | Derivador determinístico (puro, sem LLM) | é a maior parte do blueprint e a que dá para testar de verdade | 0, 1 |
| **3** | Templates GCP + renderer + ZIP | **aqui já existe `.tf` para mostrar**, sem IA nenhuma no caminho | 2 |
| **4** | Rotas + flag + reaper | fecha o fluxo ponta a ponta pelo HTTP | 1, 3 |
| **5** | Blueprint pelo modelo, restrito ao julgamento | melhora nomes e narrativa; opcional por desenho | 3 |
| **6** | `terraform fmt` + `validate` | precisa de decisão de infra (§6) | 3 |
| **7** | Procedência no pacote gerado | o que impede o `.tf` de passar por fato | 3 |

### Bloco 2 — o derivador

`app/domain/services/terraform_plan.py`, função pura: entra o `llm_input_payload`
e as escolhas do usuário, sai a lista de recursos derivados + premissas tipadas.

Sem I/O, sem framework, testável contra os dois CSVs reais que já estão
versionados. É onde mora a conta de GB (`custo ÷ taxa gb_month`) e a de vCPU de
banco, cada uma emitindo a premissa que a sustenta.

### Bloco 5 — o que o modelo faz

Recebe os recursos **já derivados** e preenche só: `logical_name` legível,
agrupamento, ordem de `depends_on`, texto do README. O merge usa a mesma lógica
de `aplicar_autoridade` — campo derivado vence, campo de julgamento é preservado.

Se o modelo devolver um recurso que não estava na derivação, ele é descartado e
vira `warning`. Se devolver número, é ignorado.

### Bloco 7 — procedência

O ZIP leva, no `README.md` e no `manifest.json`: qual relatório, qual análise,
origem e **idade** de cada preço usado, a taxa de câmbio **com data**, o nível de
confiança da análise, e a lista de `assumptions` separada por categoria
(derivado / estimado / sem base).

Já temos tudo isso no `premissas` do payload — inclusive o alerta de câmbio que
entrou semana passada. É só não jogar fora.

---

## 6. Decisões que preciso de você

**1. `terraform validate`: CLI na imagem da API, ou fora do MVP?**

Pôr o binário na imagem significa: +80 MB, um executável que roda subprocess
dentro do pod que atende requisição, e `terraform init` baixando >200 MB de
provider por geração — com egress para a HashiCorp que o cluster pode não ter.

Alternativas: (a) sem `validate` no MVP, só `fmt` — que é puro formatador e não
precisa de rede; (b) validar num job separado; (c) imagem com provider
pré-baixado em `TF_PLUGIN_CACHE_DIR`.

Minha recomendação: **(a) para a demo, (c) depois.** O `.tf` sai formatado e
correto por construção — os templates são nossos, não do modelo.

**2. Região.** Hoje cotamos só `us-central1` no GCP. Se o cliente escolher
`southamerica-east1`, o `.tf` sai certo mas a economia citada é de outra região.
Aceita que o README **não cite economia** no MVP, ou prefere restringir a escolha
de região à que cotamos?

**3. Object storage.** Fora do MVP (não é derivável), ou você quer que eu crie a
categoria na agregação antes? É trabalho de agregação, não de template.

**4. Prazo real.** O "até segunda" era da mensagem de 11/09. Confirma se a demo é
amanhã — muda o que vale atacar.

## 7. O que preciso que você providencie

| item | para quê | sem isso |
|---|---|---|
| `NVIDIA_API_KEY` no meu `.env.dev` | testar o Bloco 5 de verdade | blocos 0–4 e 6–7 seguem normalmente |
| Acesso ao Postgres de dev (`kubectl` port-forward) | rodar a suíte de integração | 60 caracterizações continuam pulando aqui; só o CI as roda |
| Confirmar `AGENT_FLOW_ENABLED` em dev | não é do Terraform, é a pendência anterior | — |

## 8. Dependências do Ronaldo

**Nenhuma.** A geração de Terraform não depende de `submit_billing_analysis` nem
de qualquer tool do MCP. O fluxo é `Frontend → API`, e a análise que serve de
entrada já existe no banco.

A única interseção é futura: se o prompt do Terraform for para o MCP como
`04_SH1FT_TERRAFORM_ENGINEER`. Para o MVP ele fica no backend, como ele mesmo
propôs — e com uma ressalva que ele antecipou sem saber que já era caso: a tool
que o backend consome hoje é `get_all_prompts`, que traz **todos**. Quando isso
migrar, vai precisar de uma tool seletiva, não só de um prompt novo.

## 9. Fora do escopo dele, que eu acrescento

1. **Bloco 0** — o `sku_id` perdido. Ele não sabia que faltava.
2. **Bloco 2** — derivação determinística antes do modelo. Muda o desenho.
3. **Bloco 7** — procedência no pacote. Sem isso o `.tf` vira afirmação.
4. **Reaper** para `terraform_generation_jobs`.
5. **Envelope em português**, alinhado ao contrato que já está em produção.
6. **Executor** para o subprocess.
7. O aviso de que o README **não pode citar economia** enquanto a cotação for de
   uma região só.
