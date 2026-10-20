"""O shape do destino tem que chegar ao payload.

O matcher calcula `n2-standard-8` e guardava em `EquivalentOption.sku_id`; a
conversão para `EquivalentService` descartava o campo em silêncio, e o payload
ficava só com `nome_servico` — que é rótulo humano ("Compute Engine N2"), não
shape.

Ninguém percebia porque o rótulo *parece* uma resposta. Só que não dá para
escrever `machine_type` com ele, e nenhum consumidor consegue reproduzir qual
máquina foi escolhida.

Este é o segundo campo a se perder nessa mesma fronteira — o primeiro foi a
proveniência do preço. Daí o teste.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.analysis import (
    EquivalentService,
    FinancialEstimate,
    TechnicalMatch,
)
from app.use_cases.analysis.run_analysis_pipeline import _serialize_equivalent


def _equivalente(**kw) -> EquivalentService:
    base = {
        "provider": "gcp",
        "service_name": "Compute Engine N2",
        "category": "compute",
        "service_type": "virtual_server",
        "sku_id": "n2-standard-8",
        "instances": 3,
        "technical_match": TechnicalMatch(vcpus=Decimal("24"), ram_gb=Decimal("96")),
        "financials": FinancialEstimate(
            estimated_monthly_cost=Decimal("812.44"),
            pricing_confidence="official",
        ),
    }
    return EquivalentService(**{**base, **kw})


def test_o_sku_chega_ao_payload() -> None:
    d = _serialize_equivalent(_equivalente())
    assert d["sku"] == "n2-standard-8"


def test_rotulo_e_shape_sao_campos_diferentes() -> None:
    """O ponto central: `nome_servico` não substitui o `sku`.

    Um Terraform escrito a partir de "Compute Engine N2" não tem `machine_type`.
    """
    d = _serialize_equivalent(_equivalente())
    assert d["nome_servico"] == "Compute Engine N2"
    assert d["sku"] != d["nome_servico"]


def test_o_que_o_terraform_precisa_esta_todo_la() -> None:
    """Shape, quantidade e capacidade — o suficiente para um recurso de compute
    sem o modelo precisar inventar nada."""
    d = _serialize_equivalent(_equivalente())
    assert d["sku"] == "n2-standard-8"
    assert d["instancias"] == 3
    assert d["correspondencia_tecnica"] == {"vcpus": 24.0, "ram_gb": 96.0}


def test_catalogo_curado_nao_inventa_sku() -> None:
    """Equivalente sem hardware casado não tem shape. Emitir string vazia ou o
    rótulo no lugar seria pior que omitir: o consumidor trataria como shape."""
    d = _serialize_equivalent(
        _equivalente(sku_id=None, match_basis="curated_only", technical_match=None)
    )
    assert "sku" not in d
    assert d["base_correspondencia"] == "curated_only"
