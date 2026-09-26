#!/usr/bin/env python3
"""Mocks de inventário coerentes com os mocks de billing (I3 do PLANO_INVENTARIO_API).

Lê os **arquivos** de billing já gerados por ``gerar_mocks_billing.py`` e monta
o inventário das mesmas contas, com os mesmos IDs. Ler o arquivo, e não chamar o
gerador de billing por dentro, mantém os dois desacoplados: o billing mock não
muda por causa do inventário, e a coerência é conferida pelo que o parser de
billing de fato grava.

Cada nuvem sai no formato oficial dela:

- AWS: um snapshot do AWS Config por conta e região (`configurationItems`).
- GCP: export do Cloud Asset Inventory em NDJSON, com as chaves do proto.
- Azure: duas páginas da saída do `az graph query`.
- OCI: o formato canônico `oneshift-inventory-v1`.

Por cima dos recursos do billing, a topologia de propósito: grupos de segurança
por camada, dependência entre aplicações, um banco compartilhado, um
balanceador compartilhado, um bastion, um monitoramento que acessa todos os
bancos, SSH e banco abertos para a internet, disco sem anexo e máquina parada.
É o que exercita cada regra da topologia (RI5 a RI12). E, de propósito, uma
conta inteira da AWS fica fora do snapshot, para exercitar a cobertura (RI13).

Determinístico: mesma entrada, mesmos bytes.

Uso:
    python scripts/gerar_mocks_billing.py --destino /tmp/mocks
    python scripts/gerar_mocks_inventario.py --billing /tmp/mocks --destino /tmp/mocks/inventario
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import ipaddress
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

_ZERO = Decimal("0")

# Serviço que chama serviço (quem chama, quem é chamado): vira regra de grupo
# liberando a porta da aplicação. Dá as ondas: `auth` antes de `pagamentos`,
# que vem antes de `checkout`.
CHAMADAS = (
    ("checkout", "pagamentos"),
    ("checkout", "catalogo"),
    ("busca", "catalogo"),
    ("relatorios", "ingestao"),
    ("pagamentos", "auth"),
)
# O banco do `auth` é acessado também por estas: banco compartilhado.
CONSUMIDORES_DO_BANCO_AUTH = ("checkout", "pagamentos")
CRITICIDADE = {"pagamentos": "high", "checkout": "critical", "relatorios": "low"}
_ESCRITORIO = "203.0.113.0/24"


def _hash(*partes: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()


def _numero(*partes: object, modulo: int) -> int:
    return int(_hash(*partes)[:12], 16) % modulo


@dataclass
class ResumoInventario:
    provedor: str
    arquivos: list[Path]
    recursos: int
    # IDs do billing que precisam aparecer no inventário.
    ids_billing_incluidos: frozenset[str]
    # IDs do billing que ficam de fora de propósito (conta fora, snapshot).
    ids_billing_fora: frozenset[str]
    riscos_esperados: frozenset[str] = frozenset()
    extras: dict[str, Any] = field(default_factory=dict)


def _gravar_json(caminho: Path, conteudo: object, *, gz: bool = False) -> None:
    texto = json.dumps(conteudo, ensure_ascii=False, sort_keys=True, indent=1)
    if gz:
        # mtime fixo: o cabeçalho do gzip carrega a data e quebraria o determinismo.
        with caminho.open("wb") as bruto, gzip.GzipFile(fileobj=bruto, mode="wb", mtime=0) as g:
            g.write(texto.encode("utf-8"))
    else:
        caminho.write_text(texto, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
# AWS — AWS Config
# ════════════════════════════════════════════════════════════════════════════

_CAPTURA_AWS = "2026-03-31T12:00:00.000Z"
# De cada 12 volumes, um fica sem anexo e um vai para máquina parada.
_CICLO_DE_VOLUMES = 12
_VOLUME_SOLTO = 11
_VOLUME_DE_PARADA = 10
_VOLUMES_PARA_PARADAS = 10
CONTAS_FORA_DO_SNAPSHOT = ("333333333333",)
_MOTORES = {
    # motor do produto do CUR → (engine do RDS, versões possíveis, porta)
    "PostgreSQL": ("postgres", ("14.10", "15.4", "16.1"), 5432),
    "MySQL": ("mysql", ("8.0.35",), 3306),
    "Aurora PostgreSQL": ("aurora-postgresql", ("15.4", "14.9"), 5432),
    "Aurora MySQL": ("aurora-mysql", ("8.0.mysql_aurora.3.05.2",), 3306),
}
_PORTA_CACHE = 6379
_ARMAZENAMENTO_POR_PRODUTO = {
    "AmazonS3": ("AWS::S3::Bucket", "arn:aws:s3:::{id}"),
    "AmazonECR": ("AWS::ECR::Repository", "arn:aws:ecr:{regiao}:{conta}:repository/{id}"),
    "Amazon Elastic Container Registry Public": (
        "AWS::ECR::PublicRepository",
        "arn:aws:ecr-public::{conta}:repository/{id}",
    ),
    "AmazonCloudWatch": ("AWS::Logs::LogGroup", "arn:aws:logs:{regiao}:{conta}:log-group:{id}"),
    "AmazonEFS": (
        "AWS::EFS::FileSystem",
        "arn:aws:elasticfilesystem:{regiao}:{conta}:file-system/{id}",
    ),
}


@dataclass
class _RecursoCur:
    id: str
    conta: str
    regiao: str
    zona: str
    tags: dict[str, str]
    produtos: set[str] = field(default_factory=set)
    usos: set[str] = field(default_factory=set)
    tipo: str = ""
    produto: dict[str, str] = field(default_factory=dict)
    gb_mes: Decimal = _ZERO


def _ler_cur(caminho: Path) -> dict[str, _RecursoCur]:
    recursos: dict[str, _RecursoCur] = {}
    with gzip.open(caminho, "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rid = r["line_item_resource_id"]
            if not rid:
                continue
            rec = recursos.get(rid)
            if rec is None:
                brutas = json.loads(r["resource_tags"] or "{}")
                rec = recursos[rid] = _RecursoCur(
                    id=rid,
                    conta=r["line_item_usage_account_id"],
                    regiao=r["product_region_code"],
                    zona=r["line_item_availability_zone"],
                    # O CUR prefixa tag de usuário com `user_`; o Config não.
                    tags={k.removeprefix("user_"): v for k, v in brutas.items()},
                )
            rec.produtos.add(r["line_item_product_code"])
            rec.usos.add(r["line_item_usage_type"])
            rec.tipo = rec.tipo or r["product_instance_type"]
            if not rec.produto:
                rec.produto = json.loads(r["product"] or "{}")
            if "VolumeUsage" in r["line_item_usage_type"]:
                rec.gb_mes += Decimal(r["line_item_usage_amount"] or "0")
    return recursos


def _ip_publico(ident: str) -> str:
    return f"54.{_numero('ip', ident, modulo=200)}.{_numero('ip2', ident, modulo=250)}.10"


class _AmbienteAws:
    """Uma conta numa região: VPC, subredes, grupos e os itens do Config."""

    def __init__(self, conta: str, regiao: str, indice: int, ambiente: str) -> None:
        self.conta = conta
        self.regiao = regiao
        self.ambiente = ambiente
        self.itens: list[dict[str, Any]] = []
        self.rede = ipaddress.ip_network(f"10.{indice}.0.0/16")
        self.vpc = f"vpc-{_hash('vpc', conta, regiao)[:17]}"
        # Três camadas por zona: pública (balanceador, NAT), app, dados.
        blocos = list(self.rede.subnets(new_prefix=20))
        self.subredes: dict[tuple[str, str], tuple[str, ipaddress.IPv4Network]] = {}
        for i, camada in enumerate(("publica", "app", "dados")):
            for j, letra in enumerate("abc"):
                ident = f"subnet-{_hash('sub', conta, regiao, camada, letra)[:17]}"
                self.subredes[(camada, letra)] = (ident, blocos[i * 4 + j])  # type: ignore[assignment]
        self.proximo_ip: dict[str, int] = defaultdict(lambda: 10)
        self.grupos: dict[str, dict[str, Any]] = {}

    def arn(self, servico: str, recurso: str) -> str:
        return f"arn:aws:{servico}:{self.regiao}:{self.conta}:{recurso}"

    def ip(self, camada: str, letra: str) -> str:
        ident, bloco = self.subredes[(camada, letra)]
        n = self.proximo_ip[ident]
        self.proximo_ip[ident] += 1
        return str(bloco[n])

    def item(
        self,
        tipo: str,
        rid: str,
        config: dict[str, Any],
        *,
        arn: str | None = None,
        nome: str | None = None,
        zona: str | None = None,
        tags: dict[str, str] | None = None,
        relacoes: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
    ) -> None:
        self.itens.append(
            {
                "configurationItemVersion": "1.3",
                "configurationItemCaptureTime": _CAPTURA_AWS,
                "configurationItemStatus": "OK",
                "configurationStateId": _numero("estado", rid, modulo=10**12),
                "accountId": self.conta,
                "awsRegion": self.regiao,
                "availabilityZone": zona or "Not Applicable",
                "resourceType": tipo,
                "resourceId": rid,
                "resourceName": nome,
                "arn": arn,
                "tags": tags or {},
                "relationships": [
                    {"resourceType": t, "resourceId": i, "name": n} for t, i, n in relacoes
                ],
                "configuration": config,
            }
        )

    def grupo(self, nome: str) -> dict[str, Any]:
        if nome not in self.grupos:
            ident = f"sg-{_hash('sg', self.conta, self.regiao, nome)[:17]}"
            self.grupos[nome] = {
                "groupId": ident,
                "groupName": nome,
                "vpcId": self.vpc,
                "ipPermissions": [],
                "ipPermissionsEgress": [
                    {"ipProtocol": "-1", "ipv4Ranges": [{"cidrIp": "0.0.0.0/0"}]}
                ],
            }
        return self.grupos[nome]

    def libera(
        self,
        destino: str,
        porta: int | None,
        *,
        de_grupo: str | None = None,
        de_cidr: str | None = None,
    ) -> None:
        """Regra de entrada em `destino`. Só se o grupo existir no ambiente."""
        if destino not in self.grupos:
            return
        if de_grupo is not None and de_grupo not in self.grupos:
            return
        perm: dict[str, Any] = (
            {"ipProtocol": "-1"}
            if porta is None
            else {"ipProtocol": "tcp", "fromPort": porta, "toPort": porta}
        )
        if de_grupo is not None:
            perm["userIdGroupPairs"] = [
                {"groupId": self.grupos[de_grupo]["groupId"], "userId": self.conta}
            ]
        else:
            perm["ipv4Ranges"] = [{"cidrIp": de_cidr}]
        self.grupos[destino]["ipPermissions"].append(perm)


def gerar_inventario_aws(cur: Path, destino: Path) -> ResumoInventario:
    destino.mkdir(parents=True, exist_ok=True)
    recursos = _ler_cur(cur)
    incluidos: set[str] = set()
    fora: set[str] = set()

    por_ambiente: dict[tuple[str, str], list[_RecursoCur]] = defaultdict(list)
    for rec in sorted(recursos.values(), key=lambda r: r.id):
        # Snapshot de EBS não é gravado pelo Config: fica só no billing.
        if rec.conta in CONTAS_FORA_DO_SNAPSHOT or rec.id.startswith("snap-"):
            fora.add(rec.id)
            continue
        por_ambiente[(rec.conta, rec.regiao)].append(rec)
        incluidos.add(rec.id)

    arquivos: list[Path] = []
    total = 0
    for indice, ((conta, regiao), lista_rec) in enumerate(sorted(por_ambiente.items()), start=10):
        ambiente = next((r.tags.get("env") for r in lista_rec if r.tags.get("env")), "prod")
        amb = _AmbienteAws(conta, regiao, indice, ambiente)
        _aws_ambiente(amb, lista_rec)
        sufixo = _hash(conta, regiao)[:8]
        nome = f"{conta}_Config_{regiao}_ConfigSnapshot_20260331T120000Z_{sufixo}.json.gz"
        arquivo = destino / nome
        _gravar_json(
            arquivo,
            {
                "fileVersion": "1.0",
                "configSnapshotId": _hash("snap", conta, regiao)[:36],
                "configurationItems": amb.itens,
            },
            gz=True,
        )
        arquivos.append(arquivo)
        total += len(amb.itens)

    return ResumoInventario(
        provedor="aws",
        arquivos=arquivos,
        recursos=total,
        ids_billing_incluidos=frozenset(incluidos),
        ids_billing_fora=frozenset(fora),
        riscos_esperados=frozenset(
            {
                "exposicao_administrativa",
                "banco_exposto",
                "ferramenta_administrativa",
                "hub_de_dados",
                "balanceador_compartilhado",
                "banco_compartilhado",
                "grupo_interno",
                "origem_externa",
                "regra_ampla",
                "recurso_com_ip_publico",
            }
        ),
    )


def _aws_ambiente(amb: _AmbienteAws, recursos: list[_RecursoCur]) -> None:  # noqa: PLR0912, PLR0915
    conta, regiao = amb.conta, amb.regiao
    instancias = [r for r in recursos if r.id.startswith("i-")]
    bancos = [r for r in recursos if r.id.startswith("arn:aws:rds:")]
    caches = [r for r in recursos if r.id.startswith("arn:aws:elasticache:")]
    balanceadores = [r for r in recursos if r.id.startswith("lb-")]
    apps = sorted({r.tags.get("app", "") for r in recursos if r.tags.get("app")})

    # ── rede ────────────────────────────────────────────────────────────
    amb.item(
        "AWS::EC2::VPC",
        amb.vpc,
        {"vpcId": amb.vpc, "cidrBlock": str(amb.rede), "isDefault": False},
        arn=amb.arn("ec2", f"vpc/{amb.vpc}"),
        tags={"Name": f"vpc-{amb.ambiente}"},
    )
    for (camada, letra), (ident, bloco) in sorted(amb.subredes.items()):
        amb.item(
            "AWS::EC2::Subnet",
            ident,
            {
                "subnetId": ident,
                "vpcId": amb.vpc,
                "cidrBlock": str(bloco),
                "availabilityZone": f"{regiao}{letra}",
                "mapPublicIpOnLaunch": camada == "publica",
            },
            arn=amb.arn("ec2", f"subnet/{ident}"),
            zona=f"{regiao}{letra}",
            tags={"Name": f"{camada}-{letra}"},
            relacoes=[("AWS::EC2::VPC", amb.vpc, "Is contained in Vpc")],
        )
    igw = f"igw-{_hash('igw', conta, regiao)[:17]}"
    amb.item(
        "AWS::EC2::InternetGateway",
        igw,
        {"attachments": [{"vpcId": amb.vpc, "state": "available"}]},
        arn=amb.arn("ec2", f"internet-gateway/{igw}"),
    )

    # ── grupos por camada ───────────────────────────────────────────────
    amb.grupo("default")
    amb.libera("default", None, de_grupo="default")  # auto-referência: `grupo_interno`
    amb.grupo("bastion")
    amb.libera("bastion", 22, de_cidr=_ESCRITORIO)  # `origem_externa`
    amb.grupo("monitoramento")
    apps_com_banco = {r.tags.get("app") for r in (*bancos, *caches)}
    apps_com_lb = {r.tags.get("app") for r in balanceadores}
    for app in apps:
        amb.grupo(f"{app}-app")
        if app in apps_com_lb:
            amb.grupo(f"{app}-lb")
        if app in apps_com_banco:
            amb.grupo(f"{app}-dados")
    for app in apps:
        amb.libera(f"{app}-lb", 443, de_cidr="0.0.0.0/0")
        amb.libera(f"{app}-app", 8080, de_grupo=f"{app}-lb")
        amb.libera(f"{app}-app", 22, de_grupo="bastion")
    for chamador, chamado in CHAMADAS:
        amb.libera(f"{chamado}-app", 8080, de_grupo=f"{chamador}-app")
    # Balanceador do catálogo também atende a busca: `balanceador_compartilhado`.
    amb.libera("busca-app", 8080, de_grupo="catalogo-lb")
    # SSH do relatórios aberto para a internet: `exposicao_administrativa`.
    amb.libera("relatorios-app", 22, de_cidr="0.0.0.0/0")

    portas: dict[str, int] = {}
    for b in bancos:
        motor = _MOTORES.get(b.produto.get("database_engine", ""), ("postgres", ("15.4",), 5432))
        portas[b.tags.get("app", "")] = motor[2]
    for c in caches:
        portas.setdefault(c.tags.get("app", ""), _PORTA_CACHE)
    for app, porta in sorted(portas.items()):
        amb.libera(f"{app}-dados", porta, de_grupo=f"{app}-app")
        amb.libera(f"{app}-dados", porta, de_grupo="monitoramento")  # `hub_de_dados`
    for consumidor in CONSUMIDORES_DO_BANCO_AUTH:  # `banco_compartilhado`
        amb.libera("auth-dados", portas.get("auth", 5432), de_grupo=f"{consumidor}-app")
    amb.libera("ingestao-dados", 5432, de_cidr="0.0.0.0/0")  # `banco_exposto`
    amb.libera("pagamentos-dados", 5432, de_cidr=str(amb.rede))  # `regra_ampla`

    def ids_de_grupo(*nomes: str) -> list[dict[str, str]]:
        return [
            {"groupName": n, "groupId": amb.grupos[n]["groupId"]} for n in nomes if n in amb.grupos
        ]

    # ── instâncias do billing + as só do inventário ─────────────────────
    vistas: list[dict[str, Any]] = []
    for r in instancias:
        letra = (r.zona[-1:] or "a") if r.zona[-1:] in "abc" else "a"
        app = r.tags.get("app", "")
        vistas.append(
            {
                "id": r.id,
                "tipo": r.tipo or "m5.large",
                "letra": letra,
                "tags": dict(r.tags),
                "grupos": [f"{app}-app", "default"],
                "camada": "app",
                "estado": "running",
                "ip_publico": app == "relatorios",  # `recurso_com_ip_publico`
            }
        )
    extras = [
        ("bastion", "t3.micro", "publica", "a", "running", ["bastion"], True),
        ("monitoramento", "m6i.large", "app", "a", "running", ["monitoramento"], False),
    ]
    for app, tipo, camada, letra, estado, grupos, publico in extras:
        vistas.append(
            {
                "id": f"i-{_hash(app, conta, regiao)[:17]}",
                "tipo": tipo,
                "letra": letra,
                "tags": {"app": app, "env": amb.ambiente, "team": "sre"},
                "grupos": grupos,
                "camada": camada,
                "estado": estado,
                "ip_publico": publico,
            }
        )
    volumes = sorted((r for r in recursos if r.id.startswith("vol-")), key=lambda r: r.id)
    if len(volumes) >= _VOLUMES_PARA_PARADAS:  # máquinas paradas que ainda pagam disco
        for letra in "ab":
            app = apps[_numero("parada", conta, letra, modulo=len(apps))] if apps else "sem-app"
            vistas.append(
                {
                    "id": f"i-{_hash('parada', conta, regiao, letra)[:17]}",
                    "tipo": "m5.large",
                    "letra": letra,
                    "tags": {"app": app, "env": amb.ambiente},
                    "grupos": [f"{app}-app", "default"],
                    "camada": "app",
                    "estado": "stopped",
                    "ip_publico": False,
                }
            )
    for vista in vistas:
        app = vista["tags"].get("app", "")
        if app in CRITICIDADE and _numero("crit", vista["id"], modulo=2) == 0:
            vista["tags"]["criticality"] = CRITICIDADE[app]

    # Volume → raiz de uma instância, dado de uma instância, dado de uma parada
    # ou solto (sem anexo). Sempre na mesma zona.
    mapas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    anexos: dict[str, dict[str, Any]] = {}
    por_letra = defaultdict(list)
    for v in vistas:
        por_letra[v["letra"]].append(v)
    sem_raiz = {v["id"] for v in vistas if v["estado"] == "running"}
    for i, vol in enumerate(volumes):
        letra = vol.zona[-1:] if vol.zona[-1:] in "abc" else "a"
        ativas = [v for v in por_letra[letra] if v["estado"] == "running"]
        paradas = [v for v in por_letra[letra] if v["estado"] == "stopped"]
        dono: dict[str, Any] | None = None
        raiz = False
        if i % _CICLO_DE_VOLUMES == _VOLUME_SOLTO:
            dono = None
        elif i % _CICLO_DE_VOLUMES == _VOLUME_DE_PARADA and paradas:
            dono = paradas[(i // 12) % len(paradas)]
        elif any(v["id"] in sem_raiz for v in ativas):
            dono = next(v for v in ativas if v["id"] in sem_raiz)
            sem_raiz.discard(dono["id"])
            raiz = True
        elif ativas:
            dono = ativas[i % len(ativas)]
        if dono is not None:
            dispositivo = (
                "/dev/xvda" if raiz else f"/dev/sd{'fghijklmnop'[len(mapas[dono['id']]) % 11]}"
            )
            mapas[dono["id"]].append(
                {"deviceName": dispositivo, "ebs": {"volumeId": vol.id, "status": "attached"}}
            )
            anexos[vol.id] = {"instanceId": dono["id"], "device": dispositivo, "state": "attached"}

    for v in vistas:
        letra = v["letra"]
        subrede, _ = amb.subredes[(v["camada"], letra)]
        ip = amb.ip(v["camada"], letra)
        tem_raiz = any(m["deviceName"] == "/dev/xvda" for m in mapas.get(v["id"], []))
        config = {
            "instanceId": v["id"],
            "instanceType": v["tipo"],
            "state": {"code": 16 if v["estado"] == "running" else 80, "name": v["estado"]},
            "subnetId": subrede,
            "vpcId": amb.vpc,
            "privateIpAddress": ip,
            "publicIpAddress": _ip_publico(v["id"]) if v["ip_publico"] else None,
            "imageId": "ami-0c02fb55956c7d316",
            "architecture": "arm64" if re.match(r"^[a-z]+\d+g", v["tipo"]) else "x86_64",
            "rootDeviceType": "ebs",
            "rootDeviceName": "/dev/xvda" if tem_raiz else None,
            "blockDeviceMappings": mapas.get(v["id"], []),
            "securityGroups": ids_de_grupo(*v["grupos"]),
            "placement": {"availabilityZone": f"{regiao}{letra}", "tenancy": "default"},
        }
        amb.item(
            "AWS::EC2::Instance",
            v["id"],
            config,
            arn=amb.arn("ec2", f"instance/{v['id']}"),
            zona=f"{regiao}{letra}",
            tags=v["tags"],
            relacoes=[("AWS::EC2::Subnet", subrede, "Is contained in Subnet")]
            + [
                ("AWS::EC2::SecurityGroup", g["groupId"], "Is associated with SecurityGroup")
                for g in config["securityGroups"]
            ],
        )

    for vol in volumes:
        anexo = anexos.get(vol.id)
        tipo_volume = next(
            (u.rsplit(".", 1)[-1] for u in sorted(vol.usos) if "VolumeUsage" in u), "gp3"
        )
        amb.item(
            "AWS::EC2::Volume",
            vol.id,
            {
                "volumeId": vol.id,
                "size": max(1, round(vol.gb_mes)),
                "volumeType": tipo_volume,
                "state": "in-use" if anexo else "available",
                "encrypted": True,
                "availabilityZone": vol.zona,
                "attachments": [anexo] if anexo else [],
                "tags": [{"key": k, "value": v} for k, v in sorted(vol.tags.items())],
            },
            arn=amb.arn("ec2", f"volume/{vol.id}"),
            zona=vol.zona,
            tags=vol.tags,
        )

    # ── bancos, caches, balanceadores ───────────────────────────────────
    clusters: dict[str, list[str]] = defaultdict(list)
    exposto = False
    for b in bancos:
        identificador = b.id.split(":db:", 1)[1]
        motor, versoes, porta = _MOTORES.get(
            b.produto.get("database_engine", ""), ("postgres", ("15.4",), 5432)
        )
        app = b.tags.get("app", "")
        publico = app == "ingestao" and not exposto
        exposto = exposto or publico
        config = {
            "dBInstanceIdentifier": identificador,
            "dBInstanceClass": b.tipo or "db.m6g.large",
            "engine": motor,
            "engineVersion": versoes[_numero("versao", b.id, modulo=len(versoes))],
            "multiAZ": b.produto.get("deployment_option") == "Multi-AZ",
            "allocatedStorage": 100 * (1 + _numero("disco", b.id, modulo=10)),
            "storageType": "gp3",
            "publiclyAccessible": publico,
            "dBInstanceStatus": "available",
            "endpoint": {
                "address": f"{identificador}.abc.{regiao}.rds.amazonaws.com",
                "port": porta,
            },
            "vpcSecurityGroups": [
                {"vpcSecurityGroupId": amb.grupos[f"{app}-dados"]["groupId"], "status": "active"}
            ]
            if f"{app}-dados" in amb.grupos
            else [],
            "dBSubnetGroup": {
                "dBSubnetGroupName": f"dados-{amb.ambiente}",
                "vpcId": amb.vpc,
                "subnets": [{"subnetIdentifier": amb.subredes[("dados", z)][0]} for z in "ab"],
            },
        }
        if motor.startswith("aurora"):
            cluster = f"{app}-{motor}"
            config["dBClusterIdentifier"] = cluster
            clusters[cluster].append(identificador)
        tags = dict(b.tags)
        if app in CRITICIDADE:
            tags["criticality"] = CRITICIDADE[app]
        amb.item(
            "AWS::RDS::DBInstance",
            f"db-{_hash('dbi', b.id)[:26].upper()}",
            config,
            arn=b.id,
            nome=identificador,
            zona=b.zona,
            tags=tags,
        )
    for cluster, membros in sorted(clusters.items()):
        amb.item(
            "AWS::RDS::DBCluster",
            f"cluster-{_hash('cl', conta, regiao, cluster)[:26].upper()}",
            {
                "dBClusterIdentifier": cluster,
                "engine": cluster.split("-", 1)[1],
                "dBClusterMembers": [{"dBInstanceIdentifier": m} for m in membros],
            },
            arn=amb.arn("rds", f"cluster:{cluster}"),
            nome=cluster,
        )
    for c in caches:
        ident = c.id.rsplit(":", 1)[-1]
        app = c.tags.get("app", "")
        amb.item(
            "AWS::ElastiCache::CacheCluster",
            ident,
            {
                "cacheClusterId": ident,
                "cacheNodeType": c.tipo or "cache.m6g.large",
                "engine": "redis",
                "engineVersion": "7.0.7",
                "numCacheNodes": 1,
                "securityGroups": [
                    {"securityGroupId": amb.grupos[f"{app}-dados"]["groupId"], "status": "active"}
                ]
                if f"{app}-dados" in amb.grupos
                else [],
            },
            arn=c.id,
            nome=ident,
            zona=c.zona,
            tags=c.tags,
        )
    for n, lb in enumerate(balanceadores):
        app = lb.tags.get("app", "")
        arn = amb.arn(
            "elasticloadbalancing", f"loadbalancer/app/{app}-{n}/{_hash('lb', lb.id)[:16]}"
        )
        amb.item(
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            arn,
            {
                "loadBalancerArn": arn,
                "loadBalancerName": f"{app}-{n}",
                "type": "application",
                "scheme": "internal" if app == "ingestao" else "internet-facing",
                "vpcId": amb.vpc,
                "dNSName": f"{app}-{n}.{regiao}.elb.amazonaws.com",
                "securityGroups": [amb.grupos[f"{app}-lb"]["groupId"]]
                if f"{app}-lb" in amb.grupos
                else [],
                "availabilityZones": [
                    {"zoneName": f"{regiao}{z}", "subnetId": amb.subredes[("publica", z)][0]}
                    for z in "ab"
                ],
            },
            arn=arn,
            nome=lb.id,
            tags=lb.tags,
        )

    # ── o resto do billing ──────────────────────────────────────────────
    for r in recursos:
        if r.id.startswith("nat-"):
            amb.item(
                "AWS::EC2::NatGateway",
                r.id,
                {
                    "natGatewayId": r.id,
                    "subnetId": amb.subredes[("publica", "a")][0],
                    "vpcId": amb.vpc,
                },
                arn=amb.arn("ec2", f"natgateway/{r.id}"),
                tags=r.tags,
            )
        elif r.id.startswith("vpn-"):
            amb.item(
                "AWS::EC2::VPNConnection",
                r.id,
                {"vpnConnectionId": r.id, "type": "ipsec.1"},
                arn=amb.arn("ec2", f"vpn-connection/{r.id}"),
                tags=r.tags,
            )
        elif r.id.startswith("vpce-"):
            amb.item(
                "AWS::EC2::VPCEndpoint",
                r.id,
                {"vpcEndpointId": r.id, "vpcId": amb.vpc},
                arn=amb.arn("ec2", f"vpc-endpoint/{r.id}"),
                tags=r.tags,
            )
        elif r.id.startswith("eks-"):
            amb.item(
                "AWS::EKS::Cluster",
                r.id,
                {
                    "name": r.id,
                    "version": "1.29",
                    "resourcesVpcConfig": {
                        "subnetIds": [amb.subredes[("app", z)][0] for z in "ab"],
                        "securityGroupIds": [
                            g["groupId"] for g in ids_de_grupo(f"{r.tags.get('app', '')}-app")
                        ],
                    },
                },
                arn=amb.arn("eks", f"cluster/{r.id}"),
                nome=r.id,
                tags=r.tags,
            )
        elif r.id.startswith("kafka-"):
            amb.item(
                "AWS::MSK::Cluster",
                r.id,
                {"clusterName": r.id},
                arn=amb.arn("kafka", f"cluster/{r.id}/{_hash('msk', r.id)[:8]}"),
                nome=r.id,
                tags=r.tags,
            )
        else:
            for produto in sorted(r.produtos):
                if produto in _ARMAZENAMENTO_POR_PRODUTO:
                    tipo, modelo = _ARMAZENAMENTO_POR_PRODUTO[produto]
                    amb.item(
                        tipo,
                        r.id,
                        {"name": r.id},
                        arn=modelo.format(id=r.id, regiao=regiao, conta=conta),
                        nome=r.id,
                        tags=r.tags,
                    )

    # Grupos por último: as regras já estão completas.
    for nome, config in sorted(amb.grupos.items()):
        amb.item(
            "AWS::EC2::SecurityGroup",
            config["groupId"],
            config,
            arn=amb.arn("ec2", f"security-group/{config['groupId']}"),
            nome=nome,
            relacoes=[("AWS::EC2::VPC", amb.vpc, "Is contained in Vpc")],
        )


# ════════════════════════════════════════════════════════════════════════════
# GCP — Cloud Asset Inventory
# ════════════════════════════════════════════════════════════════════════════

_API = "https://www.googleapis.com/compute/v1"
_CAPTURA_GCP = "2025-12-31T23:00:00Z"
_DOCUMENTO_DE_DESCOBERTA = "https://www.googleapis.com/discovery/v1/apis/compute/v1/rest"
_TIPO_DISCO = (("SSD backed PD", "pd-ssd"), ("Balanced PD", "pd-balanced"))


def _ler_bigquery(caminho: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    vms: dict[str, dict[str, Any]] = {}
    discos: dict[str, dict[str, Any]] = {}
    with gzip.open(caminho, "rt", encoding="utf-8") as fh:
        for linha in fh:
            r = json.loads(linha)
            recurso = r.get("resource") or {}
            global_name = recurso.get("global_name")
            if not global_name:
                continue
            labels = {x["key"]: x["value"] for x in r.get("labels") or []}
            sistema = {x["key"]: x["value"] for x in r.get("system_labels") or []}
            base = {
                "global": global_name,
                "nome": recurso.get("name"),
                "projeto": r["project"]["id"],
                "zona": r["location"]["zone"],
                "regiao": r["location"]["region"],
                "labels": labels,
            }
            if "/instances/" in global_name:
                vm = vms.setdefault(global_name, base)
                if sistema.get("compute.googleapis.com/machine_spec"):
                    vm["tipo"] = sistema["compute.googleapis.com/machine_spec"]
            elif "/disks/" in global_name:
                disco = discos.setdefault(global_name, {**base, "gb_mes": _ZERO, "sku": ""})
                disco["gb_mes"] += Decimal(r["usage"].get("amount_in_pricing_units") or "0")
                disco["sku"] = r["sku"]["description"]
    return vms, discos


def gerar_inventario_gcp(  # noqa: PLR0912, PLR0915 - uma seção por tipo de asset
    bigquery: Path, destino: Path
) -> ResumoInventario:
    destino.parent.mkdir(parents=True, exist_ok=True)
    vms, discos = _ler_bigquery(bigquery)
    assets: list[dict[str, Any]] = []
    projetos = sorted({v["projeto"] for v in (*vms.values(), *discos.values())})
    regioes_do_projeto = {
        p: sorted({v["regiao"] for v in (*vms.values(), *discos.values()) if v["projeto"] == p})
        for p in projetos
    }

    def asset(nome: str, tipo: str, data: dict[str, Any], local: str) -> None:
        # Número do projeto: o ancestral e o `parent` usam número, não id.
        numero = 100000 + projetos.index(nome.split("/projects/", 1)[1].split("/", 1)[0])
        assets.append(
            {
                "name": nome,
                "asset_type": tipo,
                "ancestors": [f"projects/{numero}", "organizations/123456789"],
                "update_time": _CAPTURA_GCP,
                "resource": {
                    "version": "v1",
                    "discovery_document_uri": _DOCUMENTO_DE_DESCOBERTA,
                    "discovery_name": tipo.rsplit("/", 1)[-1],
                    "parent": f"//cloudresourcemanager.googleapis.com/projects/{numero}",
                    "data": data,
                    "location": local,
                },
            }
        )

    discos_por_zona: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for d in sorted(discos.values(), key=lambda d: d["global"]):
        discos_por_zona[(d["projeto"], d["zona"])].append(d)
    usuarios: dict[str, list[str]] = defaultdict(list)
    anexos_vm: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for p_idx, projeto in enumerate(projetos):
        rede_nome = f"vpc-{projeto}"
        rede = f"//compute.googleapis.com/projects/{projeto}/global/networks/{rede_nome}"
        asset(
            rede,
            "compute.googleapis.com/Network",
            {
                "name": rede_nome,
                "autoCreateSubnetworks": False,
                "id": str(_numero("net", projeto, modulo=10**18)),
            },
            "global",
        )
        regioes = regioes_do_projeto[projeto]
        for r_idx, regiao in enumerate(regioes):
            asset(
                f"//compute.googleapis.com/projects/{projeto}/regions/{regiao}/subnetworks/app-{regiao}",
                "compute.googleapis.com/Subnetwork",
                {
                    "name": f"app-{regiao}",
                    "ipCidrRange": f"10.{p_idx + 20}.{r_idx}.0/24",
                    "network": f"{_API}/projects/{projeto}/global/networks/{rede_nome}",
                    "region": f"{_API}/projects/{projeto}/regions/{regiao}",
                },
                regiao,
            )
        apps = sorted(
            {v["labels"].get("app", "") for v in vms.values() if v["projeto"] == projeto} - {""}
        )
        firewalls = [
            (
                "permite-iap-ssh",
                {
                    "sourceRanges": ["35.235.240.0/20"],
                    "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
                },
            ),
        ]
        # As chamadas conhecidas, e — num projeto com várias aplicações e nenhuma
        # chamada conhecida entre elas — uma em cadeia, para haver dependência.
        chamadas = [(a, b) for a, b in CHAMADAS if a in apps and b in apps]
        if not chamadas and len(apps) > 1:
            chamadas = [(apps[i + 1], apps[i]) for i in range(len(apps) - 1)]
        for chamador, chamado in chamadas:
            if chamador in apps and chamado in apps:
                firewalls.append(
                    (
                        f"{chamado}-de-{chamador}",
                        {
                            "sourceTags": [f"app-{chamador}"],
                            "targetTags": [f"app-{chamado}"],
                            "allowed": [{"IPProtocol": "tcp", "ports": ["8080"]}],
                        },
                    )
                )
        if p_idx == 0 and apps:
            firewalls.append(
                (
                    "ssh-aberto",
                    {
                        "sourceRanges": ["0.0.0.0/0"],
                        "targetTags": [f"app-{apps[0]}"],
                        "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
                    },
                )
            )
        for nome, corpo in firewalls:
            asset(
                f"//compute.googleapis.com/projects/{projeto}/global/firewalls/{nome}",
                "compute.googleapis.com/Firewall",
                {
                    "name": nome,
                    "network": f"{_API}/projects/{projeto}/global/networks/{rede_nome}",
                    "direction": "INGRESS",
                    "priority": 1000,
                    **corpo,
                },
                "global",
            )

    for vm in sorted(vms.values(), key=lambda v: v["global"]):
        livres = discos_por_zona[(vm["projeto"], vm["zona"])]
        # Primeiro disco livre na zona vira o boot; metade dos restantes, dados.
        if livres:
            boot = livres.pop(0)
            anexos_vm[vm["global"]].append({"disco": boot, "boot": True})
            if livres and _numero("dados", vm["global"], modulo=2) == 0:
                anexos_vm[vm["global"]].append({"disco": livres.pop(0), "boot": False})

    ip = defaultdict(lambda: 10)
    for vm in sorted(vms.values(), key=lambda v: v["global"]):
        projeto, zona, regiao = vm["projeto"], vm["zona"], vm["regiao"]
        p_idx = projetos.index(projeto)
        nome_asset = (
            f"//compute.googleapis.com/projects/{projeto}/zones/{zona}/instances/{vm['nome']}"
        )
        numero = vm["global"].rsplit("/", 1)[-1]
        app = vm["labels"].get("app", "")
        discos_vm = []
        for a in anexos_vm[vm["global"]]:
            d = a["disco"]
            link = f"{_API}/projects/{projeto}/zones/{zona}/disks/{d['nome']}"
            discos_vm.append(
                {
                    "source": link,
                    "boot": a["boot"],
                    "deviceName": d["nome"],
                    "autoDelete": a["boot"],
                    "mode": "READ_WRITE",
                    "type": "PERSISTENT",
                }
            )
            usuarios[d["global"]].append(
                f"{_API}/projects/{projeto}/zones/{zona}/instances/{vm['nome']}"
            )
        chave_ip = (projeto, regiao)
        ip[chave_ip] += 1
        r_idx = regioes_do_projeto[projeto].index(regiao)
        zona_api = f"{_API}/projects/{projeto}/zones/{zona}"
        regiao_api = f"{_API}/projects/{projeto}/regions/{regiao}"
        asset(
            nome_asset,
            "compute.googleapis.com/Instance",
            {
                "id": numero,
                "name": vm["nome"],
                "status": "RUNNING",
                "machineType": f"{zona_api}/machineTypes/{vm.get('tipo', 'e2-medium')}",
                "zone": zona_api,
                "networkInterfaces": [
                    {
                        "network": f"{_API}/projects/{projeto}/global/networks/vpc-{projeto}",
                        "subnetwork": f"{regiao_api}/subnetworks/app-{regiao}",
                        "networkIP": f"10.{p_idx + 20}.{r_idx}.{ip[chave_ip]}",
                        "name": "nic0",
                    }
                ],
                "disks": discos_vm,
                "tags": {"items": [f"app-{app}"] if app else []},
                "labels": vm["labels"],
                "serviceAccounts": [
                    {
                        "email": f"{projeto}-compute@developer.gserviceaccount.com",
                        "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                    }
                ],
            },
            zona,
        )

    for d in sorted(discos.values(), key=lambda d: d["global"]):
        tipo = next((t for chave, t in _TIPO_DISCO if chave in d["sku"]), "pd-standard")
        asset(
            f"//compute.googleapis.com/projects/{d['projeto']}/zones/{d['zona']}/disks/{d['nome']}",
            "compute.googleapis.com/Disk",
            {
                "id": d["global"].rsplit("/", 1)[-1],
                "name": d["nome"],
                "sizeGb": str(max(10, round(d["gb_mes"] * 10) * 10 // 10)),
                "type": f"{_API}/projects/{d['projeto']}/zones/{d['zona']}/diskTypes/{tipo}",
                "status": "READY",
                "users": usuarios.get(d["global"], []),
                "labels": d["labels"],
            },
            d["zona"],
        )

    # Só no inventário: um Cloud SQL no primeiro projeto (o billing do mock não
    # o atribui a recurso).
    if projetos:
        p = projetos[0]
        asset(
            f"//cloudsql.googleapis.com/projects/{p}/instances/pedidos",
            "sqladmin.googleapis.com/Instance",
            {
                "name": "pedidos",
                "databaseVersion": "POSTGRES_15",
                "state": "RUNNABLE",
                "connectionName": f"{p}:us-central1:pedidos",
                "region": "us-central1",
                "settings": {
                    "tier": "db-custom-4-16384",
                    "availabilityType": "ZONAL",
                    "dataDiskSizeGb": "200",
                    "ipConfiguration": {
                        "ipv4Enabled": False,
                        "privateNetwork": f"projects/{p}/global/networks/vpc-{p}",
                    },
                },
            },
            "us-central1",
        )

    assets.sort(key=lambda a: a["name"])
    texto = "\n".join(json.dumps(a, ensure_ascii=False, sort_keys=True) for a in assets) + "\n"
    with destino.open("wb") as bruto, gzip.GzipFile(fileobj=bruto, mode="wb", mtime=0) as g:
        g.write(texto.encode("utf-8"))
    return ResumoInventario(
        provedor="gcp",
        arquivos=[destino],
        recursos=len(assets),
        ids_billing_incluidos=frozenset(vms) | frozenset(discos),
        ids_billing_fora=frozenset(),
        riscos_esperados=frozenset({"exposicao_administrativa", "regra_ampla"}),
    )


# ════════════════════════════════════════════════════════════════════════════
# Azure — Resource Graph
# ════════════════════════════════════════════════════════════════════════════

_GRAFIA_ARM = {
    "microsoft.compute/virtualmachines": "Microsoft.Compute/virtualMachines",
    "microsoft.compute/disks": "Microsoft.Compute/disks",
    "microsoft.storage/storageaccounts": "Microsoft.Storage/storageAccounts",
    "microsoft.dbforpostgresql/flexibleservers": "Microsoft.DBforPostgreSQL/flexibleServers",
    "microsoft.containerservice/managedclusters": "Microsoft.ContainerService/managedClusters",
}
_CICLO_DE_DISCOS_AZURE = 6
_DISCO_SOLTO_AZURE = 5
_GB_POR_TIER = {
    "P10": 128,
    "P15": 256,
    "P20": 512,
    "P30": 1024,
    "P40": 2048,
    "P50": 4096,
    "S10": 128,
    "S15": 256,
    "S20": 512,
    "S30": 1024,
    "S40": 2048,
    "E10": 128,
    "E15": 256,
    "E20": 512,
    "E30": 1024,
}


def _grafia(ident: str) -> str:
    """O id do billing vem em minúsculas; o Resource Graph devolve com caixa
    mista. O normalizador precisa casar os dois."""
    m = re.match(
        r"^(/subscriptions/[^/]+/)resourcegroups(/[^/]+/providers/)([^/]+/[^/]+)(/.*)$", ident
    )
    if not m:
        return ident
    tipo = _GRAFIA_ARM.get(m.group(3), m.group(3))
    return f"{m.group(1)}resourceGroups{m.group(2)}{tipo}{m.group(4)}"


def _grupo_de_recursos(ident: str) -> str:
    partes = ident.split("/")
    return partes[4] if len(partes) > 4 else ""  # noqa: PLR2004 - posição no id do ARM


def gerar_inventario_azure(  # noqa: PLR0912, PLR0915 - uma seção por tipo
    custos: Path, destino: Path
) -> ResumoInventario:
    destino.mkdir(parents=True, exist_ok=True)
    recursos: dict[str, dict[str, Any]] = {}
    with custos.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rid = r["ResourceId"]
            if not rid or rid in recursos:
                continue
            tags = json.loads("{" + r["Tags"] + "}") if r["Tags"] else {}
            info = json.loads(r["AdditionalInfo"]) if r.get("AdditionalInfo") else {}
            recursos[rid] = {
                "id": rid,
                "categoria": r["MeterCategory"],
                "sub": r["MeterSubCategory"],
                "medidor": r["MeterName"],
                "info": info,
                "tags": tags,
                "local": r["ResourceLocation"].lower().replace(" ", ""),
                "assinatura": r["SubscriptionId"],
                "nome_assinatura": r.get("SubscriptionName", ""),
            }
    linhas: list[dict[str, Any]] = []

    def linha(ident: str, tipo: str, props: dict[str, Any], **extra: Any) -> None:
        linhas.append(
            {
                "id": ident,
                "name": ident.rsplit("/", 1)[-1],
                "type": tipo.lower(),
                "location": extra.pop("location", "eastus"),
                "resourceGroup": _grupo_de_recursos(ident),
                "subscriptionId": ident.split("/")[2],
                "tags": extra.pop("tags", {}),
                "properties": props,
                "sku": extra.pop("sku", None),
                "kind": "",
                "zones": extra.pop("zones", None),
                "managedBy": extra.pop("managedBy", ""),
            }
        )

    # VNet, NSG e ASG são regionais: a NIC só entra em VNet e ASG da região
    # dela. Um ambiente por (assinatura, região).
    por_ambiente: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in sorted(recursos.values(), key=lambda r: r["id"]):
        por_ambiente[(r["assinatura"], r["local"])].append(r)

    for s_idx, ((assinatura, local), itens) in enumerate(sorted(por_ambiente.items())):
        base = f"/subscriptions/{assinatura}/resourceGroups/rg-rede/providers/Microsoft.Network"
        vnet = f"{base}/virtualNetworks/vnet-{local}"
        sub_app, sub_dados, sub_aks = (f"{vnet}/subnets/{n}" for n in ("app", "dados", "aks"))
        nsg_app, nsg_dados = (
            f"{base}/networkSecurityGroups/nsg-app-{local}",
            f"{base}/networkSecurityGroups/nsg-dados-{local}",
        )
        apps = sorted({r["tags"].get("app", "") for r in itens} - {""})
        asgs = {a: f"{base}/applicationSecurityGroups/asg-{a}-{local}" for a in apps}
        tem_banco = any("/flexibleservers/" in r["id"] for r in itens)
        consumidores_do_banco = (
            ([a for a in ("checkout", "pagamentos") if a in asgs] or apps[:2]) if tem_banco else []
        )
        linha(
            vnet,
            "microsoft.network/virtualnetworks",
            {
                "addressSpace": {"addressPrefixes": [f"10.{40 + s_idx}.0.0/16"]},
                "subnets": [
                    {
                        "id": sub_app,
                        "name": "app",
                        "properties": {
                            "addressPrefix": f"10.{40 + s_idx}.1.0/24",
                            "networkSecurityGroup": {"id": nsg_app},
                        },
                    },
                    {
                        "id": sub_dados,
                        "name": "dados",
                        "properties": {
                            "addressPrefix": f"10.{40 + s_idx}.2.0/24",
                            "networkSecurityGroup": {"id": nsg_dados},
                            "delegations": [
                                {
                                    "name": "pg",
                                    "properties": {
                                        "serviceName": "Microsoft.DBforPostgreSQL/flexibleServers"
                                    },
                                }
                            ],
                        },
                    },
                    {
                        "id": sub_aks,
                        "name": "aks",
                        "properties": {"addressPrefix": f"10.{40 + s_idx}.8.0/21"},
                    },
                ],
            },
            location=local,
        )
        regras_app = []
        prioridade = 100
        for chamador, chamado in CHAMADAS:
            if chamador in asgs and chamado in asgs:
                regras_app.append(
                    {
                        "name": f"{chamado}-de-{chamador}",
                        "properties": {
                            "priority": prioridade,
                            "direction": "Inbound",
                            "access": "Allow",
                            "protocol": "Tcp",
                            "sourceApplicationSecurityGroups": [{"id": asgs[chamador]}],
                            "destinationApplicationSecurityGroups": [{"id": asgs[chamado]}],
                            "sourcePortRange": "*",
                            "destinationPortRange": "8080",
                        },
                    }
                )
                prioridade += 10
        if s_idx == 0:
            regras_app.append(
                {
                    "name": "ssh-internet",
                    "properties": {
                        "priority": prioridade,
                        "direction": "Inbound",
                        "access": "Allow",
                        "protocol": "Tcp",
                        "sourceAddressPrefix": "Internet",
                        "sourcePortRange": "*",
                        "destinationAddressPrefix": "*",
                        "destinationPortRange": "22",
                    },
                }
            )
        linha(
            nsg_app,
            "microsoft.network/networksecuritygroups",
            {
                "securityRules": regras_app,
                "subnets": [{"id": sub_app}],
                "defaultSecurityRules": [{"name": "AllowVnetInBound"}, {"name": "DenyAllInBound"}],
            },
            location=local,
        )
        regras_dados = [
            {
                "name": f"pg-de-{a}",
                "properties": {
                    "priority": 100 + 10 * i,
                    "direction": "Inbound",
                    "access": "Allow",
                    "protocol": "Tcp",
                    "sourceApplicationSecurityGroups": [{"id": asgs[a]}],
                    "sourcePortRange": "*",
                    "destinationPortRange": "5432",
                },
            }
            for i, a in enumerate(consumidores_do_banco)
        ]
        linha(
            nsg_dados,
            "microsoft.network/networksecuritygroups",
            {"securityRules": regras_dados, "subnets": [{"id": sub_dados}]},
            location=local,
        )
        for ident in sorted(asgs.values()):
            linha(ident, "microsoft.network/applicationsecuritygroups", {}, location=local)

        vms = [r for r in itens if "/virtualmachines/" in r["id"]]
        discos = [r for r in itens if "/disks/" in r["id"]]
        dados_vm: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for i, d in enumerate(discos):
            if i % _CICLO_DE_DISCOS_AZURE == _DISCO_SOLTO_AZURE or not vms:
                continue  # sem anexo
            dono = vms[i % len(vms)]
            dados_vm[dono["id"]].append(d)
            d["dono"] = dono["id"]
        for n, vm in enumerate(vms):
            ident = _grafia(vm["id"])
            nic = (
                f"/subscriptions/{assinatura}/resourceGroups/{_grupo_de_recursos(vm['id'])}"
                f"/providers/Microsoft.Network/networkInterfaces/{vm['id'].rsplit('/', 1)[-1]}-nic"
            )
            app = vm["tags"].get("app", "")
            linha(
                nic,
                "microsoft.network/networkinterfaces",
                {
                    "ipConfigurations": [
                        {
                            "name": "ipconfig1",
                            "properties": {
                                "primary": True,
                                "privateIPAddress": f"10.{40 + s_idx}.1.{10 + n}",
                                "subnet": {"id": sub_app},
                                "applicationSecurityGroups": [{"id": asgs[app]}]
                                if app in asgs
                                else [],
                            },
                        }
                    ],
                    "virtualMachine": {"id": ident},
                },
                tags=vm["tags"],
                location=local,
            )
            linha(
                ident,
                "microsoft.compute/virtualmachines",
                {
                    "vmId": _hash("vmid", vm["id"])[:36],
                    "hardwareProfile": {"vmSize": vm["info"].get("ServiceType", "Standard_D4s_v5")},
                    "storageProfile": {
                        "dataDisks": [
                            {"lun": i, "managedDisk": {"id": _grafia(d["id"])}}
                            for i, d in enumerate(dados_vm[vm["id"]])
                        ],
                        "imageReference": {
                            "publisher": "Canonical",
                            "offer": "0001-com-ubuntu-server-jammy",
                            "sku": "22_04-lts-gen2",
                        },
                    },
                    "networkProfile": {"networkInterfaces": [{"id": nic}]},
                    "extended": {"instanceView": {"powerState": {"code": "PowerState/running"}}},
                },
                tags=vm["tags"],
                location=vm["local"],
                zones=["1"],
            )
        for d in discos:
            tier = d["medidor"].split(" ", 1)[0]
            sku = (
                "Premium_LRS"
                if "Premium" in d["sub"]
                else ("StandardSSD_LRS" if "Standard SSD" in d["sub"] else "Standard_LRS")
            )
            linha(
                _grafia(d["id"]),
                "microsoft.compute/disks",
                {
                    "diskSizeGB": _GB_POR_TIER.get(tier, 128),
                    "tier": tier,
                    "diskState": "Attached" if d.get("dono") else "Unattached",
                },
                sku={"name": sku},
                managedBy=_grafia(d["dono"]) if d.get("dono") else "",
                tags=d["tags"],
                location=d["local"],
            )
        for r in itens:
            if "/flexibleservers/" in r["id"]:
                linha(
                    _grafia(r["id"]),
                    "microsoft.dbforpostgresql/flexibleservers",
                    {
                        "version": "15" if s_idx == 0 else "14",
                        "state": "Ready",
                        "storage": {"storageSizeGB": 512},
                        "highAvailability": {"mode": "ZoneRedundant" if s_idx == 0 else "Disabled"},
                        "network": {
                            "delegatedSubnetResourceId": sub_dados,
                            "publicNetworkAccess": "Disabled",
                        },
                    },
                    sku={"name": "Standard_D4ds_v5", "tier": "GeneralPurpose"},
                    tags=r["tags"],
                    location=r["local"],
                )
            elif "/managedclusters/" in r["id"]:
                linha(
                    _grafia(r["id"]),
                    "microsoft.containerservice/managedclusters",
                    {
                        "kubernetesVersion": "1.29.2",
                        "agentPoolProfiles": [
                            {
                                "name": "sistema",
                                "vnetSubnetID": sub_aks,
                                "count": 3,
                                "vmSize": "Standard_D4s_v5",
                            }
                        ],
                    },
                    tags=r["tags"],
                    location=r["local"],
                )
            elif "/storageaccounts/" in r["id"]:
                linha(
                    _grafia(r["id"]),
                    "microsoft.storage/storageaccounts",
                    {"allowBlobPublicAccess": False},
                    sku={"name": "Standard_LRS"},
                    tags=r["tags"],
                    location=r["local"],
                )

    linhas.sort(key=lambda x: x["id"].lower())
    metade = (len(linhas) + 1) // 2
    arquivos = []
    for i, pagina in enumerate((linhas[:metade], linhas[metade:]), start=1):
        arquivo = destino / f"azure_resource_graph_pagina{i}.json"
        _gravar_json(
            arquivo,
            {
                "count": len(pagina),
                "data": pagina,
                "skip_token": "dG9rZW4=" if i == 1 else None,
                "total_records": len(linhas),
            },
        )
        arquivos.append(arquivo)
    return ResumoInventario(
        provedor="azure",
        arquivos=arquivos,
        recursos=len(linhas),
        ids_billing_incluidos=frozenset(recursos),
        ids_billing_fora=frozenset(),
        riscos_esperados=frozenset({"exposicao_administrativa"}),
    )


# ════════════════════════════════════════════════════════════════════════════
# OCI — formato canônico
# ════════════════════════════════════════════════════════════════════════════

_CICLO_DE_VOLUMES_OCI = 5
_VOLUME_SOLTO_OCI = 4
_SHAPES = (
    ("Ampere A1", "VM.Standard.A1.Flex"),
    ("E5", "VM.Standard.E5.Flex"),
    ("E4", "VM.Standard.E4.Flex"),
)


def gerar_inventario_oci(  # noqa: PLR0912, PLR0915 - uma seção por serviço
    custos: Path, destino: Path
) -> ResumoInventario:
    destino.parent.mkdir(parents=True, exist_ok=True)
    recursos: dict[str, dict[str, Any]] = {}
    with gzip.open(custos, "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rid = r["product/resourceId"]
            if not rid:
                continue
            rec = recursos.setdefault(
                rid,
                {
                    "id": rid,
                    "servico": r["product/service"],
                    "desc": r["product/Description"],
                    "compartimento": r["product/compartmentName"],
                    "compartimento_id": r["product/compartmentId"],
                    "regiao": r["product/region"],
                    "ad": r.get("product/availabilityDomain", ""),
                    "tenancy": r["lineItem/tenantId"],
                    "app": r.get("tags/app", ""),
                    "env": r.get("tags/env", ""),
                    "ocpu_h": _ZERO,
                    "gb_h": _ZERO,
                    "gb_mes": _ZERO,
                },
            )
            unidade = r["cost/billingUnitReadable"]
            qtd = Decimal(r["usage/billedQuantity"] or "0")
            if unidade == "OCPU Hours":
                rec["ocpu_h"] += qtd
            elif unidade == "GB Hours":
                rec["gb_h"] += qtd
            elif unidade == "GB Month":
                rec["gb_mes"] += qtd

    tenancy = next(iter(recursos.values()))["tenancy"] if recursos else "ocid1.tenancy.oc1..x"
    compartimentos = sorted({r["compartimento"] for r in recursos.values()})
    rec_json: list[dict[str, Any]] = []
    relacoes: list[dict[str, str]] = []
    regiao = "sa-saopaulo-1"

    def recurso(ident: str, tipo: str, **campos: Any) -> None:
        rec_json.append(
            {
                "id": ident,
                "tipo": tipo,
                "conta": tenancy,
                "regiao": regiao,
                "estado": campos.pop("estado", "ativo"),
                **campos,
            }
        )

    def liga(de: str, para: str, tipo: str) -> None:
        relacoes.append({"de": de, "para": para, "tipo": tipo})

    subredes: dict[tuple[str, str], str] = {}
    for c_idx, comp in enumerate(compartimentos):
        vcn = f"ocid1.vcn.oc1.{regiao}.{comp}"
        recurso(
            vcn,
            "rede",
            tipo_nativo="Vcn",
            nome=f"vcn-{comp}",
            atributos={"cidr": f"10.{60 + c_idx}.0.0/16"},
        )
        for s_idx, camada in enumerate(("publica", "app", "dados")):
            ident = f"ocid1.subnet.oc1.{regiao}.{comp}-{camada}"
            subredes[(comp, camada)] = ident
            recurso(
                ident,
                "subrede",
                tipo_nativo="Subnet",
                nome=f"{camada}-{comp}",
                atributos={"cidr": f"10.{60 + c_idx}.{s_idx}.0/24", "publica": camada == "publica"},
            )
            liga(ident, vcn, "contido_em")

    instancias = sorted(
        (r for r in recursos.values() if r["servico"] == "COMPUTE"), key=lambda r: r["id"]
    )
    apps = sorted({r["app"] for r in instancias} - {""})
    nsg_lb = f"ocid1.networksecuritygroup.oc1.{regiao}.lb"
    nsg_db = f"ocid1.networksecuritygroup.oc1.{regiao}.db"
    nsg_app = {a: f"ocid1.networksecuritygroup.oc1.{regiao}.app-{a}" for a in apps}
    recurso(
        nsg_lb,
        "grupo_seguranca",
        tipo_nativo="NetworkSecurityGroup",
        nome="nsg-lb",
        atributos={
            "regras": [
                {
                    "protocolo": "tcp",
                    "portas": [443, 443],
                    "origem": {"tipo": "cidr", "valor": "0.0.0.0/0"},
                }
            ]
        },
    )
    for a, ident in sorted(nsg_app.items()):
        regras = [
            {
                "protocolo": "tcp",
                "portas": [8080, 8080],
                "origem": {"tipo": "grupo", "valor": nsg_lb},
            }
        ]
        regras += [
            {
                "protocolo": "tcp",
                "portas": [8080, 8080],
                "origem": {"tipo": "grupo", "valor": nsg_app[ch]},
            }
            for ch, cd in CHAMADAS
            if cd == a and ch in nsg_app
        ]
        recurso(
            ident,
            "grupo_seguranca",
            tipo_nativo="NetworkSecurityGroup",
            nome=f"nsg-app-{a}",
            atributos={"regras": regras},
        )
    recurso(
        nsg_db,
        "grupo_seguranca",
        tipo_nativo="NetworkSecurityGroup",
        nome="nsg-db",
        atributos={
            "regras": [
                {
                    "protocolo": "tcp",
                    "portas": [1522, 1522],
                    "origem": {"tipo": "grupo", "valor": nsg_app[a]},
                }
                for a in apps
                if a in ("checkout", "pagamentos", "relatorios")
            ]
        },
    )

    for n, inst in enumerate(instancias):
        shape = next((s for chave, s in _SHAPES if chave in inst["desc"]), "VM.Standard.E4.Flex")
        horas = Decimal(744)
        ocpus = max(1, math.ceil(inst["ocpu_h"] / horas)) if inst["ocpu_h"] else 1
        memoria = max(1, math.ceil(inst["gb_h"] / horas)) if inst["gb_h"] else ocpus * 16
        comp = inst["compartimento"]
        recurso(
            inst["id"],
            "instancia",
            tipo_nativo="Instance",
            nome=f"{inst['app'] or 'vm'}-{n}",
            zona=inst["ad"] or "SA-SAOPAULO-1-AD-1",
            atributos={
                "tipo_maquina": shape,
                "ocpus": ocpus,
                "memoria_gib": memoria,
                "vcpus": ocpus if "A1" in shape else ocpus * 2,
                "ip_privado": f"10.{60 + compartimentos.index(comp)}.1.{10 + n}",
            },
            tags={k: v for k, v in (("app", inst["app"]), ("env", inst["env"])) if v},
        )
        liga(inst["id"], subredes[(comp, "app")], "contido_em")
        if inst["app"] in nsg_app:
            liga(inst["id"], nsg_app[inst["app"]], "protegido_por")

    volumes = sorted(
        (r for r in recursos.values() if r["servico"] == "BLOCK_STORAGE"), key=lambda r: r["id"]
    )
    for i, vol in enumerate(volumes):
        solto = i % _CICLO_DE_VOLUMES_OCI == _VOLUME_SOLTO_OCI
        dono = instancias[i % len(instancias)] if instancias and not solto else None
        recurso(
            vol["id"],
            "disco",
            tipo_nativo="Volume",
            nome=f"volume-{i}",
            estado="ativo" if dono else "disponivel",
            atributos={
                "tamanho_gb": max(50, round(vol["gb_mes"])),
                "categoria_disco": "ssd",
                "boot": False,
            },
        )
        if dono:
            liga(vol["id"], dono["id"], "anexado_a")
    for r in sorted(recursos.values(), key=lambda r: r["id"]):
        comp = r["compartimento"]
        if r["servico"] == "OBJECT_STORAGE":
            recurso(r["id"], "bucket", tipo_nativo="Bucket", nome=r["id"].rsplit(".", 1)[-1])
        elif r["servico"] == "DATABASE":
            recurso(
                r["id"],
                "banco",
                tipo_nativo="AutonomousDatabase",
                nome="adb-producao",
                atributos={
                    "motor": "oracle",
                    "versao": "19c",
                    "classe": "ATP-ECPU",
                    "alta_disponibilidade": False,
                    "publico": False,
                    "porta": 1522,
                },
            )
            liga(r["id"], subredes[(comp, "dados")], "contido_em")
            liga(r["id"], nsg_db, "protegido_por")
        elif r["servico"] == "LOAD_BALANCER":
            recurso(
                r["id"],
                "balanceador",
                tipo_nativo="LoadBalancer",
                nome=r["id"].rsplit(".", 1)[-1],
                atributos={"publico": True, "esquema": "publico"},
            )
            liga(r["id"], subredes[(comp, "publica")], "contido_em")
            liga(r["id"], nsg_lb, "protegido_por")
            alvo = apps[int(r["id"][-1]) % len(apps)] if apps else None
            for inst in instancias:
                if inst["app"] == alvo:
                    liga(r["id"], inst["id"], "encaminha_para")

    documento = {
        "formato": "oneshift-inventory-v1",
        "provedor": "oci",
        "capturado_em": "2026-03-31T12:00:00Z",
        "recursos": sorted(rec_json, key=lambda r: r["id"]),
        "relacoes": sorted(relacoes, key=lambda r: (r["de"], r["para"], r["tipo"])),
    }
    _gravar_json(destino, documento)
    return ResumoInventario(
        provedor="oci",
        arquivos=[destino],
        recursos=len(rec_json),
        ids_billing_incluidos=frozenset(recursos),
        ids_billing_fora=frozenset(),
    )


# ── entrada de linha de comando ─────────────────────────────────────────────


def gerar_todos(billing: Path, destino: Path) -> dict[str, ResumoInventario]:
    return {
        "aws": gerar_inventario_aws(
            billing / "mock_aws_cur2_2026-03.csv.gz", destino / "aws_config"
        ),
        "gcp": gerar_inventario_gcp(
            billing / "mock_gcp_bigquery_2025-12.json.gz",
            destino / "gcp_asset_inventory_2025-12-31.json.gz",
        ),
        "azure": gerar_inventario_azure(
            billing / "mock_azure_cost_details_2026-03.csv", destino / "azure_resource_graph"
        ),
        "oci": gerar_inventario_oci(
            billing / "mock_oci_cost_report_2026-03.csv.gz", destino / "oci_inventario.json"
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--billing", type=Path, required=True, help="pasta com os mocks de billing")
    p.add_argument("--destino", type=Path, required=True)
    args = p.parse_args()
    for provedor, resumo in gerar_todos(args.billing, args.destino).items():
        print(  # noqa: T201
            f"{provedor}: {resumo.recursos} itens em {len(resumo.arquivos)} arquivo(s); "
            f"{len(resumo.ids_billing_incluidos)} recursos do billing incluídos, "
            f"{len(resumo.ids_billing_fora)} de fora"
        )


if __name__ == "__main__":
    main()
