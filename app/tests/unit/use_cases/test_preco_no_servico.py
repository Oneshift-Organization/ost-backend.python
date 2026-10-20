"""Preço da categoria repartido pela parte de cada serviço (PLANO_PRECO_NAO_COMPUTE R2).

O balanceador da AWS espalha linhas por hora, processamento, egress e tráfego entre
zonas. Pela regra antiga (o serviço inteiro igual a uma categoria), o detalhe do
AWSELB ficava sem preço. Com `custos_auxiliares.por_servico`, cada serviço recebe a
parte dele de cada categoria, e o resto da categoria segue na linha agregada.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.use_cases.analysis.equivalence_pricing import reconcile_pricing
from app.use_cases.analysis.equivalence_projection import project_equivalences


def _servico(nome: str, custo: float, *destinos: tuple[str, str]) -> dict[str, Any]:
    return {
        "name": nome,
        "cost": custo,
        "category": "networking",
        "service_type": "load_balancer",
        "source_metadata": {},
        "type_source": "curado",
        "targets": [
            {"provider": p, "service_name": s, "service_type": "load_balancer", "metadata": {}}
            for p, s in destinos
        ],
    }


def _cotacao(provedor: str, custo: float, metodo: str = "quantidade_medida") -> dict[str, Any]:
    return {
        "provedor": provedor,
        "custo_mensal_estimado": custo,
        "confianca_preco": "official",
        "metodo": metodo,
        "taxa_destino": "0.0113",
        "unidade": "hour",
    }


@pytest.fixture
def payload() -> dict[str, Any]:
    # AWSELB: 40 de hora, 35 de processamento e 25 de egress. O egress da categoria
    # tem mais 75 de outro serviço (EC2, que é pool e não vira linha de serviço).
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "decision_workloads": [
            _servico("AWSELB", 100, ("oci", "OCI Load Balancing"), ("azure", "Azure Load Balancer"))
        ],
        "custos_auxiliares": {
            "balanceador": 40,
            "balanceador_dados": 35,
            "egresso_rede": 100,
            "equivalentes_balanceador": [_cotacao("oci", 20), _cotacao("azure", 44)],
            "equivalentes_balanceador_dados": [_cotacao("oci", 1), _cotacao("azure", 22)],
            "equivalentes_egresso_rede": [
                _cotacao("oci", 10, "category_rate_ratio"),
                _cotacao("azure", 97, "category_rate_ratio"),
            ],
            "por_servico": {
                "balanceador": {"AWSELB": 40},
                "balanceador_dados": {"AWSELB": 35},
                "egresso_rede": {"AWSELB": 25, "AmazonEC2": 75},
            },
        },
        "resumo_executivo": {
            "custo_mensal_atual": 300,
            "por_provedor": [
                # 300 - 175 cobertos + (20 + 1 + 10) cotados
                {"provedor": "oci", "custo_estimado_total": 156},
                # 300 - 175 + (44 + 22 + 97)
                {"provedor": "azure", "custo_estimado_total": 288},
            ],
        },
    }


def _linhas(payload: dict[str, Any]) -> dict[tuple[str, str], Any]:
    return {(r.workload, r.target_provider): r for r in project_equivalences(payload)}


def test_servico_recebe_a_parte_de_cada_categoria(payload: dict[str, Any]) -> None:
    linhas = _linhas(payload)
    oci = linhas[("AWSELB", "OCI")]
    # 20 (hora inteira) + 1 (processamento inteiro) + 10 x 25/100 do egress
    assert oci.estimated_monthly_cost == 23.5
    assert oci.source_monthly_cost == 100
    # Uma das partes é por razão de tarifas: o método do serviço diz isso
    assert oci.pricing_method == "category_rate_ratio"
    assert [p["categoria"] for p in oci.pricing_evidence["partes"]] == [
        "Balanceador de carga",
        "Processamento do balanceador",
        "Tráfego de saída",
    ]
    assert linhas[("AWSELB", "AZURE")].estimated_monthly_cost == pytest.approx(44 + 22 + 24.25)


def test_o_resto_da_categoria_fica_na_linha_agregada(payload: dict[str, Any]) -> None:
    linhas = _linhas(payload)
    resto = linhas[("Tráfego de saída (agregado)", "OCI")]
    assert resto.source_monthly_cost == 75
    assert resto.estimated_monthly_cost == 7.5
    # Hora e processamento foram inteiros para o AWSELB: sem linha agregada
    assert ("Balanceador de carga (agregado)", "OCI") not in linhas


def test_a_conciliacao_fecha_por_destino(payload: dict[str, Any]) -> None:
    ledger = reconcile_pricing(project_equivalences(payload), payload)
    assert [(item["provider"], item["status"]) for item in ledger] == [
        ("oci", "reconciled"),
        ("azure", "reconciled"),
    ]


def test_parte_sem_cotacao_no_destino_fica_ao_custo_atual(payload: dict[str, Any]) -> None:
    payload["custos_auxiliares"]["equivalentes_balanceador_dados"] = [_cotacao("oci", 1)]
    # Sem o processamento cotado na Azure, o total do cenário mantém os 35 de origem
    payload["resumo_executivo"]["por_provedor"][1]["custo_estimado_total"] = 300 - 140 + 44 + 97
    linhas = _linhas(payload)
    azure = linhas[("AWSELB", "AZURE")]
    assert azure.estimated_monthly_cost == pytest.approx(44 + 35 + 24.25)
    assert azure.pricing_evidence["partes"][-1]["categoria"] == "Mantido ao custo atual"
    assert linhas[("AWSELB", "OCI")].estimated_monthly_cost == 23.5
    ledger = reconcile_pricing(project_equivalences(payload), payload)
    assert {item["provider"]: item["status"] for item in ledger} == {
        "oci": "reconciled",
        "azure": "reconciled",
    }


def test_custo_fora_das_categorias_fica_ao_custo_atual(payload: dict[str, Any]) -> None:
    # 30 do AWSELB fora das categorias: ficam no saldo mantido do cenário
    payload["decision_workloads"][0]["cost"] = 130
    payload["resumo_executivo"]["custo_mensal_atual"] = 330
    payload["resumo_executivo"]["por_provedor"][0]["custo_estimado_total"] = 186
    payload["resumo_executivo"]["por_provedor"][1]["custo_estimado_total"] = 318
    linhas = _linhas(payload)
    assert linhas[("AWSELB", "OCI")].estimated_monthly_cost == 23.5 + 30
    ledger = reconcile_pricing(project_equivalences(payload), payload)
    assert [item["status"] for item in ledger] == ["reconciled", "reconciled"]


def test_sem_parte_cotada_no_destino_nao_ganha_preco(payload: dict[str, Any]) -> None:
    payload["custos_auxiliares"]["por_servico"] = {
        "rede_outros": {"AWSELB": 100},
        "egresso_rede": {"AmazonEC2": 100},
    }
    linhas = _linhas(payload)
    assert linhas[("AWSELB", "OCI")].estimated_monthly_cost is None


def test_servico_inferido_nao_ganha_preco(payload: dict[str, Any]) -> None:
    payload["decision_workloads"][0]["type_source"] = "inferido"
    assert _linhas(payload)[("AWSELB", "OCI")].estimated_monthly_cost is None


def test_alternativas_dividem_o_orcamento(payload: dict[str, Any]) -> None:
    payload["decision_workloads"][0]["targets"].append(
        {"provider": "oci", "service_name": "OCI Network Load Balancer", "metadata": {}}
    )
    oci = [r for r in project_equivalences(payload) if r.target_provider == "OCI"]
    precos = {r.target_service: r.estimated_monthly_cost for r in oci if r.workload == "AWSELB"}
    assert precos == {"OCI Load Balancing": 23.5, "OCI Network Load Balancer": 23.5}
    ledger = reconcile_pricing(project_equivalences(payload), payload)
    assert ledger[0]["status"] == "reconciled"


def test_todas_as_partes_medidas_dizem_quantidade_medida(payload: dict[str, Any]) -> None:
    payload["custos_auxiliares"]["por_servico"]["egresso_rede"] = {"AmazonEC2": 100}
    payload["decision_workloads"][0]["cost"] = 75
    oci = _linhas(payload)[("AWSELB", "OCI")]
    assert oci.pricing_method == "quantidade_medida"
    assert oci.estimated_monthly_cost == 21
    assert "Quantidade medida" in oci.pricing_reason
