# PLANO_PRECO_REGIONAL — preço por região, preços oficiais, equivalência de família e payback

> ## Status — 2026-10-12
>
> | Item | Estado |
> |---|---|
> | R1 — regiões: zonas, região da linha, escolha do preço | ✅ `1c3620e` |
> | R2 — pools por região real | ✅ `220b485` |
> | R3 — coleta por região e preços oficiais de categoria (AWS, GCP, Azure, OCI) | ✅ `f3b8f27`, `14c3fc1`, `964d893` |
> | R4 — cotação regional no matcher e nas categorias, com proveniência | ✅ `5002a29`, `65bf27d` |
> | R5 — equivalência de família: arquitetura e burstable; formas reais do GCP | ✅ `91b8cea` (formas do GCP em R3) |
> | R6 — payback derivado da premissa do agente | ✅ `fe3065e` |
> | R7 — Terraform: região cotada no README | ✅ `51bb319`, `02f9e95` |
> | R8 — validação antes/depois, documentação e entrega | ✅ ver §6 e §7 |

Prioridade 2 da lista de 12/10/2026: itens 3 (preço por região) e 4 (preços
oficiais de AWS e GCP), mais `family_equivalence` e payback.

## 0. Por que este plano existe

Medido em 12/10/2026, nas APIs públicas dos provedores e no banco local:

| fato | evidência |
|---|---|
| Toda cotação usa uma região fixa por provedor | `cloud_price_points`: AWS só `us-east-1`, Azure só `eastus`, GCP só `us-central1`, OCI só `us-ashburn-1` (rotulado; o código fixa a região) |
| São Paulo custa ~1,6× a Virgínia | AWS `m7g.2xlarge` 0,3264 → 0,5202 (1,59×); Azure, mediana de 751 VMs, 1,60×; GCP E2 0,0218 → 0,0346 por vCPU (1,59×); gp3 0,08 → 0,152 (1,9×); S3 0,023 → 0,0405 |
| A OCI tem preço único global | política pública da Oracle; o `cetools` não tem região |
| Disco, objeto e egress de AWS e GCP são `reference` digitados | `seed_category_rates.py`, região fictícia `us-east` |
| As fontes oficiais existem e são públicas | AWS: `ebs.json` da calculadora (380 KB, todas as regiões), Price List Bulk de S3 (520 KB/região) e de Data Transfer (1,5 MB/região). GCP: Catalog API com a key que já existe (Compute e Cloud Storage). Azure: Retail API. OCI: cetools |
| O matcher troca a arquitetura | `M7G` (Graviton, ARM) → OCI `E3` (x86); `E2` do GCP (x86) → AWS `t4g` (ARM, burstable) e OCI `A2` (ARM) |
| O matcher escolhe burstable para carga sustentada | `IS4GEN`, `C4`, `R8G` → Azure `Standard_B2pts_v2` (2 vCPU, 1 GiB, burstable) a US$ 6/mês por instância |
| O GCP cota máquina que não existe | `e2-standard-64` (o E2 vai até 32 vCPU) e `c3-standard-16` (o C3 é 4, 8, 22, 44…). O Terraform usaria esses nomes |
| Os bills agregados não têm região, mas a linha diz | AWS CE: prefixo do `UsageType` (`SAE1-`, `USE2-`; sem prefixo é `us-east-1`). GCP Cost Table: descrição do SKU (`running in Sao Paulo`, `in Americas`) |
| O payback não é calculado | `savings_summary.payback_period_months` é SEM_BASE; o agente manda `payback.meses` e a premissa, e o backend repassa sem conferir |

Consequência: num bill de São Paulo, a economia de migrar para AWS, Azure ou
GCP sai ~60% otimista no compute e até 90% no disco. Num bill da OCI em São
Paulo, o mesmo erro favorece a saída da OCI. A decisão pode virar.

## 1. Regras de trabalho

- Branch `feat/preco-regional` a partir da `develop`, um commit por etapa,
  merge **só** na `develop`. Nada na `main` sem autorização explícita.
- Sem acesso a produção. Validação no Postgres local e na CI.
- Testes não acessam a rede: fetchers testados com respostas gravadas.
- Cada etapa com testes, revisão crítica e análise de impacto.
- Sem migration: `cloud_price_points` já tem `region` na chave primária.
- Análises gravadas não mudam (o payload é congelado). Só o recálculo muda.

## 2. Decisões

**D1. Zona = metrópole equivalente entre provedores.** Tabela de domínio pura
(`domain/services/regioes.py`): cada zona tem uma região canônica por provedor
(a que se cota como destino) e a lista de regiões de cada provedor que pertencem
a ela. Zonas: `virginia`, `meio-oeste`, `texas`, `oregon`, `california`,
`canada`, `sao-paulo`, `santiago`, `londres`, `frankfurt`, `europa-oeste`. Região
fora da tabela não inventa vizinho: cai na zona padrão, com aviso.

**D2. Zona padrão `virginia`.** É onde já estão três dos quatro padrões atuais e
as taxas de referência (`us-east`). O GCP padrão passa de `us-central1` para
`us-east4` **só quando a região de origem é desconhecida**; com origem
conhecida, cada provedor é cotado na zona dela.

**D3. Região da linha, em ordem:** a coluna `region` do export; senão o prefixo
do `UsageType` da AWS (sem prefixo = `us-east-1`, convenção documentada da AWS);
senão a localidade na descrição do SKU do GCP (`Sao Paulo` →
`southamerica-east1`). `Americas` é grupo de preço do GCP (Iowa, Carolina do Sul
e Oregon custam o mesmo), não região: a linha fica com `americas`, que pertence
à zona `meio-oeste`. Não se inventa `us-central1`.

**D4. Pool por região real.** A chave do pool usa a região inferida. Um pool
`M7G/global` com linhas `SAE1-` vira `M7G/us-east-1` e `M7G/sa-east-1`. São
cargas em lugares diferentes, cotadas em lugares diferentes.

**D5. Escolha do preço por provedor, em ordem:** região exata; para provedor de
preço global (OCI), qualquer região sincronizada; senão a região do provedor na
zona padrão; senão a primeira em ordem alfabética. A situação vai junto:
`exata`, `preco_global` ou `aproximada`.

**D6. Preço de outra região não é oficial para esta.** Cotação `aproximada`
rebaixa o rótulo de `official` para `reference`. A confiança da análise já mede
a fração de custo cotada com preço oficial, então cai sozinha, sem regra nova. E
um aviso informativo do backend diz qual provedor foi cotado onde.

**D7. Categorias (disco, objeto, egress, banco) na zona dominante do bill.** Os
custos auxiliares não são separados por região. A zona dominante é a de maior
custo entre as linhas com região conhecida. Vai declarada em `premissas`. O
provedor de origem usa a própria região quando houver taxa dela.

**D8. Fontes oficiais de categoria:**

| provedor | disco SSD | disco HDD | objeto | egress |
|---|---|---|---|---|
| AWS | gp3 (`ebs.json`) | st1 | S3 Standard, 1ª faixa (Bulk) | internet, 1ª faixa paga (Bulk) |
| GCP | pd-balanced | pd-standard, 1ª faixa paga | Standard regional | Premium para o próprio continente, 1ª faixa paga |
| Azure | segue `reference` (preço por disco, não por GB) | idem | Blob Hot LRS | já é oficial; passa a ter região |
| OCI | block volume (já) | idem | Object Storage Standard | já |

Banco gerenciado continua `reference` nos quatro: o índice $/vCPU-mês não tem
fonte única. Declarado.

**D9. Equivalência de família = arquitetura + burstable.** Tabela de padrões por
provedor (`domain/services/perfil_de_familia.py`). Candidato de outra
arquitetura só entra se o provedor não tem nenhum da mesma que cubra o pool, e
vai marcado `arquitetura_diferente`. Burstable só é candidato de pool
burstable. A classe por razão RAM/vCPU continua com a penalidade de
desperdício do packer, que já a cobre. `perf_coef` (desempenho por vCPU entre
gerações) fica fora: não há fonte pública confiável.

**D10. GCP com formas reais.** Tamanhos e razões de memória por família
(`standard`, `highmem`, `highcpu`), como a Google publica. Famílias: E2, N2,
N2D, N4, C3, C4, C4A (ARM) e T2A (ARM).

**D11. Payback derivado da premissa do agente.** Quando o agente declara
`payback.premissa_custo_migracao_usd`, o backend calcula
`meses = ⌈premissa ÷ economia mensal do provedor selecionado⌉` e sobrescreve o
`meses` do agente. Sem economia, não há payback (`meses: null`, com o motivo). A
premissa continua do agente; a divisão é do backend. A tela (fluxo do LLM) não
tem de onde tirar o custo de migração e continua sem payback. Uma tabela de
"orçamento máximo por prazo" seria só `economia × N`, que a tela já tem.

**D12. Versões.** O payload muda, então o fingerprint dos snapshots muda sozinho.
A regra de autoridade muda (D11): `AUTHORITY_VERSION` vai para
`agent-authority-v2`, e um snapshot preparado antes responde `409` com
`motivo: versao` no envio, como previsto no contrato. `decision_policy` não
muda: a regra de decisão é a mesma, só os insumos melhoram.

## 3. Etapas

### R1 — regiões (domínio puro)

1. `domain/services/regioes.py`: tabela de zonas (D1), `zona_da_regiao`,
   `regiao_canonica(zona, provedor)`, `regiao_da_linha(provedor, region,
   usage_type)` (D3), `escolher_regiao_de_preco(provedor, desejada,
   disponiveis)` (D5) e `PRECO_GLOBAL = {"oci"}`.
2. Testes: cada prefixo AWS e localidade GCP conhecidos; sem prefixo é
   `us-east-1`; prefixo de edge (`US-`, `EU-`) não é região; `americas` cai no
   `meio-oeste`; as quatro situações de escolha; a tabela é consistente (toda
   região canônica pertence à própria zona; nenhuma região em duas zonas).

### R2 — pools por região real

1. `BillingAggregator._pool_key` usa `regiao_da_linha`.
2. Teste: bill AWS agregado com `SAE1-BoxUsage` gera pool `sa-east-1`; GCP Cost
   Table com `running in Sao Paulo` gera `southamerica-east1`.
3. Gate de integração: CUR e Cost Explorer concordam na capacidade **por
   região**, não mais CUR por região contra legado global.

### R3 — coleta por região e preços oficiais de categoria

1. AWS: compute por região (mapa de rótulos ampliado); `fetch_aws_category_rates`
   com `ebs.json` (uma chamada para todas as regiões) e Bulk de S3 e de Data
   Transfer por região.
2. GCP: compute de várias regiões numa passada do catálogo, com as formas
   reais (D10); disco e egress do mesmo catálogo; objeto do catálogo do Cloud
   Storage.
3. Azure: compute e categorias por região.
4. OCI: uma coleta, gravada em cada região pedida (preço global).
5. `scripts/sync_prices.py --zonas virginia,meio-oeste,sao-paulo,texas,california`
   (padrão) ou `--zonas todas`. As opções antigas continuam funcionando.
6. Testes com respostas gravadas (recortes reais) para cada fetcher.

### R4 — cotação regional

1. Repositório: `regioes_com_preco(provedor, service_type)`.
2. Matcher: `match_pool(..., regioes={provedor: regiao})`; `_candidates` usa
   D5/D6 e devolve a região cotada e a situação.
3. Contexto: região de cada pool → zona → região de cada destino; categorias
   pela zona dominante (D7) com a mesma escolha.
4. Payload: `financeiro.preco_regiao` e `financeiro.preco_regiao_aproximada`
   (só quando `true`); `taxa_origem_regiao` e `taxa_destino_regiao` nas
   categorias; `premissas.regioes` com zona dominante, origem por pool,
   destino por provedor e o que foi aproximado.
5. Alertas do backend: região de origem desconhecida; preço de outra região.
6. Testes: unidade (matcher com repo falso por região, escolha de taxa) e
   integração (os dois CSVs e os mocks).

### R5 — equivalência de família

1. `domain/services/perfil_de_familia.py`: `perfil(provedor, familia_ou_sku)` →
   arquitetura (`x86`/`arm`) e burstable, por padrões.
2. Packer: filtra candidatos pelo perfil do pool (D9), com o fallback de
   arquitetura marcado.
3. Payload: `arquitetura_diferente: true` no equivalente quando aconteceu.
4. Testes: Graviton, Azure `p`, OCI `A1`/`A2`, GCP `T2A`/`C4A`; burstable `t*` e
   `B*`; pool burstable aceita burstable; fallback de arquitetura.

### R6 — payback

1. `mapear_verdade`: com `payback.premissa_custo_migracao_usd` e economia > 0,
   `payback.meses` e `savings_summary.payback_period_months` viram derivação.
2. Classificação em `authority.py`; `AUTHORITY_VERSION = "agent-authority-v2"`
   e novo hash no guarda.
3. Testes: premissa e economia → meses; economia ≤ 0 → `null` com motivo; sem
   payback do agente → nada muda.

### R7 — Terraform

1. README: "Preços cotados em <região>" por provedor de destino; aviso quando a
   região escolhida no Terraform é de outra zona que a cotada.

### R8 — validação e entrega

1. Sincronizar as zonas no banco local.
2. Antes/depois nos 2 bills reais e nos 4 mocks: decisão, economia por
   provedor, confiança, regiões, arquitetura.
3. E2E do snapshot (`scripts/e2e_snapshot_agente.py`): 4/4 coerentes.
4. Suíte, ruff, mypy (sem erro novo), pre-commit.
5. Docs: `precision-engine.md` (§3.2, §3.3, §7), `PLANO_API_V2.md` §10.4,
   `docs/endpoints/agent-snapshots.md` (versão de autoridade), `data-model.md`
   (regiões em `cloud_price_points`), `dev-setup.md` (sync por zona),
   `MCP.PLAN` (campos novos e autoridade v2).
6. Merge na `develop`.

## 4. Riscos e o que fazer com eles

| risco | mitigação |
|---|---|
| Pools mudam de chave (`global` → região) e a tela vê pools novos | é correção; o front já lê `regiao` opcional. Registrar no guia do front |
| Ambiente sem sync regional cota tudo `aproximada` e a confiança cai | é o sinal certo; o aviso diz qual sync rodar |
| A tabela de zonas erra um vizinho | testes de consistência; região fora da tabela cai na padrão com aviso, nunca num vizinho inventado |
| A Retail API da Azure limita requisições (429) | backoff já existe; zonas sob demanda |
| Snapshot do agente preparado antes do deploy | `409 motivo: versao`, previsto; o MCP prepara de novo |
| CI sem rede | a CI só tem taxas `reference`; a escolha regional cai em `aproximada` e os gates continuam valendo |

## 5. Revisão crítica do plano (antes de implementar)

- **D7 é aproximação.** Um bill com 85% em `americas` e 12% em São Paulo cota
  todo o disco com a taxa do meio-oeste. Separar os custos auxiliares por zona
  é o certo e fica registrado como próximo passo. Por ora, a premissa declara a
  zona usada.
- **D9 pode esconder uma opção barata.** Um pool Graviton não é cotado em x86
  quando o destino tem ARM. É a comparação de igual para igual. A migração de
  arquitetura é replataforma, outra decisão, e o `arquitetura_diferente`
  aparece quando ela for a única saída.
- **D9 depende de disponibilidade regional.** A Azure pode não ter ARM em
  `brazilsouth`, e o T2A do GCP não existe em São Paulo. Nesse caso cai no
  fallback marcado. Os testes cobrem isso com dados gravados, não com
  suposição.
- **R2 muda a chave dos pools no bill agregado.** Precisa conferir a correlação
  com inventário (`correlacao_inventario`) e a topologia, que leem a região do
  pool. Hoje os inventários de teste são de CUR, BigQuery e OCI, que já têm
  região; o agregado não tem inventário. Conferir mesmo assim.
- **D6 sem sync regional rebaixa tudo.** Num ambiente que só rodou o sync
  antigo, um bill de São Paulo fica com confiança menor do que hoje. É
  correto: hoje a confiança é alta sobre um preço errado.
- **D11 muda a autoridade.** O agente que mandar `meses` diferente vê o número
  trocado e o registro em `campos_sobrescritos`. Avisar o time do MCP junto com
  a v2 da autoridade.
- **Fora do plano, de propósito:** compromisso (1 e 3 anos) por região,
  `perf_coef`, preço de banco gerenciado oficial e licença de SO por região.


## 6. Revisão de cada etapa (depois de implementar)

**R1.** A tabela de zonas é julgamento, não dado: "Texas cota AWS em
`us-east-2`" é a vizinha mais próxima, não a única defensável. Os testes travam
a consistência da tabela (canônica dentro da zona, nenhuma região em duas
zonas), não a geografia. `americas` é grupo de preço do GCP, não região; ir para
o meio-oeste é convenção declarada. Prefixo de região novo da AWS que não
estiver no mapa cai em "região desconhecida", com aviso, não num vizinho.

**R2.** A chave dos pools muda nos bills agregados: `E2/global` vira
`E2/americas`, `E2/southamerica-east1` e `E2/us-south1`. É correção, mas a tela
vê pools novos e um rótulo (`americas`) que não é nome de região. Inventário não
é afetado: só existe para export detalhado, que já tinha região.

**R3.** Duas fontes da AWS (EC2 e EBS) são o endpoint da calculadora pública,
não uma API documentada; S3 e transferência vêm da Price List Bulk, que é
oficial. O destino do egress Premium do GCP (o próprio continente) é convenção.
A Retail API da Azure limita e derruba conexões: cinco regiões levaram de 10 a
30 minutos e duas tentativas caíram antes do retry cobrir `URLError` e
`RemoteDisconnected`. **Risco da substituição:** se uma fonte devolver lista
parcial sem erro, o sync apaga preços bons do mesmo conjunto. Hoje só a Azure
pagina (8 páginas fixas), e o recorte é o mesmo a cada coleta.

**R4.** As categorias usam a zona dominante: o bill GCP real tem cerca de 3,5% do compute
em São Paulo, e o disco dele é todo cotado no meio-oeste. O primeiro aviso de
"preço de outra região" disparava por banco e disco da Azure, que só existem
como referência: corrigido para avisar só o que um sync resolve. A confiança da
análise subiu de `low` para `medium` em cinco dos seis relatórios, porque as
taxas oficiais de categoria substituíram as digitadas; não porque a cotação
ficou mais otimista.

**R5.** O perfil vem do nome. A regra do `p` da Azure e do `g` da AWS pode errar
num nome futuro; nome sem perfil não filtra. Pool burstable aceita destino
burstable, mas o modo de crédito (unlimited ou não) não é modelado. Família de
disco local (IS4GEN, i*) continua cotada como uso geral: perde o NVMe local e
não há aviso. A série DC/EC da Azure (confidencial) passava no filtro de família
e foi tirada da coleta.

**R6.** O payback é tão bom quanto a premissa do agente: o backend garante a
conta, não o custo de migração. A tela continua sem payback. A autoridade subiu
para `agent-authority-v2`: snapshot preparado antes responde `409 versao` no
envio e o MCP prepara de novo.

**R7.** O README diz onde a análise cotou e avisa metrópole diferente; continua
sem número de economia.

## 7. Análise de impacto

| quem | o que muda | ação |
|---|---|---|
| Tela | campos novos e opcionais no payload (`preco_regiao`, `preco_regiao_aproximada`, `arquitetura_diferente`, `taxa_*_regiao`, `premissas.regioes`); pools do bill agregado com região; dois avisos novos de `origem: backend` | registrar no guia do front |
| Agente (MCP) | autoridade v2; `payback.meses` calculado pelo backend; os mesmos campos novos no snapshot | nota no `MCP.PLAN` |
| Análises gravadas | nada (payload congelado) | — |
| Dev e produção | sem o sync por zona, um pool de São Paulo é cotado com o preço antigo dos EUA, sai `reference` e com aviso. Os 24 preços antigos do GCP (com `e2-standard-64`) só saem quando o sync novo rodar lá | rodar `sync_prices.py` por zona (dev-setup §4.2) |
| CI | sem rede, compute vem do catálogo seed e categorias da referência; nada muda | — |
| Decisão | a política (`score-7rs-v3`) não muda; os insumos mudam | — |

## 8. Antes e depois (R8, 13/10/2026)

Antes: código da `develop` (`21c8734`). Depois: esta branch, com o sync por zona
completo no banco local (Azure: 4.256 preços buscados, 521 removidos). Mesmos
seis relatórios, sem LLM (`payload_do_agente_uc.montar`). Economia em US$/mês;
negativo é o destino sair mais caro.

| relatório | decisão | confiança | economia antes → depois |
|---|---|---|---|
| GCP Cost Table (real) | OCI = OCI | low (0,33) → medium (0,89) | AWS 873,85 → 125,94 · Azure 993,06 → 152,07 · OCI 2.797,51 → 2.628,79 |
| AWS Cost Explorer (real) | OCI = OCI | low (0,42) → medium (0,67) | Azure −19.572,21 → −21.922,40 · GCP −30.490,41 → −32.361,14 · OCI 28.883,76 → 25.169,75 |
| GCP BigQuery (mock) | OCI = OCI | low (0,28) → medium (0,87) | AWS 377,50 → −88,58 · Azure 572,49 → −23,05 · OCI 2.428,86 → 2.410,85 |
| AWS CUR (mock) | OCI = OCI | low (0,41) → medium (0,66) | Azure −10.396,90 → −12.732,49 · GCP −20.939,51 → −23.115,47 · OCI 39.546,07 → 35.990,47 |
| Azure (mock) | OCI = OCI | low (0,51) → medium (0,63) | AWS 597,49 → −67,31 · GCP −1.563,88 → −413,51 · OCI 3.872,38 → 3.925,68 |
| OCI São Paulo (mock) | nenhum = nenhum | high (1,0) = high | AWS −1.991,11 → −4.881,33 · Azure −1.412,51 → −4.856,95 · GCP −3.118,04 → −3.708,49 |

O que explica cada mudança:

- **Mesma decisão nos seis.** A confiança sobe em cinco porque as taxas de
  categoria passaram a ser oficiais, não porque a economia ficou mais otimista.
- **Bills GCP:** sair para AWS ou Azure deixa de compensar (cerca de US$ 900 →
  US$ 125–150). O compute de `southamerica-east1` é cotado em São Paulo e os
  tipos de máquina são os reais.
- **Bills AWS:** a economia da OCI cai de 9% a 13%. Os pools Graviton (C7G, M7G,
  R7G…) deixaram de ser cotados em x86 (`E3`) e vão para ARM (`A2`), mais caro
  na OCI; no GCP, para `c4a`. A cobertura sobe (72,0% → 78,6% no real; 84,8% →
  91,4% no CUR).
- **OCI de São Paulo:** sair piora muito; os outros três cotam em São Paulo,
  cerca de 1,6× a Virgínia.
- **Não é desta branch:** o pool C7G do bill AWS real (US$ 2.823) cota de 3× a
  6× o custo de origem em todos os destinos, igual na `develop` (Azure
  US$ 14.483 antes, US$ 14.121 agora). A capacidade vem das instance-hours e o
  custo parece coberto por compromisso; fica para a fila de precisão.

Checagens da R8: E2E do snapshot 4/4 coerentes (`scripts/e2e_snapshot_agente.py`);
1.573 testes passando e 24 pulados (binário do Terraform) no Python 3.12 da CI
e no local; ruff e mypy sem erro novo em relação à `develop` (os erros antigos
do repo continuam: 245 de ruff, 957 de mypy).
