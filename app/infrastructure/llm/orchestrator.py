from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.domain.interfaces.i_llm_client import ILLMClient
from app.schemas.analysis_ui import AnalysisUIResponse

if TYPE_CHECKING:
    from app.domain.analysis import MigrationContext


class LLMOrchestrator:
    """Orchestrates the LLM call to produce a structured migration report.

    Flow:
    1. Serialize MigrationContext to JSON.
    2. Build prompt with system instructions + serialized context + target JSON schema.
    3. Call ILLMClient.generate() with response_model=AnalysisUIResponse.
    4. Validate and return the structured response.
    """

    def __init__(self, llm_client: ILLMClient) -> None:
        self._client = llm_client

    async def generate_report(
        self,
        context: MigrationContext,
        source_provider: str,
        billing_period: str,
        total_spend: float,
        currency: str,
        pools_json: str,
    ) -> AnalysisUIResponse:
        context_payload = json.loads(pools_json)
        context_payload["source_provider"] = source_provider
        context_payload["billing_period"] = billing_period
        context_payload["total_spend"] = total_spend
        context_payload["currency"] = currency

        schema = AnalysisUIResponse.model_json_schema()
        prompt = (
            f"Você é um especialista sênior em FinOps e Cloud Engineering atuando como Arquiteto Cloud. "
            f"Sua fonte de verdade são os dados determinísticos abaixo (gerados pelo backend).\n\n"
            f"REGRAS OBRIGATÓRIAS:\n"
            f"1. NÃO realize cálculos. Os custos por pool já foram calculados pelo backend. Confie neles.\n"
            f"2. Para cada compute_pool, desenhe um mix de instâncias na nuvem de destino (AWS, OCI) capaz de "
            f"suportar a capacidade agregada (vCPUs e RAM GiB) de forma otimizada financeiramente.\n"
            f"3. Se houver custo de licenciamento (os_licensing), garanta que o mix proposto inclua o custo "
            f"da licença equivalente na nuvem de destino (ex: Windows, RHEL).\n"
            f"4. Considere os custos auxiliares (GKE control plane, storage, rede) na projeção de TCO total.\n"
            f"5. Analise para qual provedor de nuvem (AWS, OCI) compensa mais migrar com base em custo total "
            f"projetado e compatibilidade técnica. Preencha 'recommended_provider' e "
            f"'recommendation_rationale' com a justificativa detalhada.\n"
            f"6. LIMITE a saída a ~8000 tokens. Máximo de 5 strategic_notes, máximo de 5 action_plan. "
            f"Crie flags de otimização apenas para itens mais importantes.\n"
            f"7. RESPONDA EM PORTUGUÊS (pt-BR). Todos os campos de texto devem estar em português brasileiro.\n"
            f"8. Saída: Retorne APENAS um JSON válido. Nenhum texto explicativo fora do JSON.\n\n"
            f"Você DEVE responder com um JSON que corresponda exatamente a este schema:\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            f"Dados do Cliente (JSON):\n{json.dumps(context_payload, indent=2, default=str)}\n\n"
            f"Retorne APENAS JSON válido de acordo com o schema acima."
        )

        result = await self._client.generate(prompt, AnalysisUIResponse)

        if not isinstance(result, AnalysisUIResponse):
            raise TypeError(f"Expected AnalysisUIResponse, got {type(result).__name__}")

        return result
