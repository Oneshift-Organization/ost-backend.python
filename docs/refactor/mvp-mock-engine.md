# MVP Mock Engine - Refactor Plan & Troubleshooting

## 1. Visão Geral
Este documento define o pipeline de faturamento multi-cloud (AWS, Azure, GCP, OCI) utilizando o **Mock Engine MVP**. O sistema agora realiza cálculos determinísticos de infraestrutura (baseados em especificações de hardware) e delega estritamente a *explicabilidade* para o LLM. A API não faz suposições financeiras.

## 2. A Arquitetura de Decisão (Data-Driven)
* **Staging:** A tabela `billing_items` atua como fonte canônica bruta de ingestão.
* **Enrichment:** O `BuildBillingEnrichedUseCase` normaliza os dados cruzando o `usage_type` com a tabela de ontologia (`service_ontology`).
* **Matching (Core):** O `EquivalenceMatcher` busca `target_equivalents` na tabela `cloud_sku_catalog` aplicando a regra estrita de hardware:
  `target.vcpus >= source.vcpus AND target.ram_gb >= source.ram_gb` (retornando a opção de menor preço).

## 3. Diretrizes para Ajustes e Melhorias Contínuas
Para adicionar novos serviços, suportar novos provedores ou modificar os cálculos, siga o protocolo de intervenção isolada:
1. **Zero Condicionais no Código:** Nunca adicione IF/ELSE ou regras baseadas em strings comerciais de provedores no motor Python. Se um novo serviço surgir, a regra deve ser inserida exclusivamente no banco via `service_ontology`.
2. **Novas Métricas de Hardware:** Se no futuro precisarmos cruzar IOPS ou Network Throughput (essencial para OCI e bancos de dados), adicione as novas colunas numéricas no `cloud_sku_catalog`, crie os índices compostos no PostgreSQL e atualize a query no `EquivalenceMatcher`. A estrutura atual já garante que não haverá Full Table Scans.
3. **Atualização de Preços:** O catálogo `cloud_sku_catalog` deve ser mantido atualizado pelos Pricing Workers assíncronos (Celery). O backend principal apenas consulta o banco.

## 4. Testes e Relatórios de Erros (Troubleshooting)
O pipeline foi desenhado para expor falhas e direcionar a intervenção manual, não para engolir erros.

* **Falha 1: "0 Equivalents encontrados" no Payload de IA.**
  * **Diagnóstico:** O `EquivalenceMatcher` não encontrou nenhum SKU no catálogo da nuvem de destino que satisfaça as restrições `(vcpus >= X AND ram_gb >= Y)`.
  * **Relatório/Correção:** Verifique se o catálogo `cloud_sku_catalog` possui instâncias de alto desempenho provisionadas para aquele provedor específico.
* **Falha 2: Volume de registros "Unmapped" aumentando.**
  * **Diagnóstico:** A fatura de entrada possui um `UsageType` (AWS) ou `Service Description` (GCP) que não consta na `service_ontology`. O item não ganha contexto ontológico.
  * **Relatório/Correção:** Consulte a tabela de "dead-letter" `unmapped_billing_lines`. Estas linhas devem disparar o ciclo de Human-in-the-loop: o LLM do admin sugere a taxonomia e o operador a aprova para alimentar a ontologia de forma permanente.

## 5. Próximas Fases (Roadmap de Transição)
- Implementar **Pricing Workers** dinâmicos consumindo as APIs oficiais de varejo.
- Substituir a tag semântica `pricing_confidence: "high (mock)"` pela confiabilidade real atestada pela recência da API.
- Adicionar instâncias de storage (`object_storage`, `block_storage`) no seed mock para expandir as análises financeiras e de latência.
