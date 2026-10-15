"""Normalizador do AWS Config contra o formato documentado (I2).

A base é o exemplo oficial de snapshot
(https://docs.aws.amazon.com/config/latest/developerguide/example-s3-snapshot.html),
com a grafia dele (`availibilityZone`, tags como lista `tagName/tagValue` dentro
da configuração), mais os tipos que a topologia usa.
"""

from __future__ import annotations

import copy
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
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos

_CONTA = "123456789012"
_REGIAO = "us-west-2"

# ── exemplo oficial (volume + instância), resumido nos campos usados ────
_VOLUME = {
    "configurationItemVersion": "1.0",
    "resourceId": "vol-ce676ccc",
    "arn": "arn:aws:us-west-2b:123456789012:volume/vol-ce676ccc",
    "accountId": _CONTA,
    "configurationItemCaptureTime": "2014-03-07T23:47:08.918Z",
    "configurationItemStatus": "OK",
    "availibilityZone": "us-west-2b",
    "awsRegion": _REGIAO,
    "resourceType": "AWS::EC2::Volume",
    "tags": {},
    "relationships": [
        {
            "resourceId": "i-344c463d",
            "resourceType": "AWS::EC2::Instance",
            "name": "Attached to Instance",
        }
    ],
    "configuration": {
        "volumeId": "vol-ce676ccc",
        "size": 1,
        "state": "in-use",
        "attachments": [
            {
                "volumeId": "vol-ce676ccc",
                "instanceId": "i-344c463d",
                "device": "/dev/sdf",
                "state": "attached",
            }
        ],
        "tags": [
            {"tagName": "environment", "tagValue": "PROD"},
            {"tagName": "name", "tagValue": "DataVolume1"},
        ],
        "volumeType": "standard",
    },
}
_INSTANCIA = {
    "configurationItemVersion": "1.0",
    "resourceId": "i-344c463d",
    "accountId": _CONTA,
    "arn": "arn:aws:ec2:us-west-2b:123456789012:instance/i-344c463d",
    "configurationItemCaptureTime": "2014-03-07T23:47:09.523Z",
    "configurationItemStatus": "OK",
    "availibilityZone": "us-west-2b",
    "awsRegion": _REGIAO,
    "resourceType": "AWS::EC2::Instance",
    "tags": {"Name": "integ-test-1", "examplename": "examplevalue"},
    "relationships": [
        {
            "resourceId": "vol-ce676ccc",
            "resourceType": "AWS::EC2::Volume",
            "name": "Attached Volume",
        },
        {
            "resourceId": "vol-ef0e06ed",
            "resourceType": "AWS::EC2::Volume",
            "name": "Attached Volume",
            "direction": "OUT",
        },
        {
            "resourceId": "subnet-47b4cf2c",
            "resourceType": "AWS::EC2::SUBNET",
            "name": "Is contained in Subnet",
            "direction": "IN",
        },
    ],
    "configuration": {
        "instanceId": "i-344c463d",
        "imageId": "ami-ccf297fc",
        "state": {"code": 16, "name": "running"},
        "instanceType": "t1.micro",
        "subnetId": "subnet-47b4cf2c",
        "vpcId": "vpc-41b4cf2a",
        "privateIpAddress": "172.31.21.63",
        "publicIpAddress": "54.218.4.189",
        "architecture": "x86_64",
        "rootDeviceType": "ebs",
        "rootDeviceName": "/dev/sda1",
        "blockDeviceMappings": [
            {"deviceName": "/dev/sda1", "ebs": {"volumeId": "vol-ef0e06ed"}},
            {"deviceName": "/dev/sdf", "ebs": {"volumeId": "vol-ce676ccc"}},
        ],
        "securityGroups": [{"groupName": "launch-wizard-2", "groupId": "sg-892adfec"}],
        "networkInterfaces": [{"networkInterfaceId": "eni-55c03d22"}],
    },
}


def _ci(tipo: str, rid: str, config: dict, **extra) -> dict:
    return {
        "resourceId": rid,
        "resourceType": tipo,
        "accountId": _CONTA,
        "awsRegion": _REGIAO,
        "configurationItemStatus": "OK",
        "configurationItemCaptureTime": "2026-03-31T10:00:00Z",
        "configuration": config,
        **extra,
    }


_RAIZ = _ci("AWS::EC2::Volume", "vol-ef0e06ed", {"size": 8, "volumeType": "gp3", "state": "in-use"})
_SUBREDE = _ci(
    "AWS::EC2::Subnet",
    "subnet-47b4cf2c",
    {"cidrBlock": "172.31.16.0/20", "vpcId": "vpc-41b4cf2a", "mapPublicIpOnLaunch": True},
)
_VPC = _ci("AWS::EC2::VPC", "vpc-41b4cf2a", {"cidrBlock": "172.31.0.0/16"})
_SG_WEB = _ci(
    "AWS::EC2::SecurityGroup",
    "sg-892adfec",
    {
        "groupId": "sg-892adfec",
        "groupName": "launch-wizard-2",
        "vpcId": "vpc-41b4cf2a",
        "ipPermissions": [
            {
                "ipProtocol": "tcp",
                "fromPort": 22,
                "toPort": 22,
                "ipv4Ranges": [{"cidrIp": "0.0.0.0/0"}],
                "ipRanges": ["0.0.0.0/0"],
            },
            {
                "ipProtocol": "tcp",
                "fromPort": 443,
                "toPort": 443,
                "userIdGroupPairs": [{"groupId": "sg-alb", "userId": _CONTA}],
            },
            {"ipProtocol": "-1", "prefixListIds": [{"prefixListId": "pl-1234"}]},
            {
                "ipProtocol": "icmp",
                "fromPort": 8,
                "toPort": -1,
                "ipv4Ranges": [{"cidrIp": "10.0.0.0/8"}],
            },
        ],
        "ipPermissionsEgress": [{"ipProtocol": "-1", "ipv4Ranges": [{"cidrIp": "0.0.0.0/0"}]}],
    },
    arn="arn:aws:ec2:us-west-2:123456789012:security-group/sg-892adfec",
)
_SG_ALB = _ci(
    "AWS::EC2::SecurityGroup",
    "sg-alb",
    {
        "groupId": "sg-alb",
        "groupName": "alb",
        "vpcId": "vpc-41b4cf2a",
        "ipPermissionsEgress": [
            {
                "ipProtocol": "tcp",
                "fromPort": 443,
                "toPort": 443,
                "ipv4Ranges": [{"cidrIp": "10.0.0.0/8"}],
            }
        ],
    },
    arn="arn:aws:ec2:us-west-2:123456789012:security-group/sg-alb",
)


def _escrever(tmp_path: Path, nome: str, itens: list[dict]) -> Path:
    arq = tmp_path / nome
    arq.write_text(json.dumps({"fileVersion": "1.0", "configurationItems": itens}))
    return arq


def _normalizar(*arquivos: Path):
    return normalizar_arquivos([(a.name, a) for a in arquivos])


@pytest.fixture
def inventario(tmp_path):
    arq = _escrever(
        tmp_path,
        "snap.json",
        [_VOLUME, _INSTANCIA, _RAIZ, _SUBREDE, _VPC, _SG_WEB, _SG_ALB],
    )
    return _normalizar(arq).inventario


def _por_id(inv, rid: str):
    return next(r for r in inv.recursos if rid in r.apelidos)


def test_formato_e_provedor(tmp_path) -> None:
    arq = _escrever(tmp_path, "snap.json", [_VOLUME])
    resultado = _normalizar(arq)
    assert resultado.formatos == {"snap.json": "aws_config"}
    assert resultado.inventario.provedor == "aws"


def test_exemplo_oficial_volume_e_instancia(inventario) -> None:
    vm = _por_id(inventario, "i-344c463d")
    assert vm.tipo is TipoDeRecurso.INSTANCIA
    assert vm.chave == "arn:aws:ec2:us-west-2b:123456789012:instance/i-344c463d"
    assert vm.nome == "integ-test-1"  # resourceName ausente: tag Name
    assert vm.zona == "us-west-2b"  # a grafia antiga `availibilityZone`
    assert vm.estado is EstadoDoRecurso.ATIVO
    assert vm.atributos["tipo_maquina"] == "t1.micro"
    assert vm.atributos["ip_publico"] == "54.218.4.189"
    volume = _por_id(inventario, "vol-ce676ccc")
    assert volume.tipo is TipoDeRecurso.DISCO
    assert volume.tags == {"environment": "PROD", "name": "DataVolume1"}
    assert volume.atributos["tamanho_gb"] == 1
    assert volume.atributos["categoria_disco"] == "hdd"  # standard = magnético


def test_relacoes_saem_da_configuracao_e_do_texto(inventario) -> None:
    vm = _por_id(inventario, "i-344c463d").chave
    rel = {(r.de, r.para, r.tipo) for r in inventario.relacoes}
    dados = _por_id(inventario, "vol-ce676ccc").chave
    raiz = _por_id(inventario, "vol-ef0e06ed").chave
    subrede = _por_id(inventario, "subnet-47b4cf2c").chave
    vpc = _por_id(inventario, "vpc-41b4cf2a").chave
    grupo = _por_id(inventario, "sg-892adfec").chave
    # O mesmo anexo dito dos dois lados vira uma relação só.
    assert (dados, vm, TipoDeRelacao.ANEXADO_A) in rel
    assert (raiz, vm, TipoDeRelacao.ANEXADO_A) in rel
    assert (vm, subrede, TipoDeRelacao.CONTIDO_EM) in rel
    assert (subrede, vpc, TipoDeRelacao.CONTIDO_EM) in rel
    assert (vm, grupo, TipoDeRelacao.PROTEGIDO_POR) in rel
    assert len([r for r in rel if r[0] == dados and r[1] == vm]) == 1


def test_disco_de_boot_marcado_pela_instancia(inventario) -> None:
    assert _por_id(inventario, "vol-ef0e06ed").atributos["boot"] is True
    assert _por_id(inventario, "vol-ce676ccc").atributos["boot"] is False


def test_referencia_pendente_e_contada_nao_inventada(inventario) -> None:
    # A instância cita a ENI eni-55c03d22, que não veio no snapshot.
    assert "eni-55c03d22" not in {a for r in inventario.recursos for a in r.apelidos}
    assert inventario.contagens["referencia_externa:AWS::EC2::NetworkInterface"] == 1


def test_regras_do_grupo(inventario) -> None:
    grupo = _por_id(inventario, "sg-892adfec").chave
    alb = _por_id(inventario, "sg-alb").chave
    entrada = [r for r in inventario.regras if r.grupo == grupo and r.direcao is Direcao.ENTRADA]
    # SSH para a internet: `ipRanges` e `ipv4Ranges` repetem a mesma faixa.
    ssh = [r for r in entrada if r.porta_inicio == 22]
    assert len(ssh) == 1
    assert ssh[0].origem == Seletor(TipoDeSeletor.QUALQUER)
    assert ssh[0].protocolo == "tcp"
    https = next(r for r in entrada if r.porta_inicio == 443)
    assert https.origem == Seletor(TipoDeSeletor.GRUPO, alb)
    todos = next(r for r in entrada if r.protocolo == "todos")
    assert todos.origem == Seletor(TipoDeSeletor.SERVICO, "prefix-list:pl-1234")
    assert todos.porta_inicio is None
    icmp = next(r for r in entrada if r.protocolo == "icmp")
    assert (icmp.porta_inicio, icmp.porta_fim) == (None, None)
    assert icmp.origem == Seletor(TipoDeSeletor.CIDR, "10.0.0.0/8")


def test_restricao_de_saida(inventario) -> None:
    assert _por_id(inventario, "sg-892adfec").atributos["restringe_saida"] is False
    assert _por_id(inventario, "sg-alb").atributos["restringe_saida"] is True


def test_subrede_e_vpc(inventario) -> None:
    subrede = _por_id(inventario, "subnet-47b4cf2c")
    assert subrede.tipo is TipoDeRecurso.SUBREDE
    assert subrede.atributos == {"cidr": "172.31.16.0/20", "publica": True}
    # Sem ARN: chave montada com conta, região, tipo e id.
    assert subrede.chave == f"aws:{_CONTA}:{_REGIAO}:AWS::EC2::Subnet:subnet-47b4cf2c"


def test_grupo_de_outra_conta_vira_servico_externo(tmp_path) -> None:
    sg = copy.deepcopy(_SG_WEB)
    sg["configuration"]["ipPermissions"] = [
        {
            "ipProtocol": "tcp",
            "fromPort": 5432,
            "toPort": 5432,
            "userIdGroupPairs": [{"groupId": "sg-outra", "userId": "999999999999"}],
        }
    ]
    inv = _normalizar(_escrever(tmp_path, "s.json", [sg])).inventario
    [regra] = [r for r in inv.regras if r.direcao is Direcao.ENTRADA]
    assert regra.origem == Seletor(TipoDeSeletor.SERVICO, "grupo_externo:sg-outra")
    assert inv.contagens["grupo_externo"] == 1


def test_itens_removidos_e_instancia_encerrada_nao_entram(tmp_path) -> None:
    removido = {**_VOLUME, "configurationItemStatus": "ResourceDeleted"}
    encerrada = copy.deepcopy(_INSTANCIA)
    encerrada["configuration"]["state"]["name"] = "terminated"
    inv = _normalizar(_escrever(tmp_path, "s.json", [removido, encerrada])).inventario
    assert inv.recursos == ()
    assert inv.contagens["removido"] == 2


def test_configuracao_como_texto_e_relationship_name(tmp_path) -> None:
    """Forma da API `get-resource-config-history`."""
    vol = copy.deepcopy(_VOLUME)
    vol["configuration"] = json.dumps(vol["configuration"])
    vol["relationships"] = [
        {
            "resourceId": "i-344c463d",
            "resourceType": "AWS::EC2::Instance",
            "relationshipName": "Is attached to Instance",
        }
    ]
    inv = _normalizar(_escrever(tmp_path, "s.json", [vol, _INSTANCIA])).inventario
    disco = _por_id(inv, "vol-ce676ccc")
    assert disco.atributos["tamanho_gb"] == 1
    vm = _por_id(inv, "i-344c463d").chave
    assert any(
        r.de == disco.chave and r.para == vm and r.tipo is TipoDeRelacao.ANEXADO_A
        for r in inv.relacoes
    )


def test_saida_do_select_resource_config(tmp_path) -> None:
    arq = tmp_path / "select.json"
    arq.write_text(
        json.dumps(
            {
                "Results": [json.dumps(_VOLUME), json.dumps(_INSTANCIA)],
                "QueryInfo": {"SelectFields": []},
            }
        )
    )
    inv = _normalizar(arq).inventario
    assert {r.tipo for r in inv.recursos} == {TipoDeRecurso.DISCO, TipoDeRecurso.INSTANCIA}


def test_referencia_resolvida_entre_arquivos(tmp_path) -> None:
    a = _escrever(tmp_path, "a.json", [_INSTANCIA])
    b = _escrever(tmp_path, "b.json", [_SUBREDE, _VOLUME])
    inv = _normalizar(a, b).inventario
    vm = _por_id(inv, "i-344c463d").chave
    subrede = _por_id(inv, "subnet-47b4cf2c").chave
    assert any(r.de == vm and r.para == subrede for r in inv.relacoes)


def test_mesmo_recurso_em_dois_arquivos_fica_com_a_captura_mais_recente(tmp_path) -> None:
    antigo = copy.deepcopy(_RAIZ)
    antigo["configurationItemCaptureTime"] = "2026-01-01T00:00:00Z"
    antigo["configuration"]["size"] = 4
    a = _escrever(tmp_path, "a.json", [_RAIZ])
    b = _escrever(tmp_path, "b.json", [antigo])
    inv = _normalizar(a, b).inventario
    assert _por_id(inv, "vol-ef0e06ed").atributos["tamanho_gb"] == 8
    assert inv.contagens["duplicado"] == 1


def test_tipo_nao_modelado_e_contado(tmp_path) -> None:
    papel = _ci("AWS::IAM::Role", "AROAXYZ", {"roleName": "x"})
    inv = _normalizar(_escrever(tmp_path, "s.json", [papel])).inventario
    assert inv.recursos[0].tipo is TipoDeRecurso.OUTRO
    assert inv.contagens["tipo_nao_modelado:AWS::IAM::Role"] == 1


def test_rds_com_subnet_group_cluster_e_grupo(tmp_path) -> None:
    rds = _ci(
        "AWS::RDS::DBInstance",
        "db-ABC123",
        {
            "dBInstanceIdentifier": "pagamentos-1",
            "dBInstanceClass": "db.r6g.xlarge",
            "engine": "aurora-postgresql",
            "engineVersion": "15.4",
            "multiAZ": False,
            "publiclyAccessible": False,
            "dBInstanceStatus": "available",
            "endpoint": {"port": 5432},
            "dBClusterIdentifier": "pagamentos",
            "vpcSecurityGroups": [{"vpcSecurityGroupId": "sg-892adfec", "status": "active"}],
            "dBSubnetGroup": {"subnets": [{"subnetIdentifier": "subnet-47b4cf2c"}]},
        },
        arn="arn:aws:rds:us-west-2:123456789012:db:pagamentos-1",
        resourceName="pagamentos-1",
    )
    cluster = _ci(
        "AWS::RDS::DBCluster",
        "cluster-XYZ",
        {"dBClusterIdentifier": "pagamentos", "engine": "aurora-postgresql"},
        arn="arn:aws:rds:us-west-2:123456789012:cluster:pagamentos",
        resourceName="pagamentos",
    )
    inv = _normalizar(_escrever(tmp_path, "s.json", [rds, cluster, _SUBREDE, _SG_WEB])).inventario
    banco = _por_id(inv, "pagamentos-1")
    assert banco.tipo is TipoDeRecurso.BANCO
    assert banco.atributos["versao"] == "15.4"
    assert banco.atributos["porta"] == 5432
    assert banco.atributos["alta_disponibilidade"] is False
    tipos = {(r.para, r.tipo) for r in inv.relacoes if r.de == banco.chave}
    assert (_por_id(inv, "subnet-47b4cf2c").chave, TipoDeRelacao.CONTIDO_EM) in tipos
    assert (_por_id(inv, "sg-892adfec").chave, TipoDeRelacao.PROTEGIDO_POR) in tipos
    assert (_por_id(inv, "cluster-xyz").chave, TipoDeRelacao.MEMBRO_DE) in tipos
    # O cluster é conector, não "tipo não modelado".
    assert not any(k.startswith("tipo_nao_modelado") for k in inv.contagens)


def test_balanceadores_e_eip(tmp_path) -> None:
    alb = _ci(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/web/50dc6c495c0c9188",
        {
            "scheme": "internet-facing",
            "type": "application",
            "securityGroups": ["sg-alb"],
            "availabilityZones": [{"zoneName": "us-west-2b", "subnetId": "subnet-47b4cf2c"}],
        },
        arn="arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/web/50dc6c495c0c9188",
    )
    classico = _ci(
        "AWS::ElasticLoadBalancing::LoadBalancer",
        "legado",
        {
            "scheme": "internal",
            "subnets": ["subnet-47b4cf2c"],
            "instances": [{"instanceId": "i-344c463d"}],
        },
    )
    eip = _ci("AWS::EC2::EIP", "eipalloc-1", {"publicIp": "3.3.3.3", "allocationId": "eipalloc-1"})
    inv = _normalizar(
        _escrever(tmp_path, "s.json", [alb, classico, eip, _INSTANCIA, _SUBREDE, _SG_ALB])
    ).inventario
    web = next(r for r in inv.recursos if r.tipo_nativo.endswith("V2::LoadBalancer"))
    assert web.atributos["publico"] is True
    assert web.atributos["tipo_balanceador"] == "application"
    antigo = _por_id(inv, "legado")
    assert antigo.atributos["publico"] is False
    vm = _por_id(inv, "i-344c463d").chave
    assert any(
        r.de == antigo.chave and r.para == vm and r.tipo is TipoDeRelacao.ENCAMINHA_PARA
        for r in inv.relacoes
    )
    solto = _por_id(inv, "3.3.3.3")
    assert solto.estado is EstadoDoRecurso.DISPONIVEL


def test_no_de_eks_entra_no_cluster(tmp_path) -> None:
    no = copy.deepcopy(_INSTANCIA)
    no["tags"]["eks:cluster-name"] = "producao"
    cluster = _ci(
        "AWS::EKS::Cluster",
        "producao",
        {"name": "producao", "version": "1.29"},
        resourceName="producao",
    )
    inv = _normalizar(_escrever(tmp_path, "s.json", [no, cluster])).inventario
    alvo = _por_id(inv, "producao")
    assert alvo.tipo is TipoDeRecurso.CLUSTER_KUBERNETES
    assert any(r.para == alvo.chave and r.tipo is TipoDeRelacao.MEMBRO_DE for r in inv.relacoes)


def test_ndjson_de_itens(tmp_path) -> None:
    arq = tmp_path / "itens.ndjson"
    arq.write_text("\n".join(json.dumps(i) for i in [_VOLUME, _INSTANCIA]))
    assert len(_normalizar(arq).inventario.recursos) == 2


def test_relacao_com_tipo_em_caixa_diferente_resolve(tmp_path) -> None:
    """O exemplo oficial escreve `AWS::EC2::SUBNET` na relação."""
    vm = copy.deepcopy(_INSTANCIA)
    del vm["configuration"]["subnetId"]  # só a relação declarada diz a subrede
    inv = _normalizar(_escrever(tmp_path, "s.json", [vm, _SUBREDE])).inventario
    chave_vm = _por_id(inv, "i-344c463d").chave
    subrede = _por_id(inv, "subnet-47b4cf2c").chave
    assert any(
        r.de == chave_vm and r.para == subrede and r.tipo is TipoDeRelacao.CONTIDO_EM
        for r in inv.relacoes
    )


# ── forma real do `select-resource-config` (conta de cliente, 10/2026) ──────────


def _par(chave: str, valor: str) -> dict:
    return {"tag": f"{chave}={valor}", "key": chave, "value": valor}


def _select(tmp_path: Path, itens: list[dict]) -> Path:
    arq = tmp_path / "select.json"
    arq.write_text(json.dumps({"Results": [json.dumps(i) for i in itens], "QueryInfo": {}}))
    return arq


def test_select_tags_em_lista_e_taglist_do_rds(tmp_path) -> None:
    rds = _ci(
        "AWS::RDS::DBInstance",
        "db-X",
        {
            "dBInstanceIdentifier": "rds-app-prd",
            "dBInstanceClass": "db.t3.medium",
            "tagList": [{"key": "project", "value": "app"}],
        },
        tags=[_par("env", "prd")],
    )
    bucket = _ci(
        "AWS::S3::Bucket", "app-front", {"name": "app-front"}, tags=[_par("project", "app")]
    )
    inv = _normalizar(_select(tmp_path, [rds, bucket])).inventario
    assert _por_id(inv, "rds-app-prd").tags == {"project": "app", "env": "prd"}
    assert _por_id(inv, "app-front").tags == {"project": "app"}


def test_select_portas_decimais_nao_viram_todas(tmp_path) -> None:
    sg = copy.deepcopy(_SG_WEB)
    sg["configuration"]["ipPermissions"] = [
        {
            "ipProtocol": "tcp",
            "fromPort": 5432.0,
            "toPort": 5432.0,
            "userIdGroupPairs": [{"groupId": "sg-alb", "userId": _CONTA}],
        }
    ]
    inv = _normalizar(_select(tmp_path, [sg, _SG_ALB])).inventario
    [regra] = [
        r for r in inv.regras if r.direcao is Direcao.ENTRADA and r.grupo.endswith("sg-892adfec")
    ]
    assert (regra.porta_inicio, regra.porta_fim) == (5432, 5432)
    assert not regra.todas_as_portas


def test_select_campos_embrulhados_em_value(tmp_path) -> None:
    disco = _ci(
        "AWS::EC2::Volume",
        "vol-1",
        {"size": 20.0, "volumeType": "gp3", "state": {"value": "available"}},
    )
    nlb = _ci(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/net/k8s/1",
        {"scheme": "internet-facing", "type": "network", "securityGroups": [{"value": "sg-alb"}]},
        arn="arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/net/k8s/1",
    )
    inv = _normalizar(_select(tmp_path, [disco, nlb, _SG_ALB])).inventario
    assert _por_id(inv, "vol-1").estado is EstadoDoRecurso.DISPONIVEL
    lb = next(r for r in inv.recursos if r.tipo is TipoDeRecurso.BALANCEADOR)
    sg = _por_id(inv, "sg-alb").chave
    assert any(
        r.de == lb.chave and r.para == sg and r.tipo is TipoDeRelacao.PROTEGIDO_POR
        for r in inv.relacoes
    )


def test_padroes_da_aws_ficam_fora_e_sao_contados(tmp_path) -> None:
    itens = [
        _ci(
            "AWS::CodeDeploy::DeploymentConfig",
            "d1",
            {},
            resourceName="CodeDeployDefault.AllAtOnce",
        ),
        _ci("AWS::KMS::Key", "k1", {"keyManager": "AWS"}),
        _ci("AWS::KMS::Key", "k2", {"keyManager": "CUSTOMER"}),
        _ci("AWS::EC2::EC2Fleet", "fleet-1", {"type": "instant"}),
        _ci("AWS::Events::EventBus", "default", {}, resourceName="default"),
        _ci("AWS::Lambda::Function", "fn", {}, resourceName="lambda-infra-start-prd"),
    ]
    inv = _normalizar(_select(tmp_path, itens)).inventario
    assert sorted(r.tipo_nativo for r in inv.recursos) == ["AWS::KMS::Key", "AWS::Lambda::Function"]
    assert inv.contagens["padrao_do_provedor:AWS::EC2::EC2Fleet"] == 1
    assert inv.contagens["padrao_do_provedor:AWS::KMS::Key"] == 1
    assert "tipo_nao_modelado:AWS::CodeDeploy::DeploymentConfig" not in inv.contagens


def test_balanceador_e_disco_criados_pelo_cluster_sao_membros_dele(tmp_path) -> None:
    cluster = _ci("AWS::EKS::Cluster", "prd", {"name": "prd"}, resourceName="prd")
    nlb = _ci(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/net/kong/1",
        {"type": "network"},
        arn="arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/net/kong/1",
        tags=[_par("elbv2.k8s.aws/cluster", "prd")],
    )
    pv = _ci(
        "AWS::EC2::Volume",
        "vol-pv",
        {"size": 5, "state": "available"},
        tags=[_par("KubernetesCluster", "prd")],
    )
    inv = _normalizar(_select(tmp_path, [cluster, nlb, pv])).inventario
    alvo = _por_id(inv, "prd").chave
    membros = {r.de for r in inv.relacoes if r.para == alvo and r.tipo is TipoDeRelacao.MEMBRO_DE}
    assert membros == {
        _por_id(inv, "vol-pv").chave,
        next(r.chave for r in inv.recursos if r.tipo is TipoDeRecurso.BALANCEADOR),
    }
