"""Contestação de preço verificável (lote v4, V4.1)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.services.contestacao import (
    A_RECONFIRMAR,
    NAO_VERIFICAVEL,
    VERIFICADO,
    acoes_da_contestacao,
    verificar_contestacao,
)
from app.use_cases.analysis.agent_result import processar_sugestoes


def _payload(**financeiro: Any) -> dict[str, Any]:
    base = {
        "confianca_preco": "official",
        "preco_origem": "gcp_billing_catalog_api",
        "preco_capturado_em": "2026-10-15",
        "preco_idade_dias": 1,
        "preco_estado": "vivo",
        "preco_regiao": "us-central1",
    }
    return {
        "pools_computacao": [
            {
                "familia": "N2",
                "equivalentes": [
                    {"provedor": "GCP", "sku": "n2-standard-8", "financeiro": base | financeiro}
                ],
            }
        ],
        "custos_auxiliares": {
            "equivalentes_egresso_rede": [
                {
                    "provedor": "oci",
                    "categoria": "network_egress",
                    "confianca_preco": "official",
                    "taxa_destino_regiao": "sa-saopaulo-1",
                }
            ]
        },
    }


REF = "GCP/n2-standard-8/us-central1"


def test_preco_oficial_vivo_da_regiao_e_verificado() -> None:
    v = verificar_contestacao(REF, _payload())
    assert v.resultado == VERIFICADO
    assert v.pedido is None
    assert v.evidencia["preco_origem"] == "gcp_billing_catalog_api"


@pytest.mark.parametrize(
    "mudanca",
    [
        {"preco_estado": "em_decomposicao"},
        {"confianca_preco": "reference"},
        {"preco_regiao_aproximada": True},
    ],
)
def test_preco_velho_aproximado_ou_referencia_pede_sync(mudanca: dict[str, Any]) -> None:
    v = verificar_contestacao(REF, _payload(**mudanca))
    assert v.resultado == A_RECONFIRMAR
    assert v.pedido is not None
    assert (v.pedido.provedor, v.pedido.regiao, v.pedido.sku, v.pedido.tipo) == (
        "gcp",
        "us-central1",
        "n2-standard-8",
        "virtual_server",
    )


def test_payload_sem_estado_usa_a_idade() -> None:
    sem_estado = _payload(preco_estado=None, preco_idade_dias=12)
    assert verificar_contestacao(REF, sem_estado).resultado == VERIFICADO
    velho = _payload(preco_estado=None, preco_idade_dias=45)
    assert verificar_contestacao(REF, velho).resultado == A_RECONFIRMAR


def test_taxa_de_categoria_tambem_se_verifica() -> None:
    v = verificar_contestacao("OCI/network_egress/sa-saopaulo-1", _payload())
    assert v.resultado == VERIFICADO


@pytest.mark.parametrize("referencia", [None, "", "GCP", "AWS/m7g.large/us-east-1"])
def test_referencia_ausente_ou_nao_achada_nao_e_verificavel(referencia: object) -> None:
    assert verificar_contestacao(referencia, _payload()).resultado == NAO_VERIFICAVEL


def _contestacao(**extra: Any) -> dict[str, Any]:
    return {
        "tipo": "contestacao_preco",
        "severidade": "alta",
        "titulo": "Preço defasado",
        "referencia": REF,
        **extra,
    }


def test_contestacao_verificada_nao_rebaixa() -> None:
    processadas, final = processar_sugestoes([_contestacao()], "medium", payload=_payload())
    assert final == "medium"
    assert processadas[0]["efeito_backend"] == VERIFICADO
    assert processadas[0]["verificacao"]["resultado"] == VERIFICADO


def test_contestacao_de_preco_velho_rebaixa_como_antes() -> None:
    payload = _payload(preco_estado="adubo")
    processadas, final = processar_sugestoes([_contestacao()], "medium", payload=payload)
    assert final == "low"
    assert processadas[0]["efeito_backend"] == "confianca_rebaixada"


def test_sem_payload_segue_a_regra_antiga() -> None:
    _, final = processar_sugestoes([_contestacao()], "medium")
    assert final == "low"


def test_agente_nao_carimba_a_verificacao() -> None:
    falsa = _contestacao(verificacao={"resultado": VERIFICADO}, referencia="AWS/x/y")
    processadas, final = processar_sugestoes([falsa], "medium", payload=_payload())
    assert processadas[0]["verificacao"]["resultado"] == NAO_VERIFICAVEL
    assert final == "low"


def test_preco_sugerido_vira_observacao_do_agente() -> None:
    pedidos, observacoes = acoes_da_contestacao(
        [_contestacao(preco_sugerido="0.42")], _payload(preco_estado="adubo")
    )
    assert [p.sku for p in pedidos] == ["n2-standard-8"]
    assert [(o.chave_do_preco, o.preco) for o in observacoes] == [
        (("gcp", "n2-standard-8", "us-central1"), Decimal("0.42"))
    ]


@pytest.mark.parametrize("valor", [None, "abc", "-1", "0"])
def test_sem_preco_sugerido_valido_nao_ha_observacao(valor: object) -> None:
    _, observacoes = acoes_da_contestacao([_contestacao(preco_sugerido=valor)], _payload())
    assert observacoes == []
