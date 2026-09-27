"""Normalizador do Cloud Asset Inventory contra o formato documentado (I2).

O export para Cloud Storage usa as chaves do proto (`asset_type`,
`update_time`); o `gcloud asset list --format=json` usa camelCase. O
`resource.data` é o recurso REST do serviço nas duas.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.entities.topologia import (
    Acao,
    Direcao,
    EstadoDoRecurso,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.infrastructure.inventory_ingestion.normalizadores.gcp_asset import _versao_cloud_sql
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos

_P = "loja-prod"
_API = "https://www.googleapis.com/compute/v1"
_REDE = f"//compute.googleapis.com/projects/{_P}/global/networks/vpc-loja"
_SUB = f"//compute.googleapis.com/projects/{_P}/regions/us-central1/subnetworks/app"
_VM = f"//compute.googleapis.com/projects/{_P}/zones/us-central1-a/instances/web-1"
_BANCO_VM = f"//compute.googleapis.com/projects/{_P}/zones/us-central1-a/instances/pg-1"
_ZONA_API = f"{_API}/projects/{_P}/zones/us-central1-a"
_DISCO = f"//compute.googleapis.com/projects/{_P}/zones/us-central1-a/disks/web-1"
_SOLTO = f"//compute.googleapis.com/projects/{_P}/zones/us-central1-a/disks/sobra"


def _asset(nome: str, tipo: str, data: dict, local: str | None, camel: bool = False) -> dict:
    recurso = {
        "version": "v1",
        "data": data,
        "location": local,
        "parent": "//cloudresourcemanager.googleapis.com/projects/123",
    }
    if camel:
        return {
            "name": nome,
            "assetType": tipo,
            "resource": {**recurso, "discoveryName": "x"},
            "updateTime": "2025-12-30T12:00:00Z",
            "ancestors": ["projects/123"],
        }
    return {
        "name": nome,
        "asset_type": tipo,
        "resource": {**recurso, "discovery_name": "x"},
        "update_time": "2025-12-30T12:00:00Z",
        "ancestors": ["projects/123"],
    }


def _assets(camel: bool = False) -> list[dict]:
    return [
        _asset(
            _REDE,
            "compute.googleapis.com/Network",
            {"name": "vpc-loja", "autoCreateSubnetworks": False},
            "global",
            camel,
        ),
        _asset(
            _SUB,
            "compute.googleapis.com/Subnetwork",
            {
                "name": "app",
                "ipCidrRange": "10.10.1.0/24",
                "network": f"{_API}/projects/{_P}/global/networks/vpc-loja",
            },
            "us-central1",
            camel,
        ),
        _asset(
            _VM,
            "compute.googleapis.com/Instance",
            {
                "id": "4567890123456789012",
                "name": "web-1",
                "status": "RUNNING",
                "machineType": f"{_ZONA_API}/machineTypes/n2-standard-8",
                "networkInterfaces": [
                    {
                        "network": f"{_API}/projects/{_P}/global/networks/vpc-loja",
                        "subnetwork": f"{_API}/projects/{_P}/regions/us-central1/subnetworks/app",
                        "networkIP": "10.10.1.5",
                        "accessConfigs": [{"natIP": "34.1.2.3", "type": "ONE_TO_ONE_NAT"}],
                    }
                ],
                "disks": [
                    {
                        "source": f"{_API}/projects/{_P}/zones/us-central1-a/disks/web-1",
                        "boot": True,
                    }
                ],
                "tags": {"items": ["web"]},
                "labels": {"app": "checkout", "env": "prod"},
                "serviceAccounts": [{"email": "web@loja-prod.iam.gserviceaccount.com"}],
            },
            "us-central1-a",
            camel,
        ),
        _asset(
            _BANCO_VM,
            "compute.googleapis.com/Instance",
            {
                "id": "999",
                "name": "pg-1",
                "status": "TERMINATED",
                "machineType": f"{_API}/projects/{_P}/zones/us-central1-a/machineTypes/e2-medium",
                "networkInterfaces": [
                    {
                        "network": f"{_API}/projects/{_P}/global/networks/vpc-loja",
                        "subnetwork": f"{_API}/projects/{_P}/regions/us-central1/subnetworks/app",
                        "networkIP": "10.10.1.9",
                    }
                ],
                "tags": {"items": ["db"]},
            },
            "us-central1-a",
            camel,
        ),
        _asset(
            _DISCO,
            "compute.googleapis.com/Disk",
            {
                "id": "1111",
                "name": "web-1",
                "sizeGb": "100",
                "type": f"{_API}/projects/{_P}/zones/us-central1-a/diskTypes/pd-balanced",
                "users": [f"{_API}/projects/{_P}/zones/us-central1-a/instances/web-1"],
                "status": "READY",
            },
            "us-central1-a",
            camel,
        ),
        _asset(
            _SOLTO,
            "compute.googleapis.com/Disk",
            {
                "id": "2222",
                "name": "sobra",
                "sizeGb": "500",
                "type": f"{_API}/projects/{_P}/zones/us-central1-a/diskTypes/pd-standard",
                "status": "READY",
            },
            "us-central1-a",
            camel,
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/global/firewalls/web-para-db",
            "compute.googleapis.com/Firewall",
            {
                "name": "web-para-db",
                "network": f"{_API}/projects/{_P}/global/networks/vpc-loja",
                "direction": "INGRESS",
                "priority": 1000,
                "sourceTags": ["web"],
                "targetTags": ["db"],
                "allowed": [{"IPProtocol": "tcp", "ports": ["5432", "6000-6100"]}],
            },
            "global",
            camel,
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/global/firewalls/ssh-mundo",
            "compute.googleapis.com/Firewall",
            {
                "name": "ssh-mundo",
                "network": f"{_API}/projects/{_P}/global/networks/vpc-loja",
                "sourceRanges": ["0.0.0.0/0"],
                "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
            },
            "global",
            camel,
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/global/firewalls/desligada",
            "compute.googleapis.com/Firewall",
            {
                "name": "desligada",
                "disabled": True,
                "network": f"{_API}/projects/{_P}/global/networks/vpc-loja",
                "sourceRanges": ["10.0.0.0/8"],
                "allowed": [{"IPProtocol": "all"}],
            },
            "global",
            camel,
        ),
        _asset(
            f"//cloudsql.googleapis.com/projects/{_P}/instances/pedidos",
            "sqladmin.googleapis.com/Instance",
            {
                "name": "pedidos",
                "databaseVersion": "POSTGRES_15",
                "state": "RUNNABLE",
                "connectionName": f"{_P}:us-central1:pedidos",
                "settings": {
                    "tier": "db-custom-4-16384",
                    "availabilityType": "REGIONAL",
                    "dataDiskSizeGb": "250",
                    "ipConfiguration": {
                        "ipv4Enabled": False,
                        "privateNetwork": f"projects/{_P}/global/networks/vpc-loja",
                    },
                },
            },
            "us-central1",
            camel,
        ),
    ]


def _ndjson(tmp_path: Path, assets: list[dict]) -> Path:
    arq = tmp_path / "assets.json"
    arq.write_text("\n".join(json.dumps(a) for a in assets))
    return arq


def _inv(tmp_path: Path, camel: bool = False):
    if camel:
        arq = tmp_path / "lista.json"
        arq.write_text(json.dumps(_assets(camel=True), indent=2))
    else:
        arq = _ndjson(tmp_path, _assets())
    resultado = normalizar_arquivos([(arq.name, arq)])
    assert set(resultado.formatos.values()) == {"gcp_asset_inventory"}
    return resultado.inventario


def _r(inv, chave):
    return next(r for r in inv.recursos if r.chave == chave)


def test_as_duas_grafias_dao_o_mesmo_inventario(tmp_path) -> None:
    snake = _inv(tmp_path)
    outro = tmp_path / "camel"
    outro.mkdir()
    camel = _inv(outro, camel=True)
    assert snake.recursos == camel.recursos
    assert snake.relacoes == camel.relacoes
    assert snake.regras == camel.regras


def test_vm_com_tipo_ip_e_apelido_numerico(tmp_path) -> None:
    inv = _inv(tmp_path)
    vm = _r(inv, _VM)
    assert vm.tipo is TipoDeRecurso.INSTANCIA
    assert vm.conta == _P
    assert (vm.regiao, vm.zona) == ("us-central1", "us-central1-a")
    assert vm.atributos["tipo_maquina"] == "n2-standard-8"
    assert vm.atributos["ip_privado"] == "10.10.1.5"
    assert vm.atributos["ip_publico"] == "34.1.2.3"
    assert vm.tags == {"app": "checkout", "env": "prod"}
    # É assim que o billing export cita a VM (`resource.global_name`).
    numerico = (
        f"//compute.googleapis.com/projects/{_P}/zones/us-central1-a/instances/4567890123456789012"
    )
    assert numerico in vm.apelidos
    assert _r(inv, _BANCO_VM).estado is EstadoDoRecurso.PARADO


def test_self_links_viram_relacoes(tmp_path) -> None:
    inv = _inv(tmp_path)
    rel = {(r.de, r.para, r.tipo) for r in inv.relacoes}
    assert (_VM, _SUB, TipoDeRelacao.CONTIDO_EM) in rel
    assert (_SUB, _REDE, TipoDeRelacao.CONTIDO_EM) in rel
    assert (_DISCO, _VM, TipoDeRelacao.ANEXADO_A) in rel
    sql = f"//cloudsql.googleapis.com/projects/{_P}/instances/pedidos"
    assert (sql, _REDE, TipoDeRelacao.CONTIDO_EM) in rel


def test_discos(tmp_path) -> None:
    inv = _inv(tmp_path)
    disco = _r(inv, _DISCO)
    assert disco.atributos == {
        "tamanho_gb": 100,
        "tipo_disco": "pd-balanced",
        "categoria_disco": "ssd",
        "boot": True,
    }
    solto = _r(inv, _SOLTO)
    assert solto.estado is EstadoDoRecurso.DISPONIVEL
    assert solto.atributos["categoria_disco"] == "hdd"


def test_firewall_vira_grupos_sinteticos_com_membros(tmp_path) -> None:
    inv = _inv(tmp_path)
    web = f"gcp:{_REDE}:tag:web"
    db = f"gcp:{_REDE}:tag:db"
    toda = f"gcp:{_REDE}:todas"
    grupos = {r.chave: r for r in inv.recursos if r.tipo is TipoDeRecurso.GRUPO_SEGURANCA}
    assert set(grupos) == {web, db, toda}
    assert grupos[toda].atributos["amplo"] is True
    membros = {(r.de, r.para) for r in inv.relacoes if r.tipo is TipoDeRelacao.PROTEGIDO_POR}
    assert (_VM, web) in membros
    assert (_BANCO_VM, db) in membros
    assert (_VM, db) not in membros
    # Firewall sem alvo vale para toda VM da rede.
    assert {(_VM, toda), (_BANCO_VM, toda)} <= membros


def test_regras_do_firewall(tmp_path) -> None:
    inv = _inv(tmp_path)
    db = f"gcp:{_REDE}:tag:db"
    web = f"gcp:{_REDE}:tag:web"
    regras = [r for r in inv.regras if r.grupo == db]
    assert {(r.porta_inicio, r.porta_fim) for r in regras} == {(5432, 5432), (6000, 6100)}
    assert all(r.origem == Seletor(TipoDeSeletor.GRUPO, web) for r in regras)
    assert all(r.direcao is Direcao.ENTRADA and r.acao is Acao.PERMITIR for r in regras)
    [ssh] = [r for r in inv.regras if r.grupo == f"gcp:{_REDE}:todas"]
    assert ssh.origem == Seletor(TipoDeSeletor.QUALQUER)
    assert ssh.prioridade is None
    assert inv.contagens["regra_desabilitada"] == 1
    assert inv.contagens["firewall"] == 3


def test_cloud_sql(tmp_path) -> None:
    inv = _inv(tmp_path)
    sql = _r(inv, f"//cloudsql.googleapis.com/projects/{_P}/instances/pedidos")
    assert sql.tipo is TipoDeRecurso.BANCO
    assert sql.atributos["motor"] == "postgres"
    assert sql.atributos["versao"] == "15"
    assert sql.atributos["classe"] == "db-custom-4-16384"
    assert sql.atributos["alta_disponibilidade"] is True
    assert sql.atributos["publico"] is False
    assert f"{_P}:us-central1:pedidos" in sql.apelidos


def test_versoes_do_cloud_sql() -> None:
    assert _versao_cloud_sql("MYSQL_8_0") == ("mysql", "8.0")
    assert _versao_cloud_sql("SQLSERVER_2019_STANDARD") == ("sqlserver", "2019")
    assert _versao_cloud_sql("POSTGRES_9_6") == ("postgres", "9.6")
    assert _versao_cloud_sql(None) == (None, None)


def test_no_do_gke_entra_no_cluster(tmp_path) -> None:
    cluster = _asset(
        f"//container.googleapis.com/projects/{_P}/locations/us-central1/clusters/loja",
        "container.googleapis.com/Cluster",
        {
            "name": "loja",
            "location": "us-central1",
            "currentMasterVersion": "1.29.1",
            "networkConfig": {
                "network": f"projects/{_P}/global/networks/vpc-loja",
                "subnetwork": f"projects/{_P}/regions/us-central1/subnetworks/app",
            },
        },
        "us-central1",
    )
    no = _asset(
        f"//compute.googleapis.com/projects/{_P}/zones/us-central1-a/instances/gke-no-1",
        "compute.googleapis.com/Instance",
        {
            "id": "5",
            "name": "gke-no-1",
            "status": "RUNNING",
            "labels": {"goog-k8s-cluster-name": "loja"},
        },
        "us-central1-a",
    )
    arq = _ndjson(tmp_path, [*_assets()[:2], cluster, no])
    inv = normalizar_arquivos([(arq.name, arq)]).inventario
    alvo = cluster["name"]
    assert any(r.para == alvo and r.tipo is TipoDeRelacao.MEMBRO_DE for r in inv.relacoes)
    assert any(r.de == alvo and r.para == _SUB for r in inv.relacoes)


def test_um_asset_so_no_arquivo(tmp_path) -> None:
    arq = tmp_path / "um.json"
    arq.write_text(json.dumps(_assets()[0]))
    inv = normalizar_arquivos([(arq.name, arq)]).inventario
    assert [r.chave for r in inv.recursos] == [_REDE]
