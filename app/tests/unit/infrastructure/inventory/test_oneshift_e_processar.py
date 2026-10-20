"""Formato canônico e a junção de arquivos de um envio (I2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.entities.topologia import (
    Direcao,
    EstadoDoRecurso,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.infrastructure.inventory_ingestion.leitura import (
    InventarioInvalidoError,
    LimiteExcedidoError,
    Limites,
)
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos

_VCN = "ocid1.vcn.oc1.sa-saopaulo-1.vcn"
_SUB = "ocid1.subnet.oc1.sa-saopaulo-1.app"
_NSG_APP = "ocid1.networksecuritygroup.oc1.sa-saopaulo-1.app"
_NSG_DB = "ocid1.networksecuritygroup.oc1.sa-saopaulo-1.db"
_VM = "ocid1.instance.oc1.sa-saopaulo-1.app1"


def _doc(**extra) -> dict:
    base = {
        "formato": "oneshift-inventory-v1",
        "provedor": "oci",
        "capturado_em": "2026-03-31T12:00:00Z",
        "recursos": [
            {"id": _VCN, "tipo": "rede", "atributos": {"cidr": "10.30.0.0/16"}},
            {"id": _SUB, "tipo": "subrede", "atributos": {"cidr": "10.30.1.0/24"}},
            {"id": _NSG_APP, "tipo": "grupo_seguranca"},
            {
                "id": _NSG_DB,
                "tipo": "grupo_seguranca",
                "atributos": {
                    "regras": [
                        {
                            "protocolo": "tcp",
                            "portas": [5432, 5432],
                            "origem": {"tipo": "grupo", "valor": _NSG_APP},
                        },
                        {
                            "protocolo": "6",
                            "portas": [22, 22],
                            "origem": {"tipo": "cidr", "valor": "10.30.1.7"},
                        },
                    ]
                },
            },
            {
                "id": _VM,
                "tipo": "instancia",
                "tipo_nativo": "Instance",
                "nome": "app-1",
                "estado": "ativo",
                "regiao": "sa-saopaulo-1",
                "atributos": {
                    "tipo_maquina": "VM.Standard.E4.Flex",
                    "vcpus": 4,
                    "memoria_gib": 64,
                    "ip_privado": "10.30.1.5",
                },
                "tags": {"app": "checkout"},
            },
        ],
        "relacoes": [
            {"de": _SUB, "para": _VCN, "tipo": "contido_em"},
            {"de": _VM, "para": _SUB, "tipo": "contido_em"},
            {"de": _VM, "para": _NSG_APP, "tipo": "protegido_por"},
            {"de": _VM, "para": "ocid1.volume.fora", "tipo": "anexado_a"},
        ],
    }
    base.update(extra)
    return base


def _arquivo(tmp_path: Path, nome: str, conteudo: object) -> Path:
    arq = tmp_path / nome
    arq.write_text(json.dumps(conteudo))
    return arq


def _norm(*arquivos: Path, limites: Limites | None = None):
    return normalizar_arquivos([(a.name, a) for a in arquivos], limites)


def test_documento_canonico(tmp_path) -> None:
    resultado = _norm(_arquivo(tmp_path, "oci.json", _doc()))
    inv = resultado.inventario
    assert resultado.formatos == {"oci.json": "oneshift_canonico"}
    assert inv.provedor == "oci"
    vm = next(r for r in inv.recursos if r.chave == _VM)
    assert vm.estado is EstadoDoRecurso.ATIVO
    assert vm.atributos["memoria_gib"] == 64
    assert vm.capturado_em is not None  # herdado do documento
    assert {(r.de, r.tipo) for r in inv.relacoes} == {
        (_SUB, TipoDeRelacao.CONTIDO_EM),
        (_VM, TipoDeRelacao.CONTIDO_EM),
        (_VM, TipoDeRelacao.PROTEGIDO_POR),
    }
    assert inv.contagens["referencia_externa:recurso"] == 1


def test_regras_no_grupo_canonico(tmp_path) -> None:
    inv = _norm(_arquivo(tmp_path, "oci.json", _doc())).inventario
    regras = sorted(inv.regras, key=lambda r: r.porta_inicio or 0)
    assert [r.grupo for r in regras] == [_NSG_DB, _NSG_DB]
    assert regras[0].origem == Seletor(TipoDeSeletor.CIDR, "10.30.1.7/32")
    assert regras[0].protocolo == "tcp"  # "6" normalizado
    assert regras[1].origem == Seletor(TipoDeSeletor.GRUPO, _NSG_APP)
    assert regras[1].direcao is Direcao.ENTRADA
    # A regra não fica nos atributos: tem lugar próprio.
    nsg = next(r for r in inv.recursos if r.chave == _NSG_DB)
    assert "regras" not in nsg.atributos


@pytest.mark.parametrize(
    ("mudanca", "trecho"),
    [
        ({"provedor": "ibm"}, "provedor"),
        ({"recursos": [{"id": "x", "tipo": "maquina"}]}, "recursos.0.tipo"),
        ({"recursos": [{"id": "", "tipo": "instancia"}]}, "recursos.0.id"),
        ({"relacoes": [{"de": "a", "para": "b", "tipo": "liga"}]}, "relacoes.0.tipo"),
        ({"campo_estranho": 1}, "campo_estranho"),
    ],
)
def test_documento_canonico_invalido_diz_onde(tmp_path, mudanca, trecho) -> None:
    with pytest.raises(InventarioInvalidoError, match=trecho):
        _norm(_arquivo(tmp_path, "x.json", _doc(**mudanca)))


def test_regra_canonica_invalida(tmp_path) -> None:
    doc = _doc()
    doc["recursos"][3]["atributos"]["regras"].append(
        {"portas": [80, 22], "origem": {"tipo": "qualquer"}}
    )
    with pytest.raises(InventarioInvalidoError, match="regra 2 do grupo"):
        _norm(_arquivo(tmp_path, "x.json", doc))


def test_cidr_invalido_no_seletor(tmp_path) -> None:
    doc = _doc()
    doc["recursos"][3]["atributos"]["regras"] = [
        {"origem": {"tipo": "cidr", "valor": "999.1.1.1/40"}}
    ]
    with pytest.raises(InventarioInvalidoError, match="cidr"):
        _norm(_arquivo(tmp_path, "x.json", doc))


def test_dois_canonicos_de_provedores_diferentes(tmp_path) -> None:
    a = _arquivo(tmp_path, "a.json", _doc())
    b = _arquivo(tmp_path, "b.json", _doc(provedor="aws"))
    with pytest.raises(InventarioInvalidoError, match="provedor aws"):
        _norm(a, b)


# ── junção de formatos ──────────────────────────────────────────────────


def _config(rid: str, tipo: str = "AWS::EC2::Instance") -> dict:
    return {
        "resourceId": rid,
        "resourceType": tipo,
        "accountId": "1",
        "awsRegion": "us-east-1",
        "arn": f"arn:aws:ec2:us-east-1:1:instance/{rid}",
        "configurationItemCaptureTime": "2026-03-01T00:00:00Z",
        "configuration": {"state": {"name": "running"}, "instanceType": "m6i.large"},
    }


def test_formatos_do_mesmo_provedor_se_juntam(tmp_path) -> None:
    config = _arquivo(tmp_path, "snap.json", {"configurationItems": [_config("i-1")]})
    extra = _doc(
        provedor="aws",
        capturado_em="2026-03-02T00:00:00Z",
        recursos=[
            {
                "id": "arn:aws:ec2:us-east-1:1:instance/i-1",
                "tipo": "instancia",
                "atributos": {"tipo_maquina": "m6i.xlarge"},
            },
            {"id": "servidor-no-datacenter", "tipo": "instancia"},
        ],
        relacoes=[],
    )
    canonico = _arquivo(tmp_path, "extra.json", extra)
    resultado = _norm(config, canonico)
    inv = resultado.inventario
    assert resultado.formatos == {"snap.json": "aws_config", "extra.json": "oneshift_canonico"}
    assert len(inv.recursos) == 2
    # O mesmo recurso nos dois: fica o de captura mais recente (o canônico),
    # com os apelidos dos dois.
    vm = next(r for r in inv.recursos if "i-1" in r.apelidos)
    assert vm.atributos["tipo_maquina"] == "m6i.xlarge"
    assert inv.contagens["duplicado"] == 1


def test_nuvens_diferentes_no_mesmo_envio(tmp_path) -> None:
    aws = _arquivo(tmp_path, "snap.json", {"configurationItems": [_config("i-1")]})
    oci = _arquivo(tmp_path, "oci.json", _doc())
    with pytest.raises(InventarioInvalidoError, match="nuvens diferentes"):
        _norm(aws, oci)


def test_formato_desconhecido(tmp_path) -> None:
    arq = _arquivo(tmp_path, "billing.json", {"linhas": [{"cost": 1}]})
    with pytest.raises(InventarioInvalidoError, match="não reconhecido"):
        _norm(arq)


def test_lista_vazia(tmp_path) -> None:
    arq = _arquivo(tmp_path, "vazio.json", [])
    with pytest.raises(InventarioInvalidoError, match="sem recursos"):
        _norm(arq)


def test_limite_de_recursos(tmp_path) -> None:
    arq = _arquivo(
        tmp_path, "snap.json", {"configurationItems": [_config(f"i-{i}") for i in range(5)]}
    )
    with pytest.raises(LimiteExcedidoError, match="limite é 4"):
        _norm(arq, limites=Limites(recursos=4))


def test_reconhecimento_pelo_conteudo_nao_pelo_nome(tmp_path) -> None:
    # Um snapshot do Config com nome de arquivo de Azure continua sendo Config.
    arq = _arquivo(tmp_path, "azure_resource_graph.json", {"configurationItems": [_config("i-9")]})
    resultado = _norm(arq)
    assert resultado.inventario.provedor == "aws"
    assert resultado.inventario.recursos[0].tipo is TipoDeRecurso.INSTANCIA
