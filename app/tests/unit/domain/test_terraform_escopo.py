"""Terraform só do escopo do plano de migração (T5): o recorte do payload."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from app.domain.services.terraform_plan import derivar_plano
from app.domain.services.terraform_topologia.escopo import (
    LigacaoEntreNuvens,
    RecorteDoEscopo,
    familia_da_maquina,
    recortar_ao_escopo,
)


@pytest.mark.parametrize(
    ("tipo", "familia"),
    [
        ("c4.4xlarge", "C4"),
        ("m6g.large", "M6G"),
        ("Standard_F16s_v2", "FSV2"),
        ("Standard_D4ds_v5", "DDSV5"),
        ("Standard_B2ms", "BMS"),
        ("e2-standard-32", "E2"),
        (None, None),
        ("", None),
    ],
)
def test_familia_no_formato_da_chave_do_pool(tipo: str | None, familia: str | None) -> None:
    assert familia_da_maquina(tipo) == familia


def _payload() -> dict[str, Any]:
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "pools_computacao": [
            {
                "familia": "C7G",
                "regiao": "us-east-1",
                "instancias": 4,
                "capacidade": {"vcpus": 64.0, "ram_gb": 128.0},
                "custo_computacao": 1000.0,
                "instancias_medidas": 4,
                "equivalentes": [
                    {
                        "provedor": "GCP",
                        "sku": "e2-standard-16",
                        "instancias": 4,
                        "correspondencia_tecnica": {"vcpus": 64.0, "ram_gb": 256.0},
                    }
                ],
            },
            {
                "familia": "M5",
                "regiao": "us-east-1",
                "instancias": 2,
                "capacidade": {"vcpus": 8.0, "ram_gb": 32.0},
                "custo_computacao": 300.0,
                "equivalentes": [],
            },
        ],
        "custos_auxiliares": {
            "armazenamento_ssd": 5000.0,
            "banco_dados": 900.0,
            "rede_egress": 120.0,
            "equivalentes_banco_dados": {"GCP": 800.0},
        },
        "inventario_medido": {"armazenamento": {}},
        "topologia": {
            "versao": "oneshift-topologia-v1",
            "detalhe": {
                "posicionamento": [
                    {
                        "familia": "C7G",
                        "regiao": "us-east-1",
                        "posicionamentos": [
                            {"subrede": "s1", "instancias": 3, "vcpus": 48.0},
                            {"subrede": "s2", "instancias": 1, "vcpus": 16.0},
                        ],
                        "sem_posicionamento": 1,
                    },
                    {
                        "familia": "M5",
                        "regiao": "us-east-1",
                        "posicionamentos": [{"subrede": "s3", "instancias": 2, "vcpus": 8.0}],
                        "sem_posicionamento": 0,
                    },
                ],
                "discos": {"armazenamento_ssd": {"boot_gb": 100}},
                "bancos": [
                    {
                        "classe": "db.t4g.small",
                        "motor": "PostgreSQL",
                        "total": 10,
                        "encontrados": 8,
                    },
                    {"classe": "db.r6g.large", "motor": "PostgreSQL", "total": 2, "encontrados": 2},
                ],
                "bancos_sem_grupo": [
                    {"chave": "b1", "classe": None, "motor": "postgres"},
                    {"chave": "b2", "classe": None, "motor": "postgres"},
                ],
            },
        },
    }


def _recorte(**extra: Any) -> RecorteDoEscopo:
    base: dict[str, Any] = {
        "aplicacoes": ("loja",),
        "unidades": 5,
        "maquinas": {("s1", "C7G"): 2},
        "bancos": frozenset({"b1"}),
        "bancos_por_classe": {"db.t4g.small": 3},
        "custos": {"armazenamento_ssd": Decimal("250"), "banco_dados": Decimal("90")},
    }
    return RecorteDoEscopo(**{**base, **extra})


def test_pool_do_escopo_na_proporcao_e_o_resto_sai() -> None:
    novo = recortar_ao_escopo(_payload(), _recorte())
    [pool] = novo["pools_computacao"]
    # 2 das 3 máquinas da s1: 32 dos 64 vCPUs posicionados
    assert pool["familia"] == "C7G"
    assert pool["capacidade"] == {"vcpus": 32.0, "ram_gb": 64.0}
    assert pool["custo_computacao"] == 500.0
    assert pool["instancias"] == 2
    assert "instancias_medidas" not in pool
    [eq] = pool["equivalentes"]
    assert eq["instancias"] == 2
    assert eq["correspondencia_tecnica"] == {"vcpus": 32.0, "ram_gb": 128.0}
    [pos] = novo["topologia"]["detalhe"]["posicionamento"]
    assert pos["posicionamentos"] == [{"subrede": "s1", "instancias": 2, "vcpus": 32.0}]
    assert pos["sem_posicionamento"] == 0
    assert novo["topologia"]["escopo"]["pools_fora"] == ["M5"]


def test_fatia_sem_vcpus_pesa_pelas_maquinas() -> None:
    payload = _payload()
    for f in payload["topologia"]["detalhe"]["posicionamento"][0]["posicionamentos"]:
        f.pop("vcpus")
    novo = recortar_ao_escopo(payload, _recorte())
    [pool] = novo["pools_computacao"]
    assert pool["capacidade"]["vcpus"] == 32.0  # 2 de 4 máquinas
    [pos] = novo["topologia"]["detalhe"]["posicionamento"]
    assert "vcpus" not in pos["posicionamentos"][0]


def test_bancos_pela_classe_e_os_sem_grupo_pela_chave() -> None:
    detalhe = recortar_ao_escopo(_payload(), _recorte())["topologia"]["detalhe"]
    assert detalhe["bancos"] == [
        {"classe": "db.t4g.small", "motor": "PostgreSQL", "total": 3, "encontrados": 3}
    ]
    assert [b["chave"] for b in detalhe["bancos_sem_grupo"]] == ["b1"]


def test_custos_do_escopo_substituem_os_da_conta() -> None:
    novo = recortar_ao_escopo(
        _payload(),
        _recorte(custos={"armazenamento_ssd": Decimal("250"), "banco_dados": Decimal(0)}),
    )
    assert novo["custos_auxiliares"] == {
        "armazenamento_ssd": 250.0,
        "equivalentes_banco_dados": {"GCP": 800.0},
    }
    assert "inventario_medido" not in novo
    assert novo["topologia"]["detalhe"]["discos"] == {}


def test_secao_do_escopo_e_o_original_intacto() -> None:
    payload = _payload()
    original = deepcopy(payload)
    recorte = _recorte(
        ordens=((1, ("banco",)), (2, ("web",))),
        antes=(LigacaoEntreNuvens("cache", 1, ("6379",)),),
        para_tras=(LigacaoEntreNuvens("legado", None, ()),),
        avisos=("aviso",),
    )
    escopo = recortar_ao_escopo(payload, recorte)["topologia"]["escopo"]
    assert payload == original
    assert escopo == {
        "aplicacoes": ["loja"],
        "unidades": 5,
        "ordens": [{"ordem": 1, "grupos": ["banco"]}, {"ordem": 2, "grupos": ["web"]}],
        "antes": [{"grupo": "cache", "ordem": 1, "portas": ["6379"]}],
        "para_tras": [{"grupo": "legado", "ordem": None, "portas": []}],
        "pools_fora": ["M5"],
        "avisos": ["aviso"],
    }


def test_sem_maquina_no_escopo_o_plano_fica_sem_vm() -> None:
    novo = recortar_ao_escopo(_payload(), _recorte(maquinas={}))
    assert novo["pools_computacao"] == []
    plano = derivar_plano(novo, provedor_destino="gcp", regiao="us-central1")
    assert not [
        r
        for r in plano.recursos
        if r.resource_type == "compute" and not r.configuration.get("disk_only")
    ]
    # O disco do escopo continua, pelo custo das linhas dele
    assert [r for r in plano.recursos if r.configuration.get("disk_only")]
    assert novo["topologia"]["escopo"]["pools_fora"] == ["C7G", "M5"]


def test_o_plano_do_escopo_e_menor_que_o_da_conta() -> None:
    conta = derivar_plano(_payload(), provedor_destino="gcp", regiao="us-central1")
    escopo = derivar_plano(
        recortar_ao_escopo(_payload(), _recorte()), provedor_destino="gcp", regiao="us-central1"
    )

    def vms(plano: Any) -> int:
        return sum(
            int(r.configuration.get("instances") or 1)
            for r in plano.recursos
            if r.resource_type == "compute" and not r.configuration.get("disk_only")
        )

    assert 0 < vms(escopo) < vms(conta)


def test_pool_sem_posicionamento_pela_proporcao_do_inventario() -> None:
    """Fatura sem ID de recurso: nenhum pool tem posicionamento. A proporção vem das
    máquinas do inventário da mesma família e região, escopo sobre conta."""
    payload = _payload()
    payload["topologia"]["detalhe"]["posicionamento"] = []
    recorte = _recorte(
        maquinas={},
        por_familia={
            ("us-east-1", "C7G"): (1, 4),
            ("sa-east-1", "C7G"): (3, 3),  # outra região: não conta para o pool
            ("us-east-1", "M5"): (0, 2),
        },
    )
    novo = recortar_ao_escopo(payload, recorte)
    [pool] = novo["pools_computacao"]
    assert pool["familia"] == "C7G"
    assert pool["capacidade"] == {"vcpus": 16.0, "ram_gb": 32.0}
    assert pool["instancias"] == 1
    assert novo["topologia"]["escopo"]["pools_fora"] == ["M5"]


@pytest.mark.parametrize(
    ("do_pool", "do_recurso", "casa"),
    [
        ("us-central1", "us-central1-a", True),
        ("us-central1", "us-central1", True),
        ("us-east-1", "US-EAST-1", True),
        (None, "eastus", True),
        ("us-east-1", "us-east-2", False),
        ("us-east-1", None, False),
    ],
)
def test_regiao_do_pool_e_do_recurso(
    do_pool: str | None, do_recurso: str | None, casa: bool
) -> None:
    payload = _payload()
    payload["topologia"]["detalhe"]["posicionamento"] = []
    payload["pools_computacao"] = payload["pools_computacao"][:1]
    payload["pools_computacao"][0]["regiao"] = do_pool
    recorte = _recorte(maquinas={}, por_familia={(do_recurso, "C7G"): (1, 2)})
    assert bool(recortar_ao_escopo(payload, recorte)["pools_computacao"]) is casa


def test_grupos_de_banco_medidos_ficam_na_quantidade_do_escopo() -> None:
    """Sem eles o pacote caía num banco genérico, fora da rede nova (18/10)."""
    payload = _payload()
    payload["inventario_medido"] = {
        "armazenamento": {"storage_ssd": {"gb": 999}},
        "bancos": [
            {"classe": "db.t4g.small", "motor": "PostgreSQL", "quantidade": 38, "multi_az": True},
            {"classe": "db.r8g.4xlarge", "motor": "Aurora PostgreSQL", "quantidade": 2},
        ],
        "total_bancos": 165,
    }
    novo = recortar_ao_escopo(payload, _recorte())
    # O recorte tem 3 db.t4g.small e nenhum db.r8g.4xlarge; o armazenamento medido sai
    assert novo["inventario_medido"] == {
        "bancos": [
            {"classe": "db.t4g.small", "motor": "PostgreSQL", "quantidade": 3, "multi_az": True}
        ],
        "total_bancos": 3,
    }
