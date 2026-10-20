"""GCP Cloud Asset Inventory → modelo canônico.

Aceita as duas saídas oficiais: o export para Cloud Storage (NDJSON, chaves do
proto: ``asset_type``, ``update_time``) e o ``gcloud asset list --format=json``
(array, camelCase: ``assetType``, ``updateTime``). A diferença de grafia é só
nas chaves de topo; o ``resource.data`` é sempre o recurso REST, em camelCase.
(https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/Asset)

Três particularidades do GCP:

- **Referência é self-link** (``https://www.googleapis.com/compute/v1/projects/
  p/zones/z/instances/vm``) e vira o nome do asset (``//compute.googleapis.com/
  projects/p/zones/z/instances/vm``).
- **Firewall não é grupo, é regra com alvo** (RI4). Cada tag alvo vira um grupo
  sintético ``gcp:<rede>:tag:<tag>``, cada service account alvo
  ``gcp:<rede>:sa:<email>``, e firewall sem alvo vale para a rede inteira
  (``gcp:<rede>:todas``, marcado amplo). Os membros são as VMs da rede com a
  tag. Montado no fim, porque as VMs podem estar noutro arquivo.
- **O billing cita a VM pelo ID numérico** e o asset, pelo nome. O apelido com o
  ID numérico é gerado aqui (RI13).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
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
    instante,
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
    "compute.googleapis.com/Instance": _T.INSTANCIA,
    "compute.googleapis.com/Disk": _T.DISCO,
    "compute.googleapis.com/RegionDisk": _T.DISCO,
    "compute.googleapis.com/Snapshot": _T.SNAPSHOT,
    "compute.googleapis.com/Network": _T.REDE,
    "compute.googleapis.com/Subnetwork": _T.SUBREDE,
    "compute.googleapis.com/Address": _T.IP_PUBLICO,
    "compute.googleapis.com/GlobalAddress": _T.IP_PUBLICO,
    "compute.googleapis.com/ForwardingRule": _T.BALANCEADOR,
    "compute.googleapis.com/GlobalForwardingRule": _T.BALANCEADOR,
    "compute.googleapis.com/Router": _T.GATEWAY,
    "compute.googleapis.com/VpnGateway": _T.GATEWAY,
    "compute.googleapis.com/TargetVpnGateway": _T.GATEWAY,
    "sqladmin.googleapis.com/Instance": _T.BANCO,
    "redis.googleapis.com/Instance": _T.CACHE,
    "storage.googleapis.com/Bucket": _T.BUCKET,
    "container.googleapis.com/Cluster": _T.CLUSTER_KUBERNETES,
}
_FIREWALL = "compute.googleapis.com/Firewall"
_CLUSTER = "container.googleapis.com/Cluster"
_TIPO_GRUPO = "compute.googleapis.com/Firewall#alvo"

_SELF_LINK = re.compile(r"^https?://(?:www\.|compute\.)?googleapis\.com/compute/[a-z0-9]+/")
_PROJETO = re.compile(r"/projects/([^/]+)/")
_ZONA = re.compile(r"^[a-z]+-[a-z]+\d+-[a-z]$")

_EXECUTANDO = {"RUNNING", "PROVISIONING", "STAGING", "RUNNABLE", "READY"}
_PARADO = {"TERMINATED", "STOPPED", "STOPPING", "SUSPENDED", "SUSPENDING"}


def nome_do_asset(ref: object) -> str | None:
    """Self-link, caminho relativo ou nome de asset → nome de asset."""
    valor = texto(ref)
    if not valor:
        return None
    if valor.startswith("//"):
        return valor
    m = _SELF_LINK.match(valor)
    if m:
        return "//compute.googleapis.com/" + valor[m.end() :]
    if valor.startswith("projects/"):
        return "//compute.googleapis.com/" + valor
    return valor


def _ultimo(ref: object) -> str | None:
    valor = texto(ref)
    return valor.rstrip("/").rsplit("/", 1)[-1] if valor else None


@dataclass
class _Vm:
    chave: str
    redes: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    contas_servico: set[str] = field(default_factory=set)


class NormalizadorGcpAsset:
    formato: ClassVar[str] = "gcp_asset_inventory"

    def __init__(self) -> None:
        self._c = Construtor("gcp")
        self._vms: list[_Vm] = []
        self._firewalls: list[dict[str, Any]] = []
        self._clusters: dict[tuple[str, str], str] = {}
        # chave do grupo sintético → (rede, seletor, valor)
        self._grupos: dict[str, tuple[str, str, str | None]] = {}

    @classmethod
    def reconhece(cls, objeto: dict[str, Any] | None, primeiro: dict[str, Any] | None) -> bool:
        amostra = objeto if objeto is not None else primeiro
        if amostra is None:
            return False
        tipo = amostra.get("asset_type", amostra.get("assetType"))
        return isinstance(tipo, str) and str(amostra.get("name", "")).startswith("//")

    def consumir(self, arquivo: ArquivoLido) -> None:
        self._c.novo_arquivo()
        for asset in self._assets(arquivo):
            self._asset(asset)

    @staticmethod
    def _assets(arquivo: ArquivoLido) -> Iterator[dict[str, Any]]:
        if arquivo.itens is not None:
            yield from arquivo.itens
        elif arquivo.objeto is not None:
            yield arquivo.objeto

    def _asset(self, asset: dict[str, Any]) -> None:
        chave = texto(asset.get("name"))
        tipo_nativo = texto(asset.get("asset_type", asset.get("assetType")))
        if not chave or not tipo_nativo:
            self._c.contar("item_sem_identidade")
            return
        recurso = dicionario(asset.get("resource"))
        data = dicionario(recurso.get("data"))
        capturado = instante(asset.get("update_time", asset.get("updateTime")))
        if tipo_nativo == _FIREWALL:
            self._firewalls.append(data)
            self._c.contar("firewall")
            return
        tipo = TIPOS.get(tipo_nativo, _T.OUTRO)
        if tipo is _T.IP_PUBLICO and str(data.get("addressType", "EXTERNAL")).upper() != "EXTERNAL":
            tipo = _T.OUTRO
        if tipo is _T.OUTRO:
            self._c.contar(f"tipo_nao_modelado:{tipo_nativo}")

        m = _PROJETO.search(chave)
        projeto = m.group(1) if m else None
        local = texto(recurso.get("location"))
        zona = local if local and _ZONA.match(local) else None
        regiao = zona.rsplit("-", 1)[0] if zona else (None if local == "global" else local)

        atributos, estado, extras = self._especifico(tipo_nativo, chave, data, projeto)
        tags = tags_de_objeto(data.get("labels"))
        atributos = {**atributos, **_marcas(tipo_nativo, data)}
        nome = texto(data.get("name")) or _ultimo(chave)
        if tipo_nativo == _IMAGEM_DOCKER and nome:
            # `…/dockerImages/api@sha256:…`: cada versão é um asset; o nome é o da imagem
            nome = _ultimo(nome).split("@", 1)[0]
        # Disco de volume e balanceador de Service do GKE levam o rótulo do cluster
        cluster = tags.get("goog-k8s-cluster-name")
        if cluster and projeto and tipo in {_T.DISCO, _T.BALANCEADOR}:
            self._c.relacionar(
                chave, Referencia(_CLUSTER, f"{projeto}/{cluster}", por_nome=True), _R.MEMBRO_DE
            )
        self._c.adicionar(
            Recurso(
                chave=chave,
                tipo=tipo,
                tipo_nativo=tipo_nativo,
                nome=nome,
                conta=projeto,
                regiao=regiao,
                zona=zona,
                estado=estado,
                atributos=sem_vazios(atributos),
                tags=tags,
                apelidos=apelidos(chave, data.get("selfLink"), *extras),
                capturado_em=capturado,
            )
        )
        if tipo_nativo == _CLUSTER and projeto and data.get("name"):
            self._clusters[(projeto, str(data["name"]))] = chave

    # ── por tipo ────────────────────────────────────────────────────────

    def _especifico(  # noqa: PLR0911, PLR0912, PLR0915 - uma seção por tipo
        self, tipo_nativo: str, chave: str, data: dict[str, Any], projeto: str | None
    ) -> tuple[dict[str, Any], EstadoDoRecurso, list[object]]:
        status = str(data.get("status") or data.get("state") or "").upper()
        estado = (
            EstadoDoRecurso.ATIVO
            if status in _EXECUTANDO
            else EstadoDoRecurso.PARADO
            if status in _PARADO
            else EstadoDoRecurso.DESCONHECIDO
        )
        liga = self._c.relacionar

        if tipo_nativo == "compute.googleapis.com/Instance":
            vm = _Vm(chave)
            ip_privado = ip_publico = None
            for bruto in lista(data.get("networkInterfaces")):
                nic = dicionario(bruto)
                rede = nome_do_asset(nic.get("network"))
                subrede = nome_do_asset(nic.get("subnetwork"))
                if subrede:
                    liga(chave, subrede, _R.CONTIDO_EM)
                elif rede:
                    liga(chave, rede, _R.CONTIDO_EM)
                if rede:
                    vm.redes.add(rede)
                ip_privado = ip_privado or texto(nic.get("networkIP"))
                for acesso in lista(nic.get("accessConfigs")):
                    ip_publico = ip_publico or texto(dicionario(acesso).get("natIP"))
            for bruto in lista(data.get("disks")):
                d = dicionario(bruto)
                disco = nome_do_asset(d.get("source"))
                if disco:
                    liga(disco, chave, _R.ANEXADO_A)
                    self._c.anotar(disco, {"boot": bool(d.get("boot"))})
            vm.tags = {str(t) for t in lista(caminho(data, "tags", "items"))}
            vm.contas_servico = {
                str(dicionario(s).get("email"))
                for s in lista(data.get("serviceAccounts"))
                if dicionario(s).get("email")
            }
            self._vms.append(vm)
            cluster = tags_de_objeto(data.get("labels")).get("goog-k8s-cluster-name")
            if cluster and projeto:
                liga(
                    chave, Referencia(_CLUSTER, f"{projeto}/{cluster}", por_nome=True), _R.MEMBRO_DE
                )
            numerico = self._forma_numerica(chave, data.get("id"))
            return (
                {
                    "tipo_maquina": _ultimo(data.get("machineType")),
                    "ip_privado": ip_privado,
                    "ip_publico": ip_publico,
                },
                estado,
                [numerico],
            )

        if tipo_nativo in {"compute.googleapis.com/Disk", "compute.googleapis.com/RegionDisk"}:
            usuarios = [nome_do_asset(u) for u in lista(data.get("users"))]
            for u in usuarios:
                if u:
                    liga(chave, u, _R.ANEXADO_A)
            tipo_disco = _ultimo(data.get("type"))
            return (
                {
                    "tamanho_gb": inteiro(data.get("sizeGb")),
                    "tipo_disco": tipo_disco,
                    "categoria_disco": ("hdd" if tipo_disco == "pd-standard" else "ssd")
                    if tipo_disco
                    else None,
                },
                EstadoDoRecurso.ATIVO if any(usuarios) else EstadoDoRecurso.DISPONIVEL,
                [self._forma_numerica(chave, data.get("id"))],
            )

        if tipo_nativo == "compute.googleapis.com/Snapshot":
            disco = nome_do_asset(data.get("sourceDisk"))
            if disco:
                liga(chave, disco, _R.SNAPSHOT_DE)
            return {"tamanho_gb": inteiro(data.get("diskSizeGb"))}, EstadoDoRecurso.ATIVO, []

        if tipo_nativo == "compute.googleapis.com/Network":
            return (
                {
                    "cidr": data.get("IPv4Range"),
                    "subredes_automaticas": booleano(data.get("autoCreateSubnetworks")),
                },
                EstadoDoRecurso.ATIVO,
                [],
            )

        if tipo_nativo == "compute.googleapis.com/Subnetwork":
            rede = nome_do_asset(data.get("network"))
            if rede:
                liga(chave, rede, _R.CONTIDO_EM)
            return {"cidr": data.get("ipCidrRange")}, EstadoDoRecurso.ATIVO, []

        if tipo_nativo in {
            "compute.googleapis.com/Address",
            "compute.googleapis.com/GlobalAddress",
        }:
            usuarios = [nome_do_asset(u) for u in lista(data.get("users"))]
            for u in usuarios:
                if u:
                    liga(chave, u, _R.ANEXADO_A)
            em_uso = str(data.get("status") or "").upper() == "IN_USE" or any(usuarios)
            return (
                {"ip_publico": data.get("address")},
                EstadoDoRecurso.ATIVO if em_uso else EstadoDoRecurso.DISPONIVEL,
                [data.get("address")],
            )

        if tipo_nativo in {
            "compute.googleapis.com/ForwardingRule",
            "compute.googleapis.com/GlobalForwardingRule",
        }:
            esquema = str(data.get("loadBalancingScheme") or "EXTERNAL").upper()
            subrede = nome_do_asset(data.get("subnetwork"))
            rede = nome_do_asset(data.get("network"))
            if subrede or rede:
                liga(chave, subrede or rede or "", _R.CONTIDO_EM)
            return (
                {
                    "esquema": esquema.lower(),
                    "publico": esquema.startswith("EXTERNAL"),
                    "ip_publico": data.get("IPAddress") if esquema.startswith("EXTERNAL") else None,
                },
                EstadoDoRecurso.ATIVO,
                [],
            )

        if tipo_nativo in {
            "compute.googleapis.com/Router",
            "compute.googleapis.com/VpnGateway",
            "compute.googleapis.com/TargetVpnGateway",
        }:
            rede = nome_do_asset(data.get("network"))
            if rede:
                liga(chave, rede, _R.CONTIDO_EM)
            return {}, EstadoDoRecurso.ATIVO, []

        if tipo_nativo == "sqladmin.googleapis.com/Instance":
            motor, versao = _versao_cloud_sql(texto(data.get("databaseVersion")))
            ip = dicionario(caminho(data, "settings", "ipConfiguration"))
            rede = nome_do_asset(ip.get("privateNetwork"))
            if rede:
                liga(chave, rede, _R.CONTIDO_EM)
            autorizadas = sorted(
                str(dicionario(n).get("value"))
                for n in lista(ip.get("authorizedNetworks"))
                if dicionario(n).get("value")
            )
            ativacao = str(caminho(data, "settings", "activationPolicy") or "").upper()
            nome = texto(data.get("name"))
            return (
                {
                    "classe": caminho(data, "settings", "tier"),
                    "motor": motor,
                    "versao": versao,
                    "alta_disponibilidade": str(
                        caminho(data, "settings", "availabilityType") or ""
                    ).upper()
                    == "REGIONAL",
                    "publico": booleano(ip.get("ipv4Enabled")),
                    "redes_autorizadas": autorizadas,
                    "armazenamento_gb": inteiro(caminho(data, "settings", "dataDiskSizeGb")),
                },
                EstadoDoRecurso.PARADO if ativacao == "NEVER" else estado,
                [
                    f"//cloudsql.googleapis.com/projects/{projeto}/instances/{nome}"
                    if projeto and nome
                    else None,
                    data.get("connectionName"),
                ],
            )

        if tipo_nativo == "redis.googleapis.com/Instance":
            rede = nome_do_asset(data.get("authorizedNetwork"))
            if rede:
                liga(chave, rede, _R.CONTIDO_EM)
            return (
                {
                    "classe": data.get("tier"),
                    "motor": "redis",
                    "versao": data.get("redisVersion"),
                    "memoria_gib": inteiro(data.get("memorySizeGb")),
                    "alta_disponibilidade": str(data.get("tier") or "").upper() == "STANDARD_HA",
                    "ip_privado": data.get("host"),
                },
                estado,
                [],
            )

        if tipo_nativo == "storage.googleapis.com/Bucket":
            nome = texto(data.get("name"))
            return (
                {"classe_armazenamento": data.get("storageClass")},
                EstadoDoRecurso.ATIVO,
                [nome, f"//storage.googleapis.com/{nome}" if nome else None],
            )

        if tipo_nativo == _CLUSTER:
            config_rede = dicionario(data.get("networkConfig"))
            local = str(data.get("location") or "")
            regiao_cluster = local.rsplit("-", 1)[0] if _ZONA.match(local) else local
            subrede = nome_do_asset(config_rede.get("subnetwork")) or (
                nome_do_asset(
                    f"projects/{projeto}/regions/{regiao_cluster}/subnetworks/{data['subnetwork']}"
                )
                if data.get("subnetwork")
                else None
            )
            rede = nome_do_asset(config_rede.get("network")) or (
                nome_do_asset(f"projects/{projeto}/global/networks/{data['network']}")
                if data.get("network")
                else None
            )
            if subrede or rede:
                liga(chave, subrede or rede or "", _R.CONTIDO_EM)
            return {"versao": data.get("currentMasterVersion")}, estado, []

        return {}, estado, []

    @staticmethod
    def _forma_numerica(chave: str, ident: object) -> str | None:
        """``.../instances/vm-1`` → ``.../instances/<id numérico>``: é assim que
        o billing export cita a VM em ``resource.global_name``."""
        numero = texto(ident)
        if not numero or not numero.isdigit():
            return None
        return chave.rsplit("/", 1)[0] + "/" + numero

    # ── firewall → grupos sintéticos ────────────────────────────────────

    def _grupo(self, rede: str, seletor: str, valor: str | None) -> str:
        chave = f"gcp:{rede}:{seletor}" + (f":{valor}" if valor else "")
        if chave not in self._grupos:
            self._grupos[chave] = (rede, seletor, valor)
            m = _PROJETO.search(rede)
            nome = {"tag": f"tag {valor}", "sa": f"service account {valor}"}.get(
                seletor, "toda a rede"
            )
            self._c.adicionar(
                Recurso(
                    chave=chave,
                    tipo=_T.GRUPO_SEGURANCA,
                    tipo_nativo=_TIPO_GRUPO,
                    nome=nome,
                    conta=m.group(1) if m else None,
                    estado=EstadoDoRecurso.ATIVO,
                    atributos=sem_vazios(
                        {"rede": rede, "seletor": seletor, "amplo": seletor == "todas" or None}
                    ),
                )
            )
            self._c.relacionar(chave, rede, _R.CONTIDO_EM)
        return chave

    def _firewall(self, fw: dict[str, Any]) -> None:
        rede = nome_do_asset(fw.get("network"))
        if not rede:
            self._c.contar("firewall_sem_rede")
            return
        if booleano(fw.get("disabled")):
            self._c.contar("regra_desabilitada")
            return
        direcao = (
            Direcao.SAIDA
            if str(fw.get("direction") or "INGRESS").upper() == "EGRESS"
            else Direcao.ENTRADA
        )
        alvos = [self._grupo(rede, "tag", str(t)) for t in lista(fw.get("targetTags"))]
        alvos += [self._grupo(rede, "sa", str(s)) for s in lista(fw.get("targetServiceAccounts"))]
        if not alvos:
            alvos = [self._grupo(rede, "todas", None)]

        if direcao is Direcao.ENTRADA:
            remotos = [seletor_de_endereco(c) for c in lista(fw.get("sourceRanges"))]
            remotos += [
                Seletor(TipoDeSeletor.GRUPO, self._grupo(rede, "tag", str(t)))
                for t in lista(fw.get("sourceTags"))
            ]
            remotos += [
                Seletor(TipoDeSeletor.GRUPO, self._grupo(rede, "sa", str(s)))
                for s in lista(fw.get("sourceServiceAccounts"))
            ]
        else:
            remotos = [seletor_de_endereco(c) for c in lista(fw.get("destinationRanges"))]
        if not remotos:
            remotos = [Seletor(TipoDeSeletor.QUALQUER)]

        entradas = [(Acao.PERMITIR, dicionario(a)) for a in lista(fw.get("allowed"))]
        entradas += [(Acao.NEGAR, dicionario(d)) for d in lista(fw.get("denied"))]
        prioridade = inteiro(fw.get("priority"))
        for acao, entrada in entradas:
            protocolo = protocolo_canonico(entrada.get("IPProtocol"))
            faixas = [faixa_de_portas(p) for p in lista(entrada.get("ports"))] or [(None, None)]
            for grupo in alvos:
                for remoto in remotos:
                    for inicio, fim in faixas:
                        self._c.regra(
                            RegraDeRede(
                                grupo=grupo,
                                direcao=direcao,
                                acao=acao,
                                protocolo=protocolo,
                                origem=remoto,
                                porta_inicio=inicio if protocolo in {"tcp", "udp"} else None,
                                porta_fim=fim if protocolo in {"tcp", "udp"} else None,
                                prioridade=prioridade,
                                descricao=texto(fw.get("name")),
                            )
                        )

    def _membros(self) -> None:
        """VM da rede com a tag (ou service account) do grupo é membro dele."""
        por_rede: dict[str, list[_Vm]] = defaultdict(list)
        for vm in self._vms:
            for rede in vm.redes:
                por_rede[rede].append(vm)
        for grupo, (rede, seletor, valor) in sorted(self._grupos.items()):
            for vm in por_rede.get(rede, []):
                if (
                    seletor == "todas"
                    or (seletor == "tag" and valor in vm.tags)
                    or (seletor == "sa" and valor in vm.contas_servico)
                ):
                    self._c.relacionar(vm.chave, grupo, _R.PROTEGIDO_POR)

    def _resolver(self, ref: Referencia) -> str | None:
        if ref.tipo_nativo == _CLUSTER:
            projeto, _, nome = ref.ident.partition("/")
            return self._clusters.get((projeto, nome))
        return None

    def finalizar(self) -> Inventario:
        for fw in self._firewalls:
            self._firewall(fw)
        self._membros()
        return self._c.finalizar(self._resolver)


def _versao_cloud_sql(bruto: str | None) -> tuple[str | None, str | None]:
    """``POSTGRES_15`` → (postgres, 15); ``MYSQL_8_0`` → (mysql, 8.0);
    ``SQLSERVER_2019_STANDARD`` → (sqlserver, 2019)."""
    if not bruto:
        return None, None
    partes = bruto.upper().split("_")
    motor = {"POSTGRES": "postgres", "MYSQL": "mysql", "SQLSERVER": "sqlserver"}.get(partes[0])
    if motor is None:
        return bruto.lower(), None
    numeros = [p for p in partes[1:] if p.isdigit()]
    if motor == "sqlserver":
        return motor, numeros[0] if numeros else None
    return motor, ".".join(numeros) or None


# Serviço gerenciado que vai com a aplicação pelo rótulo (PLANO_CENARIOS_DE_ENTRADA §7)
_IMAGEM_DOCKER = "artifactregistry.googleapis.com/DockerImage"
_COMPONENTE = {
    "cloudfunctions.googleapis.com/CloudFunction": "funcao",
    "cloudfunctions.googleapis.com/Function": "funcao",
    "run.googleapis.com/Service": "funcao",
    _IMAGEM_DOCKER: "registro_de_imagens",
    "artifactregistry.googleapis.com/Repository": "registro_de_imagens",
    "secretmanager.googleapis.com/Secret": "segredo",
    "pubsub.googleapis.com/Topic": "fila",
}


def _marcas(tipo_nativo: str, data: dict[str, Any]) -> dict[str, str]:
    """Componente e as marcas que o GKE grava na descrição (JSON) do que ele cria: o
    Service no balanceador e o namespace do volume no disco."""
    marcas: dict[str, str] = {}
    if componente := _COMPONENTE.get(tipo_nativo):
        marcas["componente"] = componente
    descricao = data.get("description")
    if isinstance(descricao, str) and descricao.lstrip().startswith("{"):
        try:
            k8s = json.loads(descricao)
        except json.JSONDecodeError:
            k8s = {}
        if isinstance(k8s, dict):
            if servico := k8s.get("kubernetes.io/service-name"):
                marcas["servico_kubernetes"] = str(servico)
            if namespace := k8s.get("kubernetes.io/created-for/pvc/namespace"):
                marcas["namespace_kubernetes"] = str(namespace)
    return marcas
