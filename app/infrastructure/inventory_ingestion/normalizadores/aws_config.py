"""AWS Config: snapshot de configuração → modelo canônico.

Formato conferido no exemplo oficial
(https://docs.aws.amazon.com/config/latest/developerguide/example-s3-snapshot.html):
``{"fileVersion", "configSnapshotId", "configurationItems": [...]}``. Também
aceita a saída de ``select-resource-config`` (``{"Results": ["<json>", ...]}``)
e NDJSON de itens (export do agregador via Athena).

Relações saem **primeiro dos campos de configuração** (``subnetId``,
``securityGroups``, ``blockDeviceMappings``…), que são estruturados; a lista
``relationships`` só complementa, e o tipo da relação vem do par de tipos
canônicos, nunca do texto ("Is attached to Instance" muda de grafia entre
serviços e versões).

A referência do Config cita ``resourceId`` sem conta nem região. Resolve-se
primeiro na conta e região do item que cita, depois pelo par (tipo, id) se for
único no inventário (RI1).
"""

from __future__ import annotations

import json
from collections import defaultdict
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
from app.infrastructure.inventory_ingestion.leitura import ArquivoLido, InventarioInvalidoError
from app.infrastructure.inventory_ingestion.normalizadores.comum import (
    apelidos,
    booleano,
    caminho,
    dicionario,
    instante,
    inteiro,
    lista,
    portas_da_aws,
    protocolo_canonico,
    seletor_de_endereco,
    sem_vazios,
    tags_de_objeto,
    tags_de_pares,
    texto,
)

_T = TipoDeRecurso
_R = TipoDeRelacao

TIPOS: dict[str, TipoDeRecurso] = {
    "AWS::EC2::Instance": _T.INSTANCIA,
    "AWS::EC2::Volume": _T.DISCO,
    "AWS::EC2::NetworkInterface": _T.INTERFACE_REDE,
    "AWS::EC2::EIP": _T.IP_PUBLICO,
    "AWS::EC2::VPC": _T.REDE,
    "AWS::EC2::Subnet": _T.SUBREDE,
    "AWS::EC2::SecurityGroup": _T.GRUPO_SEGURANCA,
    "AWS::EC2::NatGateway": _T.GATEWAY,
    "AWS::EC2::InternetGateway": _T.GATEWAY,
    "AWS::EC2::EgressOnlyInternetGateway": _T.GATEWAY,
    "AWS::EC2::VPNGateway": _T.GATEWAY,
    "AWS::EC2::CustomerGateway": _T.GATEWAY,
    "AWS::EC2::VPNConnection": _T.GATEWAY,
    "AWS::EC2::TransitGateway": _T.GATEWAY,
    "AWS::ElasticLoadBalancingV2::LoadBalancer": _T.BALANCEADOR,
    "AWS::ElasticLoadBalancing::LoadBalancer": _T.BALANCEADOR,
    "AWS::RDS::DBInstance": _T.BANCO,
    "AWS::ElastiCache::CacheCluster": _T.CACHE,
    "AWS::S3::Bucket": _T.BUCKET,
    "AWS::EKS::Cluster": _T.CLUSTER_KUBERNETES,
}
# Não é unidade, mas é conector: as instâncias do mesmo Aurora ficam juntas.
_CLUSTER_RDS = "AWS::RDS::DBCluster"
_SG = "AWS::EC2::SecurityGroup"

# O próprio exemplo oficial cita `AWS::EC2::SUBNET` numa relação: a grafia do
# tipo não é confiável fora do campo `resourceType` do item.
_GRAFIA = {t.casefold(): t for t in (*TIPOS, _CLUSTER_RDS)}

_REMOVIDO = frozenset({"ResourceDeleted", "ResourceDeletedNotRecorded"})
_SSD = frozenset({"gp2", "gp3", "io1", "io2"})

# Par de tipos canônicos → (relação, tipo que fica do lado "de"). Para a lista
# `relationships`, que pode vir de qualquer um dos dois lados.
_PARES: dict[frozenset[TipoDeRecurso], tuple[TipoDeRelacao, TipoDeRecurso]] = {
    frozenset({_T.DISCO, _T.INSTANCIA}): (_R.ANEXADO_A, _T.DISCO),
    frozenset({_T.INTERFACE_REDE, _T.INSTANCIA}): (_R.ANEXADO_A, _T.INTERFACE_REDE),
    frozenset({_T.IP_PUBLICO, _T.INSTANCIA}): (_R.ANEXADO_A, _T.IP_PUBLICO),
    frozenset({_T.IP_PUBLICO, _T.INTERFACE_REDE}): (_R.ANEXADO_A, _T.IP_PUBLICO),
    frozenset({_T.SUBREDE, _T.REDE}): (_R.CONTIDO_EM, _T.SUBREDE),
    frozenset({_T.BALANCEADOR, _T.INSTANCIA}): (_R.ENCAMINHA_PARA, _T.BALANCEADOR),
    frozenset({_T.GRUPO_SEGURANCA, _T.REDE}): (_R.CONTIDO_EM, _T.GRUPO_SEGURANCA),
    frozenset({_T.GATEWAY, _T.REDE}): (_R.CONTIDO_EM, _T.GATEWAY),
    **{
        frozenset({x, _T.SUBREDE}): (_R.CONTIDO_EM, x)
        for x in (
            _T.INSTANCIA,
            _T.INTERFACE_REDE,
            _T.BANCO,
            _T.CACHE,
            _T.BALANCEADOR,
            _T.GATEWAY,
            _T.CLUSTER_KUBERNETES,
        )
    },
    **{
        frozenset({x, _T.GRUPO_SEGURANCA}): (_R.PROTEGIDO_POR, x)
        for x in (
            _T.INSTANCIA,
            _T.INTERFACE_REDE,
            _T.BANCO,
            _T.CACHE,
            _T.BALANCEADOR,
            _T.CLUSTER_KUBERNETES,
        )
    },
}


class NormalizadorAwsConfig:
    formato: ClassVar[str] = "aws_config"

    def __init__(self) -> None:
        self._c = Construtor("aws")
        # (conta, região, tipo, id) → chave; e os índices globais de fallback.
        self._exato: dict[tuple[str, str, str, str], str] = {}
        self._por_id: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._por_nome: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._nome_exato: dict[tuple[str, str, str, str], str] = {}

    @classmethod
    def reconhece(cls, objeto: dict[str, Any] | None, primeiro: dict[str, Any] | None) -> bool:
        if objeto is not None:
            if isinstance(objeto.get("configurationItems"), list):
                return True
            resultados = objeto.get("Results")
            if isinstance(resultados, list) and resultados and isinstance(resultados[0], str):
                return '"resourceType"' in resultados[0] and "AWS::" in resultados[0]
            return _e_item(objeto)
        return primeiro is not None and _e_item(primeiro)

    # ── leitura ─────────────────────────────────────────────────────────

    def consumir(self, arquivo: ArquivoLido) -> None:
        self._c.novo_arquivo()
        for item in self._itens(arquivo):
            self._item(item)

    def _itens(self, arquivo: ArquivoLido) -> Iterator[dict[str, Any]]:
        if arquivo.itens is not None:
            yield from arquivo.itens
            return
        objeto = arquivo.objeto or {}
        if isinstance(objeto.get("configurationItems"), list):
            yield from (i for i in objeto["configurationItems"] if isinstance(i, dict))
        elif isinstance(objeto.get("Results"), list):
            for bruto in objeto["Results"]:
                try:
                    item = json.loads(bruto) if isinstance(bruto, str) else bruto
                except json.JSONDecodeError as e:
                    raise InventarioInvalidoError(
                        f"{arquivo.nome}: item de `Results` não é JSON"
                    ) from e
                if isinstance(item, dict):
                    yield item
        else:
            yield objeto

    def _item(self, ci: dict[str, Any]) -> None:
        if ci.get("configurationItemStatus") in _REMOVIDO:
            self._c.contar("removido")
            return
        tipo_nativo = _nativo(ci.get("resourceType"))
        rid = texto(ci.get("resourceId"))
        if not tipo_nativo or not rid:
            self._c.contar("item_sem_identidade")
            return
        conta = texto(ci.get("accountId")) or ""
        regiao = texto(ci.get("awsRegion")) or ""
        config = ci.get("configuration")
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                self._c.contar("configuracao_ilegivel")
                config = {}
        config = dicionario(config)
        arn = texto(ci.get("arn"))
        chave = (
            arn if arn and arn.startswith("arn:") else f"aws:{conta}:{regiao}:{tipo_nativo}:{rid}"
        )
        tipo = TIPOS.get(tipo_nativo, _T.OUTRO)
        if tipo is _T.OUTRO and tipo_nativo != _CLUSTER_RDS:
            self._c.contar(f"tipo_nao_modelado:{tipo_nativo}")

        tags = {**tags_de_pares(lista(config.get("tags"))), **tags_de_objeto(ci.get("tags"))}
        nome = texto(ci.get("resourceName")) or tags.get("Name") or rid
        atributos, estado, extras = self._especifico(
            tipo_nativo=tipo_nativo, chave=chave, cfg=config, conta=conta, regiao=regiao, tags=tags
        )
        if estado is None:
            self._c.contar("removido")
            return

        self._c.adicionar(
            Recurso(
                chave=chave,
                tipo=tipo,
                tipo_nativo=tipo_nativo,
                nome=nome,
                conta=conta or None,
                regiao=regiao or None,
                zona=texto(ci.get("availabilityZone") or ci.get("availibilityZone")),
                estado=estado,
                atributos=sem_vazios(atributos),
                tags=tags,
                apelidos=apelidos(chave, arn, rid, ci.get("resourceName"), *extras),
                capturado_em=instante(ci.get("configurationItemCaptureTime")),
            )
        )
        self._exato[(conta, regiao, tipo_nativo, rid)] = chave
        self._por_id[(tipo_nativo, rid)].add(chave)
        nome_nativo = texto(ci.get("resourceName"))
        if nome_nativo:
            self._por_nome[(tipo_nativo, nome_nativo)].add(chave)
            self._nome_exato[(conta, regiao, tipo_nativo, nome_nativo)] = chave

        for rel in lista(ci.get("relationships")):
            self._relacao_declarada(chave, tipo, dicionario(rel), conta, regiao)

    def _relacao_declarada(
        self, chave: str, tipo: TipoDeRecurso, rel: dict[str, Any], conta: str, regiao: str
    ) -> None:
        tipo_alvo_nativo = _nativo(rel.get("resourceType"))
        ident = texto(rel.get("resourceId"))
        por_nome = False
        if not ident:
            ident, por_nome = texto(rel.get("resourceName")), True
        if not tipo_alvo_nativo or not ident:
            return
        tipo_alvo = TIPOS.get(tipo_alvo_nativo, _T.OUTRO)
        par = _PARES.get(frozenset({tipo, tipo_alvo}))
        if par is None or tipo == tipo_alvo:
            self._c.contar("relacao_do_provedor_ignorada")
            return
        relacao, lado_de = par
        alvo = Referencia(tipo_alvo_nativo, ident, conta, regiao, por_nome=por_nome)
        if tipo == lado_de:
            self._c.relacionar(chave, alvo, relacao)
        else:
            self._c.relacionar(alvo, chave, relacao)

    # ── por tipo ────────────────────────────────────────────────────────

    def _ref(self, tipo_nativo: str, ident: object, conta: str, regiao: str) -> Referencia | None:
        valor = texto(ident)
        return Referencia(tipo_nativo, valor, conta, regiao) if valor else None

    def _liga(
        self,
        de: str | Referencia | None,
        para: str | Referencia | None,
        relacao: TipoDeRelacao,
    ) -> None:
        if de is not None and para is not None:
            self._c.relacionar(de, para, relacao)

    def _especifico(  # noqa: PLR0911, PLR0912, PLR0915 - uma seção por tipo
        self,
        *,
        tipo_nativo: str,
        chave: str,
        cfg: dict[str, Any],
        conta: str,
        regiao: str,
        tags: dict[str, str],
    ) -> tuple[dict[str, Any], EstadoDoRecurso | None, list[object]]:
        ref = self._ref
        liga = self._liga
        ativo = EstadoDoRecurso.ATIVO

        if tipo_nativo == "AWS::EC2::Instance":
            nome_estado = str(caminho(cfg, "state", "name") or "").lower()
            if nome_estado in {"terminated", "shutting-down"}:
                return {}, None, []
            estado = {
                "running": ativo,
                "pending": ativo,
                "stopped": EstadoDoRecurso.PARADO,
                "stopping": EstadoDoRecurso.PARADO,
            }.get(nome_estado, EstadoDoRecurso.DESCONHECIDO)
            nucleos = inteiro(caminho(cfg, "cpuOptions", "coreCount"))
            threads = inteiro(caminho(cfg, "cpuOptions", "threadsPerCore"))
            raiz = texto(cfg.get("rootDeviceName"))
            liga(chave, ref("AWS::EC2::Subnet", cfg.get("subnetId"), conta, regiao), _R.CONTIDO_EM)
            for g in lista(cfg.get("securityGroups")):
                liga(chave, ref(_SG, dicionario(g).get("groupId"), conta, regiao), _R.PROTEGIDO_POR)
            for bruto in lista(cfg.get("blockDeviceMappings")):
                mapa = dicionario(bruto)
                volume = ref("AWS::EC2::Volume", caminho(mapa, "ebs", "volumeId"), conta, regiao)
                liga(volume, chave, _R.ANEXADO_A)
                if volume is not None:
                    self._c.anotar(volume, {"boot": bool(raiz) and mapa.get("deviceName") == raiz})
            for bruto in lista(cfg.get("networkInterfaces")):
                eni = dicionario(bruto)
                liga(
                    ref("AWS::EC2::NetworkInterface", eni.get("networkInterfaceId"), conta, regiao),
                    chave,
                    _R.ANEXADO_A,
                )
            cluster = tags.get("eks:cluster-name")
            if cluster:
                self._c.relacionar(
                    chave,
                    Referencia("AWS::EKS::Cluster", cluster, conta, regiao, por_nome=True),
                    _R.MEMBRO_DE,
                )
            return (
                {
                    "tipo_maquina": cfg.get("instanceType"),
                    "vcpus": nucleos * threads if nucleos and threads else None,
                    "ip_privado": cfg.get("privateIpAddress"),
                    "ip_publico": cfg.get("publicIpAddress"),
                    "imagem": cfg.get("imageId"),
                    "plataforma": cfg.get("platformDetails") or cfg.get("platform"),
                    "arquitetura": cfg.get("architecture"),
                    "tipo_raiz": cfg.get("rootDeviceType"),
                },
                estado,
                [],
            )

        if tipo_nativo == "AWS::EC2::Volume":
            estado_vol = str(cfg.get("state") or "").lower()
            tipo_disco = texto(cfg.get("volumeType"))
            for a in lista(cfg.get("attachments")):
                liga(
                    chave,
                    ref("AWS::EC2::Instance", dicionario(a).get("instanceId"), conta, regiao),
                    _R.ANEXADO_A,
                )
            return (
                {
                    "tamanho_gb": inteiro(cfg.get("size")),
                    "tipo_disco": tipo_disco,
                    "categoria_disco": ("ssd" if tipo_disco in _SSD else "hdd")
                    if tipo_disco
                    else None,
                    "iops": inteiro(cfg.get("iops")),
                    "criptografado": booleano(cfg.get("encrypted")),
                },
                {"in-use": ativo, "available": EstadoDoRecurso.DISPONIVEL}.get(
                    estado_vol, EstadoDoRecurso.DESCONHECIDO
                ),
                [],
            )

        if tipo_nativo == "AWS::EC2::NetworkInterface":
            liga(chave, ref("AWS::EC2::Subnet", cfg.get("subnetId"), conta, regiao), _R.CONTIDO_EM)
            for g in lista(cfg.get("groups")):
                liga(chave, ref(_SG, dicionario(g).get("groupId"), conta, regiao), _R.PROTEGIDO_POR)
            liga(
                chave,
                ref("AWS::EC2::Instance", caminho(cfg, "attachment", "instanceId"), conta, regiao),
                _R.ANEXADO_A,
            )
            status = str(cfg.get("status") or "").lower()
            return (
                {
                    "ip_privado": cfg.get("privateIpAddress"),
                    "ip_publico": caminho(cfg, "association", "publicIp"),
                    "tipo_interface": cfg.get("interfaceType"),
                    "gerenciada_pelo_provedor": booleano(cfg.get("requesterManaged")),
                },
                {"in-use": ativo, "available": EstadoDoRecurso.DISPONIVEL}.get(
                    status, EstadoDoRecurso.DESCONHECIDO
                ),
                [],
            )

        if tipo_nativo == "AWS::EC2::EIP":
            instancia = ref("AWS::EC2::Instance", cfg.get("instanceId"), conta, regiao)
            interface = ref(
                "AWS::EC2::NetworkInterface", cfg.get("networkInterfaceId"), conta, regiao
            )
            liga(chave, instancia or interface, _R.ANEXADO_A)
            associado = bool(instancia or interface or cfg.get("associationId"))
            return (
                {"ip_publico": cfg.get("publicIp")},
                ativo if associado else EstadoDoRecurso.DISPONIVEL,
                [cfg.get("allocationId"), cfg.get("publicIp")],
            )

        if tipo_nativo == "AWS::EC2::VPC":
            cidrs = sorted(
                {str(c) for c in [cfg.get("cidrBlock")] if c}
                | {
                    str(dicionario(a).get("cidrBlock"))
                    for a in lista(cfg.get("cidrBlockAssociationSet"))
                    if dicionario(a).get("cidrBlock")
                }
            )
            return (
                {
                    "cidr": cfg.get("cidrBlock"),
                    "cidrs": cidrs,
                    "padrao": booleano(cfg.get("isDefault")),
                },
                ativo,
                [],
            )

        if tipo_nativo == "AWS::EC2::Subnet":
            liga(chave, ref("AWS::EC2::VPC", cfg.get("vpcId"), conta, regiao), _R.CONTIDO_EM)
            return (
                {
                    "cidr": cfg.get("cidrBlock"),
                    "publica": booleano(cfg.get("mapPublicIpOnLaunch")),
                },
                ativo,
                [],
            )

        if tipo_nativo == _SG:
            liga(chave, ref("AWS::EC2::VPC", cfg.get("vpcId"), conta, regiao), _R.CONTIDO_EM)
            for p in lista(cfg.get("ipPermissions")):
                self._regras(chave, dicionario(p), Direcao.ENTRADA, conta, regiao)
            saida = [dicionario(p) for p in lista(cfg.get("ipPermissionsEgress"))]
            for p in saida:
                self._regras(chave, p, Direcao.SAIDA, conta, regiao)
            return (
                {
                    "nome_grupo": cfg.get("groupName"),
                    "restringe_saida": not any(_libera_tudo(p) for p in saida),
                },
                ativo,
                [cfg.get("groupId")],
            )

        if tipo_nativo == "AWS::RDS::DBInstance":
            identificador = texto(cfg.get("dBInstanceIdentifier"))
            for g in lista(cfg.get("vpcSecurityGroups")):
                liga(
                    chave,
                    ref(_SG, dicionario(g).get("vpcSecurityGroupId"), conta, regiao),
                    _R.PROTEGIDO_POR,
                )
            for s in lista(caminho(cfg, "dBSubnetGroup", "subnets")):
                liga(
                    chave,
                    ref("AWS::EC2::Subnet", dicionario(s).get("subnetIdentifier"), conta, regiao),
                    _R.CONTIDO_EM,
                )
            cluster = texto(cfg.get("dBClusterIdentifier"))
            if cluster:
                self._c.relacionar(
                    chave,
                    Referencia(_CLUSTER_RDS, cluster, conta, regiao, por_nome=True),
                    _R.MEMBRO_DE,
                )
            status = str(cfg.get("dBInstanceStatus") or "").lower()
            return (
                {
                    "classe": cfg.get("dBInstanceClass"),
                    "motor": cfg.get("engine"),
                    "versao": cfg.get("engineVersion"),
                    "alta_disponibilidade": booleano(cfg.get("multiAZ")),
                    "armazenamento_gb": inteiro(cfg.get("allocatedStorage")),
                    "tipo_armazenamento": cfg.get("storageType"),
                    "publico": booleano(cfg.get("publiclyAccessible")),
                    "porta": inteiro(caminho(cfg, "endpoint", "port") or cfg.get("dbInstancePort")),
                    "identificador": identificador,
                },
                {"stopped": EstadoDoRecurso.PARADO, "stopping": EstadoDoRecurso.PARADO}.get(
                    status, ativo
                ),
                [
                    identificador,
                    f"arn:aws:rds:{regiao}:{conta}:db:{identificador}" if identificador else None,
                ],
            )

        if tipo_nativo == _CLUSTER_RDS:
            return (
                {
                    "motor": cfg.get("engine"),
                    "versao": cfg.get("engineVersion"),
                    "identificador": cfg.get("dBClusterIdentifier"),
                },
                ativo,
                [],
            )

        if tipo_nativo in {
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            "AWS::ElasticLoadBalancing::LoadBalancer",
        }:
            esquema = texto(cfg.get("scheme"))
            for g in lista(cfg.get("securityGroups")):
                liga(
                    chave,
                    ref(
                        _SG,
                        g if isinstance(g, str) else dicionario(g).get("groupId"),
                        conta,
                        regiao,
                    ),
                    _R.PROTEGIDO_POR,
                )
            subredes = [dicionario(z).get("subnetId") for z in lista(cfg.get("availabilityZones"))]
            subredes += [s for s in lista(cfg.get("subnets")) if isinstance(s, str)]
            for s in subredes:
                liga(chave, ref("AWS::EC2::Subnet", s, conta, regiao), _R.CONTIDO_EM)
            for i in lista(cfg.get("instances")):
                liga(
                    chave,
                    ref("AWS::EC2::Instance", dicionario(i).get("instanceId"), conta, regiao),
                    _R.ENCAMINHA_PARA,
                )
            return (
                {
                    "esquema": esquema,
                    "publico": esquema == "internet-facing" if esquema else None,
                    "tipo_balanceador": cfg.get("type") or "classic",
                    "dns": cfg.get("dNSName") or cfg.get("dnsname"),
                },
                ativo,
                [cfg.get("loadBalancerName")],
            )

        if tipo_nativo == "AWS::ElastiCache::CacheCluster":
            for g in lista(cfg.get("securityGroups")):
                liga(
                    chave,
                    ref(_SG, dicionario(g).get("securityGroupId"), conta, regiao),
                    _R.PROTEGIDO_POR,
                )
            return (
                {
                    "classe": cfg.get("cacheNodeType"),
                    "motor": cfg.get("engine"),
                    "versao": cfg.get("engineVersion"),
                    "nos": inteiro(cfg.get("numCacheNodes")),
                },
                ativo,
                [cfg.get("cacheClusterId")],
            )

        if tipo_nativo in {"AWS::EC2::NatGateway"}:
            liga(chave, ref("AWS::EC2::Subnet", cfg.get("subnetId"), conta, regiao), _R.CONTIDO_EM)
            return {}, ativo, [cfg.get("natGatewayId")]

        if tipo_nativo in {
            "AWS::EC2::InternetGateway",
            "AWS::EC2::VPNGateway",
            "AWS::EC2::EgressOnlyInternetGateway",
        }:
            for a in lista(cfg.get("attachments")) + lista(cfg.get("vpcAttachments")):
                liga(
                    chave,
                    ref("AWS::EC2::VPC", dicionario(a).get("vpcId"), conta, regiao),
                    _R.CONTIDO_EM,
                )
            return {}, ativo, []

        if tipo_nativo == "AWS::EKS::Cluster":
            vpc = dicionario(cfg.get("resourcesVpcConfig") or cfg.get("ResourcesVpcConfig"))
            for s in lista(vpc.get("subnetIds") or vpc.get("SubnetIds")):
                liga(chave, ref("AWS::EC2::Subnet", s, conta, regiao), _R.CONTIDO_EM)
            grupos = lista(vpc.get("securityGroupIds") or vpc.get("SecurityGroupIds"))
            grupos += [vpc.get("clusterSecurityGroupId")]
            for g in grupos:
                liga(chave, ref(_SG, g, conta, regiao), _R.PROTEGIDO_POR)
            return {"versao": cfg.get("version") or cfg.get("Version")}, ativo, [cfg.get("name")]

        if tipo_nativo == "AWS::S3::Bucket":
            return {}, ativo, [cfg.get("name")]

        return {}, ativo, []

    def _regras(
        self, grupo: str, perm: dict[str, Any], direcao: Direcao, conta: str, regiao: str
    ) -> None:
        protocolo = protocolo_canonico(perm.get("ipProtocol"))
        inicio, fim = portas_da_aws(protocolo, perm.get("fromPort"), perm.get("toPort"))

        def nova(origem: Seletor, descricao: object = None) -> RegraDeRede:
            return RegraDeRede(
                grupo=grupo,
                direcao=direcao,
                acao=Acao.PERMITIR,
                protocolo=protocolo,
                origem=origem,
                porta_inicio=inicio,
                porta_fim=fim,
                descricao=texto(descricao),
            )

        faixas: list[tuple[object, object]] = []
        for r in lista(perm.get("ipRanges")):
            faixas.append(
                (r, None)
                if isinstance(r, str)
                else (dicionario(r).get("cidrIp"), dicionario(r).get("description"))
            )
        for r in lista(perm.get("ipv4Ranges")):
            faixas.append((dicionario(r).get("cidrIp"), dicionario(r).get("description")))
        for r in lista(perm.get("ipv6Ranges")):
            faixas.append((dicionario(r).get("cidrIpv6"), dicionario(r).get("description")))
        vistos: set[str] = set()
        for cidr, descricao in faixas:
            if not cidr or str(cidr) in vistos:
                continue
            vistos.add(str(cidr))
            self._c.regra(nova(seletor_de_endereco(cidr), descricao))
        for bruto in lista(perm.get("userIdGroupPairs")):
            par = dicionario(bruto)
            ident = texto(par.get("groupId"))
            if not ident:
                continue
            self._c.regra(
                nova(Seletor(TipoDeSeletor.GRUPO, ident), par.get("description")),
                origem=Referencia(_SG, ident, texto(par.get("userId")) or conta, regiao),
            )
        for pl in lista(perm.get("prefixListIds")):
            ident = texto(dicionario(pl).get("prefixListId"))
            if ident:
                self._c.regra(nova(Seletor(TipoDeSeletor.SERVICO, f"prefix-list:{ident}")))

    # ── saída ───────────────────────────────────────────────────────────

    def _resolver(self, ref: Referencia) -> str | None:
        conta, regiao = ref.conta or "", ref.regiao or ""
        if ref.por_nome:
            exato = self._nome_exato.get((conta, regiao, ref.tipo_nativo, ref.ident))
            candidatos = self._por_nome.get((ref.tipo_nativo, ref.ident), set())
        else:
            exato = self._exato.get((conta, regiao, ref.tipo_nativo, ref.ident))
            candidatos = self._por_id.get((ref.tipo_nativo, ref.ident), set())
        if exato:
            return exato
        return next(iter(candidatos)) if len(candidatos) == 1 else None

    def finalizar(self) -> Inventario:
        return self._c.finalizar(self._resolver)


def _nativo(valor: object) -> str | None:
    bruto = texto(valor)
    return _GRAFIA.get(bruto.casefold(), bruto) if bruto else None


def _e_item(obj: dict[str, Any]) -> bool:
    tipo = obj.get("resourceType")
    return isinstance(tipo, str) and tipo.startswith("AWS::") and "resourceId" in obj


def _libera_tudo(perm: dict[str, Any]) -> bool:
    if protocolo_canonico(perm.get("ipProtocol")) != "todos":
        return False
    faixas = [
        r if isinstance(r, str) else dicionario(r).get("cidrIp")
        for r in lista(perm.get("ipRanges")) + lista(perm.get("ipv4Ranges"))
    ]
    return "0.0.0.0/0" in faixas
