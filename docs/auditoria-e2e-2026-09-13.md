# Auditoria E2E — 13/09/2026

Fluxo completo executado contra Postgres real e o binário do Terraform:

```
CSV → billing_items → billing_enriched → payload da análise → blueprint → .tf
```

Dois relatórios reais (`aws_cost_explorer_full.csv`, `gcp_cost_table_full.csv`),
com conferência número a número entre cada camada.

## Conservação de valor — resultado final

| camada | AWS | GCP (convertido a USD) |
|---|---|---|
| `billing_items` | $104.077,14 | $4.881,60 |
| `billing_enriched` | $92.103,48 | $4.881,60 |
| `payload.gasto_total` | $92.103,48 | $4.881,60 |
| pools + auxiliares + órfão | $92.103,48 | $4.881,60 |

A diferença de $11.973,66 na AWS é uma linha só, `Tax` — ver §3.1.

---

## 1. Corrigido

### 1.1 A linha `Total` do GCP dobrava o gasto · **ALTA**

O Cost Table termina com `Cost type = Total`, que repete a soma de todas as
outras linhas. O parser só descartava `tax`, então ela entrava em
`billing_items`:

```
antes:  billing_items R$ 55.650,26   billing_enriched R$ 27.825,13   (exatamente 2x)
depois: billing_items R$ 27.825,13   billing_enriched R$ 27.825,13
```

**O que estava certo e o que não estava:** o enriquecimento já a descartava, então
a análise, o payload e o Terraform nunca erraram. Quem mostrava o dobro era o
**dashboard** e o **drill-down** (`GET /v1/billing-files/{job_id}/items`), que
somam `billing_items` direto.

`rounding error` ficou de fora da exclusão: é ajuste contábil real, na casa dos
centavos, e descartá-lo criaria diferença inexplicável contra a fatura.

### 1.2 Shape escolhido com preço fraco saía como "Calculado" · **ALTA**

No bill AWS real, **todos os 20 pools** têm `confianca_preco: low`, origem
`seed_catalog` — catálogo interno, não preço coletado. O README marcava cada
`machine_type` como **Calculado**, que lê como resposta certa.

O packer escolhe a máquina **comparando preços**. A escolha é derivação *dado o
preço*; se o preço é catálogo, a escolha herda a fraqueza.

```
antes:  | vm_m7g.machine_type | Calculado | e2-standard-8 x29 | Escolhido pelo empacotamento… |
depois: | vm_m7g.machine_type | Estimado  | e2-standard-8 x29 | …O empacotamento compara PREÇOS,
          e o preço usado tem confiança 'low' (seed_catalog) — não é preço coletado da API do
          provedor. Outra máquina pode sair melhor com preço oficial. |
```

Mesmo princípio de `confianca_analise`: o nível não pode ser melhor que o insumo
que o sustenta. A premissa passa a carregar confiança, origem e idade do preço.

---

## 2. Falsos positivos do auditor (o sistema estava certo)

Registrados porque custaram tempo e podem confundir de novo.

| o que parecia | o que era |
|---|---|
| GCP: 6 baldes divergindo do banco em ~5,7x | **Câmbio.** O banco guarda a moeda de origem (BRL), o payload sai na moeda de análise (USD) a 5,70. Comparar sem converter é exatamente o erro que o Precision Engine existe para evitar. |
| `main.tf` menciona valor monetário (`$`) | `${count.index}`, interpolação do Terraform. |
| pool T3 com `count=13`, payload diz 3 | Regex do auditor casava `vm_t3` com `vm_t3a`. |
| disco com 1 GB de diferença | O derivador arredonda (`quantize`), o auditor truncava. |

---

## 3. Não corrigido

### 3.1 Imposto é tratado em camadas diferentes por provedor · **MÉDIA**

| provedor | onde o imposto é excluído | efeito no dashboard |
|---|---|---|
| GCP Cost Table | **parser** — nunca entra em `billing_items` | dashboard **não** mostra imposto |
| AWS Cost Explorer | **enriquecimento** — fica em `billing_items` | dashboard **mostra** imposto |

No bill AWS são $11.973,66, 11,5% do total. Um cliente comparando os dois
dashboards vê critérios diferentes sem nada que explique.

**Por que não corrigi:** é decisão de produto, não técnica. "Quanto eu gasto"
inclui imposto; "quanto dá para migrar" não. As duas leituras são defensáveis, e
qualquer escolha muda números que o time já vem olhando — inclusive os fixados
nos testes de caracterização.

**Para decidir:** o dashboard deve mostrar o gasto faturado (com imposto) ou o
gasto analisável (sem)? Uma vez decidido, é uma linha em cada parser.

### 3.2 Todo preço de destino é de catálogo neste ambiente · **MÉDIA**

`razao_custo_confiavel: 0.0` no bill AWS — nenhum dos $195 mil cotados veio de
preço oficial, porque este banco de teste não tem `cloud_price_points` de
compute sincronizados.

Não é bug: o sistema **declara** isso corretamente (§1.2 e o
`confianca_analise` do manifesto). Mas significa que o `.tf` gerado aqui escolhe
máquinas com preço interno.

**Para resolver:** `sync_prices.py --providers azure,oci,aws,gcp` antes de gerar
pacote para cliente. O GCP exige `GCP_BILLING_API_KEY`.

### 3.3 Região da cotação ≠ região do pacote · **MÉDIA**

Já registrado como dívida §10.4 do `PLANO_API_V2`. A cotação usa região fixa
(`us-central1` no GCP); o pacote pode pedir `southamerica-east1`.

**Mitigado, não resolvido:** o README declara que **não há economia declarada**,
justamente para não publicar número de outra região. O `.tf` sai correto; o que
falta é poder afirmar economia.

### 3.4 189 SKUs não classificados no bill AWS · **BAIXA**

$6.900,61 (7,5%) em `nao_classificado`, incluindo Translate, CloudWatch, RDS
Extended Support, MWAA e VPN. Aparecem em `unsupported_items` do pacote, então
o cliente é avisado — mas não viram recurso.

**Por que não corrigi:** classificar cada um exige decidir o equivalente
cross-cloud de serviços gerenciados que o Capacity Pool Model não modela
(tradução, orquestração de workflow). É trabalho de ontologia, não de parser.

---

## 4. Verificação final

```
436 testes unitários
 75 testes de integração (2 skips: sem preço oficial neste banco)
terraform fmt -check    → canônico nos dois pacotes
terraform validate      → válido contra o schema do provider Google, nos dois
```

Artefatos gerados a partir dos CSVs versionados:

| | AWS | GCP |
|---|---|---|
| recursos no `.tf` | 26 | 6 |
| premissas declaradas | 26 | 6 |
| conservação de valor | ✅ | ✅ |
