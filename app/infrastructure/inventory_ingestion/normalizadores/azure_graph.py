"""Azure Resource Graph → modelo canônico.

Saída do ``az graph query -q "Resources | project id, name, type, location,
resourceGroup, subscriptionId, tags, properties, sku, kind, zones, managedBy"``:
``{"count", "data": [...], "skip_token", "total_records"}``, uma página por
arquivo. A lista pura também é aceita, e ``properties`` como texto JSON (é como
o portal exporta em CSV convertido).

Três particularidades da Azure:

- **Id do ARM em minúsculas** é a chave, igual ao que o parser de billing grava
  no ``resource_id``.
- **Sub-recurso vira o pai.** A NIC cita a subrede
  (``.../virtualNetworks/v/subnets/s``), e o balanceador cita a configuração de
  IP da NIC (``.../networkInterfaces/n/ipConfigurations/c``). A subrede é
  recurso próprio no modelo; a configuração de IP é reduzida à NIC.
- **NSG protege a NIC ou a subrede; ASG é seletor** (RI4). As regras padrão do
  NSG não entram: existem em toda VNet.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, ClassVar

from app.domain.entities.topologia import (
    Acao,
    Direcao,
    EstadoDoRecurso,
    Inventario,
    Recurso,
    RegraDeRede,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.infrastructure.inventory_ingestion.construtor import Construtor, Referencia
from app.infrastructure.inventory_ingestion.leitura import ArquivoLido
from app.infrastructure.inventory_ingestion.normalizadores.comum import (
    apelidos,
    booleano,
    caminho,
    dicionario,
    faixa_de_portas,
    inteiro,
    lista,
    protocolo_canonico,
    seletor_de_endereco,
    sem_vazios,
    tags_de_objeto,
    texto,
)

_T = TipoDeRecurso
_R = TipoDeRelacao

TIPOS: dict[str, TipoDeRecurso] = {
    "microsoft.compute/virtualmachines": _T.INSTANCIA,
    "microsoft.compute/disks": _T.DISCO,
    "microsoft.compute/snapshots": _T.SNAPSHOT,
    "microsoft.network/virtualnetworks": _T.REDE,
    "microsoft.network/virtualnetworks/subnets": _T.SUBREDE,
    "microsoft.network/networksecuritygroups": _T.GRUPO_SEGURANCA,
    "microsoft.network/applicationsecuritygroups": _T.GRUPO_SEGURANCA,
    "microsoft.network/networkinterfaces": _T.INTERFACE_REDE,
    "microsoft.network/publicipaddresses": _T.IP_PUBLICO,
    "microsoft.network/loadbalancers": _T.BALANCEADOR,
    "microsoft.network/applicationgateways": _T.BALANCEADOR,
    "microsoft.network/natgateways": _T.GATEWAY,
    "microsoft.network/virtualnetworkgateways": _T.GATEWAY,
    "microsoft.dbforpostgresql/flexibleservers": _T.BANCO,
    "microsoft.dbformysql/flexibleservers": _T.BANCO,
    "microsoft.sql/servers/databases": _T.BANCO,
    "microsoft.sql/managedinstances": _T.BANCO,
    "microsoft.cache/redis": _T.CACHE,
    "microsoft.storage/storageaccounts": _T.BUCKET,
    "microsoft.containerservice/managedclusters": _T.CLUSTER_KUBERNETES,
}
_SUBREDE = "microsoft.network/virtualnetworks/subnets"
_ASG = "microsoft.network/applicationsecuritygroups"
_HDD = frozenset({"standard_lrs"})
# O portal e a CLI escrevem o estado de energia assim: `PowerState/running`.
_LIGADA = frozenset({"running", "starting"})
_DESLIGADA = frozenset({"deallocated", "deallocating", "stopped", "stopping"})


def arm(ident: object) -> str | None:
    valor = texto(ident)
    return valor.lower().rstrip("/") if valor else None


def arm_pai(ident: object) -> str | None:
    """Reduz um id de sub-recurso ao recurso de primeiro nível.

    ``/subscriptions/s/resourcegroups/g/providers/microsoft.network/
    networkinterfaces/n/ipconfigurations/c`` → ``.../networkinterfaces/n``.
    Subrede fica como está: no modelo ela é recurso próprio.
    """
    valor = arm(ident)
    if not valor:
        return None
    partes = valor.split("/")
    try:
        i = partes.index("providers")
    except ValueError:
        return valor
    if len(partes) > i + 5 and partes[i + 2] == "virtualnetworks" and partes[i + 4] == "subnets":
        return "/".join(partes[: i + 6])
    return "/".join(partes[: i + 4])


class NormalizadorAzureGraph:
    formato: ClassVar[str] = "azure_resource_graph"

    def __init__(self) -> None:
        self._c = Construtor("azure")

    @classmethod
    def reconhece(cls, objeto: dict[str, Any] | None, primeiro: dict[str, Any] | None) -> bool:
        if objeto is not None:
            dados = objeto.get("data")
            if not isinstance(dados, list):
                return False
            if not dados:
                return "total_records" in objeto or "skip_token" in objeto
            primeiro = dados[0] if isinstance(dados[0], dict) else None
        return (
            primeiro is not None
            and str(primeiro.get("id", "")).lower().startswith("/subscriptions/")
            and isinstance(primeiro.get("type"), str)
        )

    def consumir(self, arquivo: ArquivoLido) -> None:
        self._c.novo_arquivo()
        for linha in self._linhas(arquivo):
            self._linha(linha)

    @staticmethod
    def _linhas(arquivo: ArquivoLido) -> Iterator[dict[str, Any]]:
        if arquivo.itens is not None:
            yield from arquivo.itens
        elif arquivo.objeto is not None:
            yield from (d for d in lista(arquivo.objeto.get("data")) if isinstance(d, dict))

    def _linha(self, linha: dict[str, Any]) -> None:
        chave = arm(linha.get("id"))
        tipo_nativo = str(linha.get("type") or "").lower()
        if not chave or not tipo_nativo:
            self._c.contar("item_sem_identidade")
            return
        props = linha.get("properties")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except json.JSONDecodeError:
                self._c.contar("configuracao_ilegivel")
                props = {}
        props = dicionario(props)
        tipo = TIPOS.get(tipo_nativo, _T.OUTRO)
        if tipo is _T.OUTRO:
            self._c.contar(f"tipo_nao_modelado:{tipo_nativo}")

        conta = texto(linha.get("subscriptionId"))
        regiao = texto(linha.get("location"))
        zonas = lista(linha.get("zones"))
        atributos, estado = self._especifico(
            tipo_nativo=tipo_nativo,
            chave=chave,
            props=props,
            sku=dicionario(linha.get("sku")),
            linha=linha,
            conta=conta,
            regiao=regiao,
        )
        self._c.adicionar(
            Recurso(
                chave=chave,
                tipo=tipo,
                tipo_nativo=tipo_nativo,
                nome=texto(linha.get("name")),
                conta=conta,
                regiao=regiao,
                zona=texto(zonas[0]) if zonas else None,
                estado=estado,
                atributos=sem_vazios(atributos),
                tags=tags_de_objeto(linha.get("tags")),
                apelidos=apelidos(chave, props.get("vmId")),
            )
        )

    # ── por tipo ────────────────────────────────────────────────────────

    def _especifico(  # noqa: PLR0911, PLR0912, PLR0915 - uma seção por tipo
        self,
        *,
        tipo_nativo: str,
        chave: str,
        props: dict[str, Any],
        sku: dict[str, Any],
        linha: dict[str, Any],
        conta: str | None,
        regiao: str | None,
    ) -> tuple[dict[str, Any], EstadoDoRecurso]:
        liga = self._liga
        ativo = EstadoDoRecurso.ATIVO

        if tipo_nativo == "microsoft.compute/virtualmachines":
            disco_so = arm(caminho(props, "storageProfile", "osDisk", "managedDisk", "id"))
            if disco_so:
                liga(disco_so, chave, _R.ANEXADO_A)
                self._c.anotar(disco_so, {"boot": True})
            for d in lista(caminho(props, "storageProfile", "dataDisks")):
                disco = arm(caminho(d, "managedDisk", "id"))
                if disco:
                    liga(disco, chave, _R.ANEXADO_A)
                    self._c.anotar(disco, {"boot": False})
            for nic in lista(caminho(props, "networkProfile", "networkInterfaces")):
                liga(arm(dicionario(nic).get("id")), chave, _R.ANEXADO_A)
            energia = (
                str(caminho(props, "extended", "instanceView", "powerState", "code") or "")
                .lower()
                .removeprefix("powerstate/")
            )
            imagem = dicionario(caminho(props, "storageProfile", "imageReference"))
            return (
                {
                    "tipo_maquina": caminho(props, "hardwareProfile", "vmSize"),
                    "plataforma": caminho(props, "storageProfile", "osDisk", "osType"),
                    "imagem": ":".join(
                        str(imagem[k]) for k in ("publisher", "offer", "sku") if imagem.get(k)
                    )
                    or None,
                },
                ativo
                if energia in _LIGADA
                else EstadoDoRecurso.PARADO
                if energia in _DESLIGADA
                else EstadoDoRecurso.DESCONHECIDO,
            )

        if tipo_nativo == "microsoft.network/networkinterfaces":
            ip_privado = None
            for cfg in lista(props.get("ipConfigurations")):
                p = dicionario(dicionario(cfg).get("properties"))
                liga(chave, arm_pai(caminho(p, "subnet", "id")), _R.CONTIDO_EM)
                if ip_privado is None or booleano(p.get("primary")):
                    ip_privado = texto(p.get("privateIPAddress")) or ip_privado
                liga(arm(caminho(p, "publicIPAddress", "id")), chave, _R.ANEXADO_A)
                for asg in lista(p.get("applicationSecurityGroups")):
                    self._c.relacionar(
                        chave,
                        Referencia(_ASG, arm(dicionario(asg).get("id")) or ""),
                        _R.PROTEGIDO_POR,
                    )
                for pool in lista(p.get("loadBalancerBackendAddressPools")):
                    liga(arm_pai(dicionario(pool).get("id")), chave, _R.ENCAMINHA_PARA)
            liga(chave, arm(caminho(props, "networkSecurityGroup", "id")), _R.PROTEGIDO_POR)
            vm = arm(caminho(props, "virtualMachine", "id"))
            liga(chave, vm, _R.ANEXADO_A)
            return (
                {"ip_privado": ip_privado},
                ativo if vm else EstadoDoRecurso.DISPONIVEL,
            )

        if tipo_nativo == "microsoft.network/virtualnetworks":
            prefixos = [str(p) for p in lista(caminho(props, "addressSpace", "addressPrefixes"))]
            for sub in lista(props.get("subnets")):
                self._subrede(dicionario(sub), chave, conta, regiao)
            return {"cidr": prefixos[0] if prefixos else None, "cidrs": sorted(prefixos)}, ativo

        if tipo_nativo == _SUBREDE:
            rede = chave.rsplit("/subnets/", 1)[0]
            liga(chave, rede, _R.CONTIDO_EM)
            liga(chave, arm(caminho(props, "networkSecurityGroup", "id")), _R.PROTEGIDO_POR)
            return {"cidr": _prefixo_da_subrede(props)}, ativo

        if tipo_nativo == "microsoft.network/networksecuritygroups":
            for regra in lista(props.get("securityRules")):
                self._regra_nsg(chave, dicionario(regra))
            for sub in lista(props.get("subnets")):
                liga(arm_pai(dicionario(sub).get("id")), chave, _R.PROTEGIDO_POR)
            for nic in lista(props.get("networkInterfaces")):
                liga(arm(dicionario(nic).get("id")), chave, _R.PROTEGIDO_POR)
            return {"regras_padrao_omitidas": bool(props.get("defaultSecurityRules"))}, ativo

        if tipo_nativo == _ASG:
            return {"asg": True}, ativo

        if tipo_nativo == "microsoft.compute/disks":
            estado_disco = str(props.get("diskState") or "").lower()
            nome_sku = str(sku.get("name") or "")
            liga(chave, arm(linha.get("managedBy")), _R.ANEXADO_A)
            return (
                {
                    "tamanho_gb": inteiro(props.get("diskSizeGB")),
                    "tipo_disco": nome_sku or None,
                    "categoria_disco": ("hdd" if nome_sku.lower() in _HDD else "ssd")
                    if nome_sku
                    else None,
                    "tier": props.get("tier"),
                    "sistema": props.get("osType"),
                },
                EstadoDoRecurso.DISPONIVEL
                if estado_disco == "unattached"
                else ativo
                if estado_disco
                else EstadoDoRecurso.DESCONHECIDO,
            )

        if tipo_nativo == "microsoft.network/publicipaddresses":
            dono = arm_pai(caminho(props, "ipConfiguration", "id"))
            liga(chave, dono, _R.ANEXADO_A)
            return (
                {"ip_publico": props.get("ipAddress")},
                ativo if dono else EstadoDoRecurso.DISPONIVEL,
            )

        if tipo_nativo in {
            "microsoft.network/loadbalancers",
            "microsoft.network/applicationgateways",
        }:
            publico = False
            for f in lista(props.get("frontendIPConfigurations")):
                p = dicionario(dicionario(f).get("properties"))
                publico = publico or bool(caminho(p, "publicIPAddress", "id"))
                liga(chave, arm_pai(caminho(p, "subnet", "id")), _R.CONTIDO_EM)
            for g in lista(props.get("gatewayIPConfigurations")):
                liga(chave, arm_pai(caminho(g, "properties", "subnet", "id")), _R.CONTIDO_EM)
            for pool in lista(props.get("backendAddressPools")):
                p = dicionario(dicionario(pool).get("properties"))
                for cfg in lista(p.get("backendIPConfigurations")):
                    liga(chave, arm_pai(dicionario(cfg).get("id")), _R.ENCAMINHA_PARA)
            return {"publico": publico, "esquema": "publico" if publico else "interno"}, ativo

        if tipo_nativo in {"microsoft.network/natgateways"}:
            for sub in lista(props.get("subnets")):
                liga(chave, arm_pai(dicionario(sub).get("id")), _R.CONTIDO_EM)
            return {}, ativo

        if tipo_nativo == "microsoft.network/virtualnetworkgateways":
            for cfg in lista(props.get("ipConfigurations")):
                liga(chave, arm_pai(caminho(cfg, "properties", "subnet", "id")), _R.CONTIDO_EM)
            return {}, ativo

        if tipo_nativo in {
            "microsoft.dbforpostgresql/flexibleservers",
            "microsoft.dbformysql/flexibleservers",
        }:
            liga(
                chave,
                arm_pai(caminho(props, "network", "delegatedSubnetResourceId")),
                _R.CONTIDO_EM,
            )
            modo_ha = str(caminho(props, "highAvailability", "mode") or "").lower()
            estado_srv = str(props.get("state") or "").lower()
            return (
                {
                    "classe": sku.get("name"),
                    "tier": sku.get("tier"),
                    "motor": "postgres" if "postgresql" in tipo_nativo else "mysql",
                    "versao": texto(props.get("version")),
                    "alta_disponibilidade": modo_ha not in {"", "disabled"} if modo_ha else None,
                    "publico": str(caminho(props, "network", "publicNetworkAccess") or "").lower()
                    == "enabled"
                    if caminho(props, "network", "publicNetworkAccess")
                    else None,
                    "armazenamento_gb": inteiro(caminho(props, "storage", "storageSizeGB")),
                },
                EstadoDoRecurso.PARADO if estado_srv in {"stopped", "stopping"} else ativo,
            )

        if tipo_nativo in {"microsoft.sql/servers/databases", "microsoft.sql/managedinstances"}:
            liga(chave, arm_pai(props.get("subnetId")), _R.CONTIDO_EM)
            return (
                {
                    "classe": sku.get("name"),
                    "tier": sku.get("tier"),
                    "motor": "sqlserver",
                    "vcpus": inteiro(sku.get("capacity")) if sku.get("family") else None,
                    "alta_disponibilidade": booleano(props.get("zoneRedundant")),
                },
                ativo,
            )

        if tipo_nativo == "microsoft.cache/redis":
            liga(chave, arm_pai(props.get("subnetId")), _R.CONTIDO_EM)
            familia = dicionario(props.get("sku")) or sku
            return (
                {
                    "classe": f"{familia.get('family', '')}{familia.get('capacity', '')}" or None,
                    "motor": "redis",
                    "versao": props.get("redisVersion"),
                    "publico": str(props.get("publicNetworkAccess") or "").lower() == "enabled"
                    if props.get("publicNetworkAccess")
                    else None,
                },
                ativo,
            )

        if tipo_nativo == "microsoft.containerservice/managedclusters":
            for pool in lista(props.get("agentPoolProfiles")):
                liga(chave, arm_pai(dicionario(pool).get("vnetSubnetID")), _R.CONTIDO_EM)
            return {"versao": props.get("kubernetesVersion")}, ativo

        if tipo_nativo == "microsoft.storage/storageaccounts":
            return {"publico": booleano(props.get("allowBlobPublicAccess"))}, ativo

        return {}, ativo

    def _liga(self, de: str | None, para: str | None, relacao: TipoDeRelacao) -> None:
        if de and para:
            self._c.relacionar(de, para, relacao)

    def _subrede(
        self, sub: dict[str, Any], rede: str, conta: str | None, regiao: str | None
    ) -> None:
        chave = arm(sub.get("id"))
        if not chave:
            return
        props = dicionario(sub.get("properties"))
        self._c.adicionar(
            Recurso(
                chave=chave,
                tipo=_T.SUBREDE,
                tipo_nativo=_SUBREDE,
                nome=texto(sub.get("name")),
                conta=conta,
                regiao=regiao,
                estado=EstadoDoRecurso.ATIVO,
                atributos=sem_vazios({"cidr": _prefixo_da_subrede(props)}),
                apelidos=apelidos(chave),
            )
        )
        self._c.relacionar(chave, rede, _R.CONTIDO_EM)
        self._liga(chave, arm(caminho(props, "networkSecurityGroup", "id")), _R.PROTEGIDO_POR)
        self._liga(arm(caminho(props, "natGateway", "id")), chave, _R.CONTIDO_EM)

    def _regra_nsg(self, grupo: str, regra: dict[str, Any]) -> None:
        p = dicionario(regra.get("properties")) or regra
        direcao = (
            Direcao.SAIDA
            if str(p.get("direction") or "").lower() == "outbound"
            else Direcao.ENTRADA
        )
        acao = Acao.NEGAR if str(p.get("access") or "").lower() == "deny" else Acao.PERMITIR
        protocolo = protocolo_canonico(p.get("protocol"))
        portas = [faixa_de_portas(x) for x in [p.get("destinationPortRange")] if x]
        portas += [faixa_de_portas(x) for x in lista(p.get("destinationPortRanges"))]
        if not portas:
            portas = [(None, None)]
        origens: list[tuple[Seletor, Referencia | None]] = [
            (seletor_de_endereco(x), None)
            for x in [p.get("sourceAddressPrefix"), *lista(p.get("sourceAddressPrefixes"))]
            if x
        ]
        for asg in lista(p.get("sourceApplicationSecurityGroups")):
            ident = arm(dicionario(asg).get("id"))
            if ident:
                origens.append((Seletor(TipoDeSeletor.GRUPO, ident), Referencia(_ASG, ident)))
        if not origens:
            origens = [(Seletor(TipoDeSeletor.QUALQUER), None)]
        destinos: list[tuple[Seletor | None, Referencia | None]] = []
        for asg in lista(p.get("destinationApplicationSecurityGroups")):
            ident = arm(dicionario(asg).get("id"))
            if ident:
                destinos.append((Seletor(TipoDeSeletor.GRUPO, ident), Referencia(_ASG, ident)))
        for x in [p.get("destinationAddressPrefix"), *lista(p.get("destinationAddressPrefixes"))]:
            seletor = seletor_de_endereco(x) if x else None
            # `*` e `VirtualNetwork` no destino = todos os membros do NSG.
            if seletor is not None and seletor.tipo is TipoDeSeletor.CIDR:
                destinos.append((seletor, None))
        if not destinos:
            destinos = [(None, None)]
        for origem, ref_origem in origens:
            for destino, ref_destino in destinos:
                for inicio, fim in portas:
                    self._c.regra(
                        RegraDeRede(
                            grupo=grupo,
                            direcao=direcao,
                            acao=acao,
                            protocolo=protocolo,
                            origem=origem,
                            destino=destino,
                            porta_inicio=inicio if protocolo in {"tcp", "udp"} else None,
                            porta_fim=fim if protocolo in {"tcp", "udp"} else None,
                            prioridade=inteiro(p.get("priority")),
                            descricao=texto(regra.get("name")),
                        ),
                        origem=ref_origem,
                        destino=ref_destino,
                    )

    def _resolver(self, ref: Referencia) -> str | None:
        return ref.ident if self._c.existe(ref.ident) else None

    def finalizar(self) -> Inventario:
        return self._c.finalizar(self._resolver)


def _prefixo_da_subrede(props: dict[str, Any]) -> str | None:
    prefixo = texto(props.get("addressPrefix"))
    if prefixo:
        return prefixo
    prefixos = lista(props.get("addressPrefixes"))
    return texto(prefixos[0]) if prefixos else None
