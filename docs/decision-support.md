# Score determinístico e 7 Rs — política v1

## Escopo e compatibilidade

Novas análises usam regras puras do backend; Claude e o fallback NVIDIA narram
os mesmos resultados. Não há serviço, GPU ou chamada de IA adicional para calcular
scores ou classificar estratégias. Não é um modelo de machine learning treinado.
Análises já persistidas não são recalculadas. Não requer migration de banco:
o snapshot e o resultado usam os campos JSON existentes.

`decision_support` é um campo aditivo de `AnalysisV2Response`. Contrato
`oneshift-decision-v1`, política `score-7rs-v3`, escala 0–100. O backend o
recalcula após inferência, inclusive no fallback e na submissão do agente.
Entradas do agente não podem substituir scores nem estratégias.

## Política de pontuação

Política inicial normativa e versionada, não calibrada com migrações reais:

| Eixo | Peso global | Fatores e pesos internos |
|---|---:|---|
| Financeiro | 50% | economia 70%; cobertura de preços 30% |
| Técnico | 50% | refatoração 60%; lock-in 40% |

- Economia: `clamp(50 + 100 * economia_mensal / custo_atual, 0, 100)`.
- Cobertura: `100 * cobertura` calculada no resumo financeiro.
- Refatoração: 30 se necessária, 100 se explicitamente dispensada nos dois
  lados do catálogo; ausência não equivale a `false`.
- Lock-in: baixo=100, médio=50, alto=0; vale a restrição conhecida mais forte.
- Fatores técnicos são ponderados pelo custo positivo dos componentes. Impostos,
  compromissos e itens não classificados não são aplicações migráveis.
  Mapeamento de tipo inferido não confirma compatibilidade técnica.
- Governança não pontua (removida do score na `score-7rs-v3`). Declarar
  `region_approved` ou `compliance_approved` como `false` continua bloqueando
  o destino. Declarações do responsável não são certificações.

Cada fator expõe regra, entradas, cobertura e contribuição. Arredondamento
decimal para duas casas; pequenas diferenças de 0,01 podem aparecer ao somar
valores exibidos já arredondados. Pesos não são redistribuídos por falta de dados.

Quando faltam informações, `value=null`, `status=partial` e os limites representam
o mínimo/máximo aritmético dos fatores desconhecidos. **Não são probabilidades nem
intervalos estatísticos.** Confiança alta/média/baixa e lock-in continuam separados.

Campos legados obrigatórios permanecem numéricos: scores inteiros recebem o
limite inferior arredondado; `viability_score` é esse inteiro ×10. Sem destino,
recebem zero. Consumidores novos **devem usar `decision_support`** para não
confundir limite ou ausência de dados com nota final. A UI atualizada faz isso.

Mesmos dados normalizados + snapshot de catálogo/preços/câmbio + declarações +
versão de política produzem a mesma decisão e fingerprint SHA-256. Reimportar a
mesma fatura com preços ou evidências atualizados pode legitimamente mudar a nota.
Notas iguais de duas faturas diferentes, isoladamente, não comprovam um defeito.

## Seleção e bloqueios

Elegibilidade exige economia positiva e cobertura financeira de pelo menos 50%.
Região ou conformidade explicitamente reprovadas bloqueiam o destino, mesmo com
economia alta. Um componente declarado que não corresponde ao snapshot também
bloqueia o cenário, evitando ignorar uma restrição por erro de identificação.

Se todos os destinos elegíveis têm nota completa, vence a maior nota; desempate
por economia e nome do provedor. Se algum é parcial, a seleção é **econômica
preliminar**, por economia, sem comparar notas de completudes diferentes. Isso
fica explícito na saída. Sem elegível, não há destino recomendado.

## Estratégias 7 Rs

Referência conceitual: [AWS Prescriptive Guidance — migration strategies](https://docs.aws.amazon.com/prescriptive-guidance/latest/large-migration-guide/migration-strategies.html).
As regras abaixo são implementação própria, não uma certificação da AWS.

| R | Evidência exigida nesta versão |
|---|---|
| Retain | responsável declara permanência necessária |
| Retire | desuso, aprovação de desativação e dependências revisadas |
| Repurchase | SaaS aprovado, aderência funcional e plano de dados |
| Relocate | plataforma suportada, licenças transferíveis e plataforma inalterada |
| Refactor | catálogo registra refatoração em origem ou destino |
| Replatform | sem refatoração nos dois lados, lock-in médio/alto |
| Rehost | sem refatoração nos dois lados, baixo lock-in |

Os três últimos são **candidatos** por componente de billing, não conclusões
sobre a aplicação inteira. Sem metadados suficientes, estratégia indeterminada.
Declarações conflitantes exigem revisão; uma exigência de retenção prevalece.
Todos precisam de validação de inventário, runtime e dependências antes de execução.

Retain/Retire/Repurchase/Relocate não reutilizam custos do cenário genérico:
ele fica bloqueado até reprecificação específica. O gerador Terraform atual
recusa esses cenários ou bloqueios de governança com HTTP 422
`ESTRATEGIA_REQUER_REVISAO`, em vez de gerar infraestrutura incoerente.

## Contexto adicional opcional pela API

`POST /v1/analyses/start/{report_id}` continua aceitando chamada **sem body**.
Quem possui evidências adicionais pode enviar:

```json
{
  "decision_context": {
    "governance": [{
      "provider": "gcp",
      "region_approved": true,
      "compliance_approved": true,
      "operations_ready": true,
      "evidence": "Referência à avaliação aprovada pelo responsável"
    }],
    "workloads": [{
      "workload": "Nome exato do componente no billing",
      "provider": "gcp",
      "retain_required": true,
      "evidence": "Restrição contratual vigente"
    }]
  }
}
```

Usar booleanos JSON reais; não enviar valores desconhecidos como `false`.
Não colocar senhas, tokens ou dados pessoais nas evidências. O contexto entra
no snapshot persistido; a idempotência de um job ativo preserva a primeira
solicitação. O formulário de coleta dessas declarações não faz parte desta entrega;
a interface exibe fielmente as informações disponíveis e as lacunas.

## Operação

- Deploy de frontend, backend e prompts em **develop**, sem mudar o principal
  Claude ou seu fallback NVIDIA. Frontend tolera resultados antigos sem o bloco.
- Prompts v2 mantêm envelope `oneshift-prompts-v1` e comportamento legado quando
  não existe `decision_support`; cache de prompts pode levar 300s para atualizar.
- Testes: `pytest app/tests/unit --no-cov`, testes HTTP sem banco/LLM, build e
  testes de apresentação no frontend. Não usar dados de clientes em fixtures.
- Rollback: reverter os commits desta funcionalidade por GitOps. Nenhuma tabela
  é removida ou alterada. Preservar snapshots e resultados para auditoria.
- Redesign da homepage, Mermaid e benefícios/créditos corporativos automáticos
  são entregas posteriores, não são incluídos silenciosamente nesta alteração.
