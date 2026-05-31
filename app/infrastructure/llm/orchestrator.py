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
            f"1. NÃO realize cálculos. Todos os custos, `estimated_monthly_cost` e `cost_delta` já foram "
            f"calculados deterministicamente pelo backend. Use os valores exatamente como estão.\n"
            f"2. Para cada `compute_pool`, SELECIONE a melhor opção de migração dentre os `equivalents` "
            f"fornecidos no campo `equivalents[]`. NÃO invente instâncias, SKUs ou preços. Se `equivalents` "
            f"estiver vazio para um pool, declare o gap explicitamente ('sem equivalente catalogado').\n"
            f"3. Use `metadata` e `docs_costs` de cada equivalent para justificar portabilidade, lock-in e "
            f"riscos de migração. NÃO invente características dos serviços alvo.\n"
            f"4. O `recommended_provider` deve ser o provider com o menor `cost_delta` agregado entre os "
            f"pools (negativo = economia). Justifique com os números do payload.\n"
            f"5. Considere `auxiliary_costs` (GKE, storage, rede, database e seus equivalents) no TCO total.\n"
            f"6. LIMITE a saída a ~8000 tokens. Máximo de 5 strategic_notes, máximo de 5 action_plan.\n"
            f"7. RESPONDA EM PORTUGUÊS (pt-BR). Todos os campos de texto em português brasileiro.\n"
            f"8. Saída: Retorne APENAS um JSON válido. Nenhum texto fora do JSON.\n\n"
            f"Você DEVE responder com um JSON que corresponda exatamente a este schema:\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            f"Dados do Cliente (JSON):\n{json.dumps(context_payload, indent=2, default=str)}\n\n"
            f"Retorne APENAS JSON válido de acordo com o schema acima."
        )

        result = await self._client.generate(prompt, AnalysisUIResponse)

        if not isinstance(result, AnalysisUIResponse):
            raise TypeError(f"Expected AnalysisUIResponse, got {type(result).__name__}")

        return result
