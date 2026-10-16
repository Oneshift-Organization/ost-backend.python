"""Normalizador do Azure Resource Graph contra o formato documentado (I2).

Linhas no formato do `az graph query ... | project id, name, type, location,
resourceGroup, subscriptionId, tags, properties, sku, kind, zones, managedBy`,
com a `properties` do ARM de cada tipo.
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
from app.infrastructure.inventory_ingestion.normalizadores.azure_graph import arm_pai
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos

_S = "00000000-0000-0000-0000-000000000001"
_G = f"/subscriptions/{_S}/resourceGroups/rg-loja/providers"
_VNET = f"{_G}/Microsoft.Network/virtualNetworks/vnet-loja"
_SUB_APP = f"{_VNET}/subnets/app"
_SUB_DB = f"{_VNET}/subnets/dados"
_NSG = f"{_G}/Microsoft.Network/networkSecurityGroups/nsg-app"
_ASG_WEB = f"{_G}/Microsoft.Network/applicationSecurityGroups/asg-web"
_ASG_API = f"{_G}/Microsoft.Network/applicationSecurityGroups/asg-api"
_NIC = f"{_G}/Microsoft.Network/networkInterfaces/vm-web-nic"
_VM = f"{_G}/Microsoft.Compute/virtualMachines/vm-web"
_OS = f"{_G}/Microsoft.Compute/disks/vm-web_OsDisk_1"
_DADOS = f"{_G}/Microsoft.Compute/disks/vm-web-dados"
_SOLTO = f"{_G}/Microsoft.Compute/disks/esquecido"
_PIP = f"{_G}/Microsoft.Network/publicIPAddresses/pip-lb"
_LB = f"{_G}/Microsoft.Network/loadBalancers/lb-web"
_PG = f"{_G}/Microsoft.DBforPostgreSQL/flexibleServers/pg-pedidos"


def _linha(ident: str, tipo: str, props: dict, **extra) -> dict:
    return {
        "id": ident,
        "name": ident.rsplit("/", 1)[-1],
        "type": tipo,
        "location": "eastus",
        "resourceGroup": "rg-loja",
        "subscriptionId": _S,
        "tags": extra.pop("tags", {}),
        "properties": props,
        "sku": extra.pop("sku", None),
        "kind": "",
        "zones": extra.pop("zones", None),
        "managedBy": extra.pop("managedBy", ""),
    }


def _linhas() -> list[dict]:
    return [
        _linha(
            _VNET,
            "microsoft.network/virtualnetworks",
            {
                "addressSpace": {"addressPrefixes": ["10.20.0.0/16"]},
                "subnets": [
                    {
                        "id": _SUB_APP,
                        "name": "app",
                        "properties": {
                            "addressPrefix": "10.20.1.0/24",
                            "networkSecurityGroup": {"id": _NSG},
                        },
                    },
                    {
                        "id": _SUB_DB,
                        "name": "dados",
                        "properties": {"addressPrefixes": ["10.20.2.0/24"]},
                    },
                ],
            },
        ),
        _linha(
            _NSG,
            "microsoft.network/networksecuritygroups",
            {
                "securityRules": [
                    {
                        "name": "https-da-internet",
                        "properties": {
                            "priority": 100,
                            "direction": "Inbound",
                            "access": "Allow",
                            "protocol": "Tcp",
                            "sourceAddressPrefix": "Internet",
                            "sourcePortRange": "*",
                            "destinationPortRange": "443",
                            "destinationAddressPrefix": "*",
                            "destinationApplicationSecurityGroups": [{"id": _ASG_WEB}],
                        },
                    },
                    {
                        "name": "web-para-api",
                        "properties": {
                            "priority": 110,
                            "direction": "Inbound",
                            "access": "Allow",
                            "protocol": "Tcp",
                            "sourceApplicationSecurityGroups": [{"id": _ASG_WEB}],
                            "destinationApplicationSecurityGroups": [{"id": _ASG_API}],
                            "destinationPortRanges": ["8080", "9000-9100"],
                        },
                    },
                    {
                        "name": "vnet-tudo",
                        "properties": {
                            "priority": 200,
                            "direction": "Inbound",
                            "access": "Allow",
                            "protocol": "*",
                            "sourceAddressPrefix": "VirtualNetwork",
                            "destinationPortRange": "*",
                        },
                    },
                    {
                        "name": "bloqueia",
                        "properties": {
                            "priority": 4000,
                            "direction": "Inbound",
                            "access": "Deny",
                            "protocol": "*",
                            "sourceAddressPrefixes": ["203.0.113.0/24", "198.51.100.7"],
                            "destinationPortRange": "*",
                        },
                    },
                ],
                "defaultSecurityRules": [{"name": "AllowVnetInBound"}],
            },
        ),
        _linha(_ASG_WEB, "microsoft.network/applicationsecuritygroups", {}),
        _linha(_ASG_API, "microsoft.network/applicationsecuritygroups", {}),
        _linha(
            _NIC,
            "microsoft.network/networkinterfaces",
            {
                "ipConfigurations": [
                    {
                        "name": "ipconfig1",
                        "properties": {
                            "primary": True,
                            "privateIPAddress": "10.20.1.4",
                            "subnet": {"id": _SUB_APP},
                            "applicationSecurityGroups": [{"id": _ASG_WEB}],
                            "loadBalancerBackendAddressPools": [
                                {"id": f"{_LB}/backendAddressPools/pool-web"}
                            ],
                        },
                    }
                ],
                "virtualMachine": {"id": _VM},
            },
        ),
        _linha(
            _VM,
            "microsoft.compute/virtualmachines",
            {
                "vmId": "abcd-1234",
                "hardwareProfile": {"vmSize": "Standard_D4s_v5"},
                "storageProfile": {
                    "osDisk": {"osType": "Linux", "managedDisk": {"id": _OS}},
                    "dataDisks": [{"lun": 0, "managedDisk": {"id": _DADOS}}],
                    "imageReference": {"publisher": "Canonical", "offer": "ubuntu", "sku": "22_04"},
                },
                "networkProfile": {"networkInterfaces": [{"id": _NIC}]},
                "extended": {"instanceView": {"powerState": {"code": "PowerState/running"}}},
            },
            tags={"app": "checkout"},
            zones=["1"],
        ),
        _linha(
            _OS,
            "microsoft.compute/disks",
            {"diskSizeGB": 64, "diskState": "Attached", "osType": "Linux"},
            sku={"name": "Premium_LRS", "tier": "Premium"},
            managedBy=_VM,
        ),
        _linha(
            _DADOS,
            "microsoft.compute/disks",
            {"diskSizeGB": 1024, "diskState": "Attached"},
            sku={"name": "Standard_LRS"},
            managedBy=_VM,
        ),
        _linha(
            _SOLTO,
            "microsoft.compute/disks",
            {"diskSizeGB": 256, "diskState": "Unattached"},
            sku={"name": "StandardSSD_LRS"},
        ),
        _linha(
            _PIP,
            "microsoft.network/publicipaddresses",
            {
                "ipAddress": "20.1.2.3",
                "ipConfiguration": {"id": f"{_LB}/frontendIPConfigurations/frontend"},
            },
        ),
        _linha(
            _LB,
            "microsoft.network/loadbalancers",
            {
                "frontendIPConfigurations": [
                    {"name": "frontend", "properties": {"publicIPAddress": {"id": _PIP}}}
                ],
                "backendAddressPools": [
                    {
                        "name": "pool-web",
                        "properties": {
                            "backendIPConfigurations": [
                                {"id": f"{_NIC}/ipConfigurations/ipconfig1"}
                            ]
                        },
                    }
                ],
            },
        ),
        _linha(
            _PG,
            "microsoft.dbforpostgresql/flexibleservers",
            {
                "version": "15",
                "state": "Ready",
                "storage": {"storageSizeGB": 512},
                "highAvailability": {"mode": "ZoneRedundant"},
                "network": {
                    "delegatedSubnetResourceId": _SUB_DB,
                    "publicNetworkAccess": "Disabled",
                },
            },
            sku={"name": "Standard_D4ds_v5", "tier": "GeneralPurpose"},
        ),
    ]


def _inv(tmp_path: Path, linhas: list[dict] | None = None):
    arq = tmp_path / "pagina1.json"
    arq.write_text(
        json.dumps(
            {"count": 1, "data": linhas or _linhas(), "skip_token": None, "total_records": 1}
        )
    )
    resultado = normalizar_arquivos([(arq.name, arq)])
    assert set(resultado.formatos.values()) == {"azure_resource_graph"}
    return resultado.inventario


def _r(inv, ident: str):
    return next(r for r in inv.recursos if r.chave == ident.lower())


def test_ids_em_minusculas_e_subredes_embutidas(tmp_path) -> None:
    inv = _inv(tmp_path)
    assert all(r.chave == r.chave.lower() for r in inv.recursos)
    app = _r(inv, _SUB_APP)
    assert app.tipo is TipoDeRecurso.SUBREDE
    assert app.atributos == {"cidr": "10.20.1.0/24"}
    assert _r(inv, _SUB_DB).atributos == {"cidr": "10.20.2.0/24"}
    assert _r(inv, _VNET).atributos["cidrs"] == ["10.20.0.0/16"]


def test_vm_discos_e_nic(tmp_path) -> None:
    inv = _inv(tmp_path)
    vm = _r(inv, _VM)
    assert vm.atributos["tipo_maquina"] == "Standard_D4s_v5"
    assert vm.estado is EstadoDoRecurso.ATIVO
    assert vm.zona == "1"
    assert "abcd-1234" in vm.apelidos
    assert _r(inv, _OS).atributos["boot"] is True
    assert _r(inv, _DADOS).atributos["boot"] is False
    assert _r(inv, _DADOS).atributos["categoria_disco"] == "hdd"
    assert _r(inv, _SOLTO).estado is EstadoDoRecurso.DISPONIVEL
    rel = {(r.de, r.para, r.tipo) for r in inv.relacoes}
    nic, vmk = _NIC.lower(), _VM.lower()
    assert (nic, vmk, TipoDeRelacao.ANEXADO_A) in rel
    assert (nic, _SUB_APP.lower(), TipoDeRelacao.CONTIDO_EM) in rel
    assert (nic, _ASG_WEB.lower(), TipoDeRelacao.PROTEGIDO_POR) in rel
    assert (_SUB_APP.lower(), _NSG.lower(), TipoDeRelacao.PROTEGIDO_POR) in rel
    assert _r(inv, _NIC).atributos["ip_privado"] == "10.20.1.4"


def test_balanceador_encaminha_para_a_nic_e_e_publico(tmp_path) -> None:
    inv = _inv(tmp_path)
    lb = _r(inv, _LB)
    assert lb.atributos["publico"] is True
    rel = {(r.de, r.para, r.tipo) for r in inv.relacoes}
    assert (_LB.lower(), _NIC.lower(), TipoDeRelacao.ENCAMINHA_PARA) in rel
    # O IP público está na configuração de frontend: reduzida ao balanceador.
    assert (_PIP.lower(), _LB.lower(), TipoDeRelacao.ANEXADO_A) in rel


def test_regras_do_nsg(tmp_path) -> None:
    inv = _inv(tmp_path)
    nsg, web, api = _NSG.lower(), _ASG_WEB.lower(), _ASG_API.lower()
    regras = [r for r in inv.regras if r.grupo == nsg]
    https = next(r for r in regras if r.descricao == "https-da-internet")
    assert https.origem == Seletor(TipoDeSeletor.QUALQUER)
    assert https.destino == Seletor(TipoDeSeletor.GRUPO, web)
    assert (https.porta_inicio, https.porta_fim) == (443, 443)
    web_api = [r for r in regras if r.descricao == "web-para-api"]
    assert {(r.porta_inicio, r.porta_fim) for r in web_api} == {(8080, 8080), (9000, 9100)}
    assert all(r.origem == Seletor(TipoDeSeletor.GRUPO, web) for r in web_api)
    assert all(r.destino == Seletor(TipoDeSeletor.GRUPO, api) for r in web_api)
    vnet = next(r for r in regras if r.descricao == "vnet-tudo")
    assert vnet.origem == Seletor(TipoDeSeletor.SERVICO, "VirtualNetwork")
    assert vnet.protocolo == "todos"
    bloqueio = [r for r in regras if r.descricao == "bloqueia"]
    assert {r.origem.valor for r in bloqueio} == {"203.0.113.0/24", "198.51.100.7/32"}
    assert all(r.acao is Acao.NEGAR and r.direcao is Direcao.ENTRADA for r in bloqueio)
    # As regras padrão não viram regra: existem em toda VNet.
    assert _r(inv, _NSG).atributos["regras_padrao_omitidas"] is True


def test_flexible_server(tmp_path) -> None:
    inv = _inv(tmp_path)
    pg = _r(inv, _PG)
    assert pg.tipo is TipoDeRecurso.BANCO
    assert pg.atributos == {
        "classe": "Standard_D4ds_v5",
        "tier": "GeneralPurpose",
        "motor": "postgres",
        "versao": "15",
        "alta_disponibilidade": True,
        "publico": False,
        "armazenamento_gb": 512,
    }
    assert any(
        r.de == _PG.lower() and r.para == _SUB_DB.lower() and r.tipo is TipoDeRelacao.CONTIDO_EM
        for r in inv.relacoes
    )


def test_lista_pura_e_properties_como_texto(tmp_path) -> None:
    linhas = _linhas()
    for linha in linhas:
        linha["properties"] = json.dumps(linha["properties"])
    arq = tmp_path / "lista.json"
    arq.write_text(json.dumps(linhas))
    inv = normalizar_arquivos([(arq.name, arq)]).inventario
    assert _r(inv, _VM).atributos["tipo_maquina"] == "Standard_D4s_v5"


def test_duas_paginas_resolvem_referencias_entre_si(tmp_path) -> None:
    linhas = _linhas()
    a = tmp_path / "p1.json"
    b = tmp_path / "p2.json"
    a.write_text(json.dumps({"data": linhas[:5]}))
    b.write_text(json.dumps({"data": linhas[5:]}))
    inv = normalizar_arquivos([(a.name, a), (b.name, b)]).inventario
    assert any(
        r.de == _NIC.lower() and r.para == _VM.lower() and r.tipo is TipoDeRelacao.ANEXADO_A
        for r in inv.relacoes
    )


def test_reducao_de_sub_recurso() -> None:
    assert arm_pai(f"{_NIC}/ipConfigurations/ipconfig1") == _NIC.lower()
    assert arm_pai(f"{_LB}/backendAddressPools/pool") == _LB.lower()
    assert arm_pai(_SUB_APP) == _SUB_APP.lower()
    assert arm_pai(None) is None
    assert arm_pai("/sem/provedor") == "/sem/provedor"


# ── AKS e componentes (formato documentado do Resource Graph; sem conta real ainda) ──

_RG_NOS = "MC_rg-loja_aks-loja_eastus"


def _aks_e_grupo_de_nos() -> list[dict]:
    base = f"/subscriptions/{_S}/resourceGroups"
    aks = _linha(
        f"{base}/rg-loja/providers/Microsoft.ContainerService/managedClusters/aks-loja",
        "microsoft.containerservice/managedclusters",
        {"kubernetesVersion": "1.29", "nodeResourceGroup": _RG_NOS},
    )
    vmss = _linha(
        f"{base}/{_RG_NOS}/providers/Microsoft.Compute/virtualMachineScaleSets/aks-pool1",
        "microsoft.compute/virtualmachinescalesets",
        {},
        sku={"name": "Standard_D4s_v5", "capacity": 3},
    )
    lb = _linha(
        f"{base}/{_RG_NOS}/providers/Microsoft.Network/loadBalancers/kubernetes",
        "microsoft.network/loadbalancers",
        {},
    )
    disco = _linha(
        f"{base}/{_RG_NOS}/providers/Microsoft.Compute/disks/pvc-1",
        "microsoft.compute/disks",
        {"diskSizeGB": 32},
        tags={"kubernetes.io-created-for-pvc-namespace": "pagamentos"},
    )
    ip = _linha(
        f"{base}/{_RG_NOS}/providers/Microsoft.Network/publicIPAddresses/kubernetes-a1",
        "microsoft.network/publicipaddresses",
        {"ipAddress": "20.1.1.1"},
        tags={"k8s-azure-service": "pagamentos/api"},
    )
    acr = _linha(
        f"{base}/rg-loja/providers/Microsoft.ContainerRegistry/registries/acrloja",
        "microsoft.containerregistry/registries",
        {},
    )
    funcao = {
        **_linha(
            f"{base}/rg-loja/providers/Microsoft.Web/sites/fn-loja", "microsoft.web/sites", {}
        ),
        "kind": "functionapp,linux",
    }
    for item in (aks, vmss, lb, disco, ip):
        if _RG_NOS in item["id"]:
            item["resourceGroup"] = _RG_NOS
    return [aks, vmss, lb, disco, ip, acr, funcao]


def test_grupo_de_nos_do_aks_faz_parte_do_cluster(tmp_path) -> None:
    inv = _inv(tmp_path, _aks_e_grupo_de_nos())
    cluster = next(r for r in inv.recursos if r.tipo_nativo.endswith("managedclusters"))
    membros = {
        r.de for r in inv.relacoes if r.para == cluster.chave and r.tipo.value == "membro_de"
    }
    nomes = {next(x.nome for x in inv.recursos if x.chave == m) for m in membros}
    assert nomes == {"aks-pool1", "kubernetes", "pvc-1"}
    vmss = next(r for r in inv.recursos if r.nome == "aks-pool1")
    assert (vmss.tipo.value, vmss.atributos["tipo_maquina"], vmss.atributos["instancias"]) == (
        "instancia",
        "Standard_D4s_v5",
        3,
    )


def test_marcas_do_kubernetes_e_componentes_da_azure(tmp_path) -> None:
    inv = _inv(tmp_path, _aks_e_grupo_de_nos())
    por_nome = {r.nome: r for r in inv.recursos}
    assert por_nome["kubernetes-a1"].atributos["servico_kubernetes"] == "pagamentos/api"
    assert por_nome["pvc-1"].atributos["namespace_kubernetes"] == "pagamentos"
    assert por_nome["acrloja"].atributos["componente"] == "registro_de_imagens"
    assert por_nome["fn-loja"].atributos["componente"] == "funcao"
