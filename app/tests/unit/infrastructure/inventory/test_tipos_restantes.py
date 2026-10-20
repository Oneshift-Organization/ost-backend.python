"""Os tipos menos comuns de cada provedor: nenhum derruba a normalização, e
cada um cai no tipo canônico certo com os atributos que a topologia usa."""

from __future__ import annotations

import json

import pytest

from app.domain.entities.topologia import EstadoDoRecurso, TipoDeRecurso, TipoDeRelacao
from app.infrastructure.inventory_ingestion.normalizadores import comum
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos


def _norm(tmp_path, conteudo):
    arq = tmp_path / "x.json"
    arq.write_text(json.dumps(conteudo))
    return normalizar_arquivos([(arq.name, arq)]).inventario


def _ci(tipo, rid, config, **extra):
    return {
        "resourceId": rid,
        "resourceType": tipo,
        "accountId": "1",
        "awsRegion": "us-east-1",
        "configurationItemStatus": "OK",
        "configuration": config,
        **extra,
    }


def test_aws_tipos_restantes(tmp_path) -> None:
    itens = [
        _ci(
            "AWS::EC2::VPC",
            "vpc-1",
            {"cidrBlock": "10.0.0.0/16", "cidrBlockAssociationSet": [{"cidrBlock": "10.1.0.0/16"}]},
        ),
        _ci("AWS::EC2::Subnet", "subnet-1", {"cidrBlock": "10.0.1.0/24", "vpcId": "vpc-1"}),
        _ci("AWS::EC2::SecurityGroup", "sg-1", {"groupId": "sg-1", "vpcId": "vpc-1"}),
        _ci("AWS::EC2::NatGateway", "nat-1", {"natGatewayId": "nat-1", "subnetId": "subnet-1"}),
        _ci("AWS::EC2::InternetGateway", "igw-1", {"attachments": [{"vpcId": "vpc-1"}]}),
        _ci(
            "AWS::EC2::NetworkInterface",
            "eni-1",
            {
                "subnetId": "subnet-1",
                "groups": [{"groupId": "sg-1"}],
                "status": "available",
                "privateIpAddress": "10.0.1.9",
                "association": {"publicIp": "3.3.3.3"},
            },
        ),
        _ci(
            "AWS::ElastiCache::CacheCluster",
            "sessao",
            {
                "cacheClusterId": "sessao",
                "cacheNodeType": "cache.r6g.large",
                "engine": "redis",
                "engineVersion": "7.0",
                "numCacheNodes": 2,
                "securityGroups": [{"securityGroupId": "sg-1"}],
            },
        ),
        _ci("AWS::S3::Bucket", "meu-bucket", {"name": "meu-bucket"}),
        _ci(
            "AWS::EKS::Cluster",
            "k8s",
            {
                "name": "k8s",
                "version": "1.29",
                "resourcesVpcConfig": {"subnetIds": ["subnet-1"], "clusterSecurityGroupId": "sg-1"},
            },
        ),
        _ci(
            "AWS::EC2::Instance",
            "i-parada",
            {"state": {"name": "stopped"}, "cpuOptions": {"coreCount": 2, "threadsPerCore": 2}},
        ),
        {"resourceType": "AWS::EC2::Volume"},  # sem id
    ]
    inv = _norm(tmp_path, {"configurationItems": itens})
    por = {r.tipo_nativo: r for r in inv.recursos}
    assert por["AWS::EC2::VPC"].atributos["cidrs"] == ["10.0.0.0/16", "10.1.0.0/16"]
    assert por["AWS::EC2::NatGateway"].tipo is TipoDeRecurso.GATEWAY
    assert por["AWS::EC2::NetworkInterface"].estado is EstadoDoRecurso.DISPONIVEL
    assert por["AWS::EC2::NetworkInterface"].atributos["ip_publico"] == "3.3.3.3"
    cache = por["AWS::ElastiCache::CacheCluster"]
    assert (cache.tipo, cache.atributos["motor"], cache.atributos["nos"]) == (
        TipoDeRecurso.CACHE,
        "redis",
        2,
    )
    assert por["AWS::S3::Bucket"].tipo is TipoDeRecurso.BUCKET
    assert "meu-bucket" in por["AWS::S3::Bucket"].apelidos
    assert por["AWS::EKS::Cluster"].atributos["versao"] == "1.29"
    parada = por["AWS::EC2::Instance"]
    assert parada.estado is EstadoDoRecurso.PARADO
    assert parada.atributos["vcpus"] == 4
    assert inv.contagens["item_sem_identidade"] == 1
    tipos_rel = {(r.de.split(":")[-1], r.para.split(":")[-1], r.tipo) for r in inv.relacoes}
    assert ("igw-1", "vpc-1", TipoDeRelacao.CONTIDO_EM) in tipos_rel
    assert ("nat-1", "subnet-1", TipoDeRelacao.CONTIDO_EM) in tipos_rel
    assert ("k8s", "sg-1", TipoDeRelacao.PROTEGIDO_POR) in tipos_rel


_P = "p1"
_API = "https://www.googleapis.com/compute/v1"


def _asset(nome, tipo, data, local="us-central1"):
    return {
        "name": nome,
        "asset_type": tipo,
        "resource": {"data": data, "location": local},
        "update_time": "2025-12-01T00:00:00Z",
    }


def test_gcp_tipos_restantes(tmp_path) -> None:
    rede = f"//compute.googleapis.com/projects/{_P}/global/networks/n"
    assets = [
        _asset(rede, "compute.googleapis.com/Network", {"name": "n"}, "global"),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/regions/us-central1/addresses/ip1",
            "compute.googleapis.com/Address",
            {"address": "35.1.1.1", "addressType": "EXTERNAL", "status": "RESERVED"},
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/regions/us-central1/addresses/ip2",
            "compute.googleapis.com/Address",
            {"address": "10.0.0.2", "addressType": "INTERNAL"},
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/regions/us-central1/forwardingRules/lb",
            "compute.googleapis.com/ForwardingRule",
            {
                "loadBalancingScheme": "INTERNAL",
                "network": f"{_API}/projects/{_P}/global/networks/n",
            },
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/regions/us-central1/routers/r",
            "compute.googleapis.com/Router",
            {"network": f"{_API}/projects/{_P}/global/networks/n"},
        ),
        _asset(
            f"//redis.googleapis.com/projects/{_P}/locations/us-central1/instances/cache",
            "redis.googleapis.com/Instance",
            {
                "tier": "STANDARD_HA",
                "memorySizeGb": 5,
                "redisVersion": "REDIS_7_0",
                "authorizedNetwork": f"projects/{_P}/global/networks/n",
                "state": "READY",
            },
        ),
        _asset(
            f"//storage.googleapis.com/{_P}-dados",
            "storage.googleapis.com/Bucket",
            {"name": f"{_P}-dados", "storageClass": "NEARLINE"},
            "US",
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/global/snapshots/s1",
            "compute.googleapis.com/Snapshot",
            {
                "diskSizeGb": "100",
                "sourceDisk": f"{_API}/projects/{_P}/zones/us-central1-a/disks/x",
            },
            "global",
        ),
        _asset(
            f"//iam.googleapis.com/projects/{_P}/serviceAccounts/sa",
            "iam.googleapis.com/ServiceAccount",
            {},
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/global/firewalls/sem-rede",
            "compute.googleapis.com/Firewall",
            {"allowed": [{"IPProtocol": "tcp"}]},
            "global",
        ),
        _asset(
            f"//compute.googleapis.com/projects/{_P}/global/firewalls/saida",
            "compute.googleapis.com/Firewall",
            {
                "network": f"{_API}/projects/{_P}/global/networks/n",
                "direction": "EGRESS",
                "destinationRanges": ["0.0.0.0/0"],
                "denied": [{"IPProtocol": "all"}],
                "targetServiceAccounts": ["a@b"],
            },
            "global",
        ),
        {"name": "", "asset_type": "x"},
    ]
    arq = tmp_path / "a.json"
    arq.write_text("\n".join(json.dumps(a) for a in assets))
    inv = normalizar_arquivos([(arq.name, arq)]).inventario
    por = {r.tipo_nativo + "|" + (r.nome or ""): r for r in inv.recursos}
    ip1 = por["compute.googleapis.com/Address|ip1"]
    assert (ip1.tipo, ip1.estado) == (TipoDeRecurso.IP_PUBLICO, EstadoDoRecurso.DISPONIVEL)
    assert por["compute.googleapis.com/Address|ip2"].tipo is TipoDeRecurso.OUTRO
    lb = por["compute.googleapis.com/ForwardingRule|lb"]
    assert (lb.tipo, lb.atributos["publico"]) == (TipoDeRecurso.BALANCEADOR, False)
    assert por["compute.googleapis.com/Router|r"].tipo is TipoDeRecurso.GATEWAY
    cache = por["redis.googleapis.com/Instance|cache"]
    assert cache.atributos["alta_disponibilidade"] is True
    assert por[f"storage.googleapis.com/Bucket|{_P}-dados"].tipo is TipoDeRecurso.BUCKET
    assert inv.contagens["firewall_sem_rede"] == 1
    assert inv.contagens["item_sem_identidade"] == 1
    assert inv.contagens["tipo_nao_modelado:iam.googleapis.com/ServiceAccount"] == 1
    saida = [r for r in inv.regras if r.grupo.endswith(":sa:a@b")]
    assert saida and saida[0].direcao.value == "saida" and saida[0].acao.value == "negar"


def test_azure_tipos_restantes(tmp_path) -> None:
    g = "/subscriptions/s/resourceGroups/rg/providers"
    sub = f"{g}/Microsoft.Network/virtualNetworks/v/subnets/s"
    linhas = [
        {
            "id": f"{g}/Microsoft.Network/virtualNetworks/v",
            "type": "Microsoft.Network/virtualNetworks",
            "properties": {
                "subnets": [
                    {
                        "id": sub,
                        "name": "s",
                        "properties": {
                            "addressPrefix": "10.0.0.0/24",
                            "natGateway": {"id": f"{g}/Microsoft.Network/natGateways/nat"},
                        },
                    }
                ]
            },
        },
        {
            "id": f"{g}/Microsoft.Network/natGateways/nat",
            "type": "microsoft.network/natgateways",
            "properties": {"subnets": [{"id": sub}]},
        },
        {
            "id": f"{g}/Microsoft.Network/virtualNetworkGateways/vpn",
            "type": "microsoft.network/virtualnetworkgateways",
            "properties": {"ipConfigurations": [{"properties": {"subnet": {"id": sub}}}]},
        },
        {
            "id": f"{g}/Microsoft.Sql/servers/srv/databases/db",
            "type": "microsoft.sql/servers/databases",
            "sku": {"name": "GP_Gen5_4", "tier": "GeneralPurpose", "family": "Gen5", "capacity": 4},
            "properties": {"zoneRedundant": True},
        },
        {
            "id": f"{g}/Microsoft.Cache/Redis/r",
            "type": "microsoft.cache/redis",
            "properties": {
                "sku": {"family": "C", "capacity": 1},
                "redisVersion": "6",
                "subnetId": sub,
                "publicNetworkAccess": "Disabled",
            },
        },
        {
            "id": f"{g}/Microsoft.ContainerService/managedClusters/aks",
            "type": "microsoft.containerservice/managedclusters",
            "properties": {
                "kubernetesVersion": "1.29",
                "agentPoolProfiles": [{"vnetSubnetID": sub}],
            },
        },
        {
            "id": f"{g}/Microsoft.Storage/storageAccounts/sa",
            "type": "microsoft.storage/storageaccounts",
            "properties": {"allowBlobPublicAccess": False},
        },
        {
            "id": f"{g}/Microsoft.Network/publicIPAddresses/solto",
            "type": "microsoft.network/publicipaddresses",
            "properties": {"ipAddress": "20.0.0.9"},
        },
        {
            "id": f"{g}/Microsoft.Compute/virtualMachines/desligada",
            "type": "microsoft.compute/virtualmachines",
            "properties": {
                "extended": {"instanceView": {"powerState": {"code": "PowerState/deallocated"}}}
            },
        },
        {
            "id": f"{g}/Microsoft.Web/sites/app",
            "type": "microsoft.web/sites",
            "properties": "{nao json",
        },
        {"id": "", "type": "x"},
    ]
    inv = _norm(tmp_path, {"data": linhas})
    por = {r.tipo_nativo: r for r in inv.recursos}
    assert por["microsoft.network/natgateways"].tipo is TipoDeRecurso.GATEWAY
    sql = por["microsoft.sql/servers/databases"]
    assert (
        sql.atributos["motor"],
        sql.atributos["vcpus"],
        sql.atributos["alta_disponibilidade"],
    ) == ("sqlserver", 4, True)
    redis = por["microsoft.cache/redis"]
    assert (redis.atributos["classe"], redis.atributos["publico"]) == ("C1", False)
    assert por["microsoft.containerservice/managedclusters"].atributos["versao"] == "1.29"
    assert por["microsoft.storage/storageaccounts"].atributos["publico"] is False
    assert por["microsoft.network/publicipaddresses"].estado is EstadoDoRecurso.DISPONIVEL
    assert por["microsoft.compute/virtualmachines"].estado is EstadoDoRecurso.PARADO
    assert inv.contagens["configuracao_ilegivel"] == 1
    assert inv.contagens["item_sem_identidade"] == 1


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [("-1", "todos"), ("6", "tcp"), ("UDP", "udp"), ("58", "icmp"), (None, "todos"), ("50", "50")],
)
def test_protocolo_canonico(entrada, saida) -> None:
    assert comum.protocolo_canonico(entrada) == saida


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [
        ("22", (22, 22)),
        ("9000-8000", (8000, 9000)),
        ("*", (None, None)),
        ("x", (None, None)),
        (None, (None, None)),
    ],
)
def test_faixa_de_portas(entrada, saida) -> None:
    assert comum.faixa_de_portas(entrada) == saida


def test_ajudantes() -> None:
    assert comum.portas_da_aws("tcp", "x", 1) == (None, None)
    assert comum.portas_da_aws("tcp", -1, -1) == (None, None)
    assert comum.seletor_de_endereco("10.0.0.0/0").tipo.value == "qualquer"
    assert comum.seletor_de_endereco("Storage.EastUS").tipo.value == "servico"
    assert comum.ip_valido("10.0.0.1") == "10.0.0.1"
    assert comum.ip_valido("nada") is None
    assert comum.ip_valido(None) is None
    assert comum.instante("ontem") is None
    assert comum.instante("2026-01-01T00:00:00").tzinfo is not None
    assert comum.inteiro("3.0") == 3
    assert comum.inteiro("abc") is None
    assert comum.booleano("Enabled") is True
    assert comum.booleano("talvez") is None
    assert comum.lista("x") == ["x"]
    assert comum.tags_de_pares([{"Key": "a", "Value": None}, "lixo"]) == {"a": ""}
    assert comum.tags_de_objeto(["não é objeto"]) == {}
    assert comum.texto("  ") is None
