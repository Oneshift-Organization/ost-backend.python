"""Ocupação de origem burstable na mesma base do empacotamento (PROXIMAS_EXECUCOES,
item 4). O caso é o pool T4G da f67fcd01: 2,93 vCPU nominal, 11,74 GB, numa A1 de 2 OCPU."""

from __future__ import annotations

from typing import Any

from app.domain.services.terraform_plan import derivar_plano


def _payload(*, com_registro: bool, sku: str = "VM.Standard.A1.Flex.1c8g") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provedor_origem": "aws",
        "pools_computacao": [
            {
                "familia": "T4G",
                "regiao": "us-east-1",
                "capacidade": {"vcpus": 2.93, "ram_gib": 11.74},
                "custo_computacao": 71.98,
                "equivalentes": [
                    {
                        "provedor": "OCI",
                        "sku": sku,
                        "instancias": 2,
                        "correspondencia_tecnica": {"vcpus": 2.0, "ram_gb": 16.0},
                        "financeiro": {"confianca_preco": "official"},
                    }
                ],
            }
        ],
    }
    if com_registro:
        payload["premissas"] = {
            "burstable_pela_base": {
                "pools": [{"pool": "T4G/us-east-1", "base_cpu": 0.4, "vcpus_nominais": 2.93}]
            }
        }
    return payload


def _premissa_e_avisos(payload: dict[str, Any]) -> tuple[str, list[str]]:
    plano = derivar_plano(payload, provedor_destino="oci", regiao="sa-saopaulo-1")
    premissa = next(p for p in plano.premissas if p.campo.endswith(".machine_type"))
    return premissa.base, plano.avisos


def test_pela_base_gravada_a_maquina_cabe() -> None:
    base, avisos = _premissa_e_avisos(_payload(com_registro=True))
    # 2,93 x 0,4 = 1,17 vCPU de 2; a memória (11,74 de 16 GB) é o que mais pesa
    assert "o uso medido ocupa 73% delas" in base
    assert "A origem é burstable: a vCPU conta pela linha de base (40% da nominal)" in base
    assert not any("máquina a mais" in a for a in avisos)


def test_analise_sem_o_registro_segue_pela_nominal() -> None:
    # Análise anterior à regra: dimensionada pela nominal, lida pela nominal
    base, avisos = _premissa_e_avisos(_payload(com_registro=False))
    assert "o uso medido ocupa 146% delas" in base
    assert "burstable" not in base
    assert any("máquina a mais" in a for a in avisos)


def test_destino_com_credito_fica_pela_nominal() -> None:
    """Destino burstable também tem crédito: o empacotamento usa a nominal, e o aviso também."""
    payload = _payload(com_registro=True, sku="t3a.large")
    payload["provedor_origem"] = "gcp"
    payload["pools_computacao"][0]["equivalentes"][0]["provedor"] = "AWS"
    plano = derivar_plano(payload, provedor_destino="aws", regiao="us-east-1")
    premissa = next(p for p in plano.premissas if p.campo.endswith(".machine_type"))
    assert "burstable" not in premissa.base
    assert "146%" in premissa.base
