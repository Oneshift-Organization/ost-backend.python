"""Adaptador OCI.

Duas particularidades:

- **`compartment_id` em tudo.** É o contêiner lógico da OCI e não sai da
  análise. Vira variável sem default: o cliente tem que informar o OCID, e não
  há palpite razoável.
- **Shape flexível.** A OCI cobra vCPU e memória separadamente em shapes
  `*.Flex`, então o dimensionamento entra em `shape_config` em vez de um nome
  de instância fechado — o que casa melhor com o modelo de pool da análise do
  que nas outras nuvens.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from app.domain.services.oci_shape import FAMILIA_PADRAO, ler_shape, ocpus_para
from app.domain.services.terraform_topologia.bancos import ram_da_classe
from app.infrastructure.terraform.hcl import (
    atributo,
    bloco,
    bloco_aninhado,
    comentario,
    referencia,
)
from app.infrastructure.terraform.renderers.base import (
    ajustar_tamanho,
    com_indice,
    disco_raiz,
    faixa_de_portas,
    fontes,
    instancias_banco,
    nome_banco,
    nome_cloud,
    quantidade_volumes,
    texto_banco,
    texto_espelho,
    texto_grupo,
    texto_tamanho_disco,
    texto_volume_bucket,
    var_versao,
)
from app.schemas.terraform import (
    RecursoTerraform,
    TerraformBlueprint,
    TipoRecurso,
    VariavelTerraform,
)

REDE = "rede_principal"
SUBNET = "subnet_principal"

# Proporção usada quando a análise não trouxe RAM: a OCI recomenda 16 GB por
# OCPU nos shapes flexíveis de propósito geral.
_GB_POR_VCPU = 16
# O nome do shape Flex de cada família; o X9 (Intel) é o Standard3
_SHAPE_DA_FAMILIA = {"X9": "VM.Standard3.Flex"}


class AdaptadorOCI:
    provider_nome = "oci"
    provider_source = "oracle/oci"
    provider_version = "~> 6.0"

    def provider_bloco(self, bp: TerraformBlueprint) -> str:
        # Só a região. Autenticação vem do `~/.oci/config` de quem aplica.
        return bloco("provider", ["oci"], [atributo("region", bp.region)])

    def variaveis_extras(self, bp: TerraformBlueprint) -> list[VariavelTerraform]:
        variaveis = [
            VariavelTerraform(
                name="compartment_id",
                description=(
                    "OCID do compartment onde os recursos serão criados. É "
                    "estrutura da OCI e não sai do billing."
                ),
            ),
            VariavelTerraform(
                name="availability_domain",
                description=(
                    "Nome do availability domain (ex.: 'Uocm:SA-SAOPAULO-1-AD-1'). "
                    "Varia por tenancy — consulte na sua conta."
                ),
            ),
        ]
        if any(r.resource_type is TipoRecurso.COMPUTE
               and not r.configuration.get("disk_only") for r in bp.resources):
            variaveis.append(
                VariavelTerraform(
                    name="image_ocid",
                    description=(
                        "OCID da imagem de boot. Varia por região — o billing não "
                        "informa o sistema operacional em uso."
                    ),
                )
            )
        bancos = [r for r in bp.resources if r.resource_type is TipoRecurso.MANAGED_DATABASE]
        if any(_produto_do_banco(r) != "oracle" for r in bancos):
            variaveis.append(
                VariavelTerraform(
                    name="db_admin_username",
                    description=(
                        "Usuário administrador do banco (PostgreSQL ou MySQL HeatWave). "
                        "Não sai do billing nem do inventário."
                    ),
                )
            )
        if bancos:
            variaveis.append(
                VariavelTerraform(
                    name="db_admin_password",
                    description=(
                        "Senha do administrador do banco gerenciado. A plataforma não "
                        "gera nem armazena senha — escolha uma e guarde no seu cofre."
                    ),
                    sensitive=True,
                )
            )
        if any(r.resource_type is TipoRecurso.OBJECT_STORAGE for r in bp.resources):
            variaveis.append(
                VariavelTerraform(
                    name="object_storage_namespace",
                    description="Namespace do Object Storage da sua tenancy.",
                )
            )
        return variaveis

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        return []

    def outputs(self, bp: TerraformBlueprint) -> list[str]:
        saidas = []
        if any(r.resource_type is TipoRecurso.NETWORK for r in bp.resources):
            saidas.append(bloco("output", ["vcn_id"], [
                atributo("value", referencia(f"oci_core_vcn.{REDE}.id")),
                atributo("description", "OCID da VCN criada."),
            ]))
        for r in bp.resources:
            if r.resource_type is TipoRecurso.OBJECT_STORAGE:
                saidas.append(bloco("output", [f"{r.logical_name}_nome"], [
                    atributo("value", referencia(
                        f"oci_objectstorage_bucket.{r.logical_name}.name"
                    )),
                    atributo("description", "Nome do bucket."),
                ]))
        return saidas

    def recurso(self, r: RecursoTerraform, bp: TerraformBlueprint) -> str | None:
        return {
            TipoRecurso.NETWORK: _rede,
            TipoRecurso.SUBNET: _subnet,
            TipoRecurso.COMPUTE: _compute,
            TipoRecurso.OBJECT_STORAGE: _bucket,
            TipoRecurso.MANAGED_DATABASE: _banco,
            TipoRecurso.SECURITY_GROUP: _nsg,
        }.get(r.resource_type, lambda *_: None)(r, bp)


def _compartimento() -> str:
    return atributo("compartment_id", referencia("var.compartment_id"))


def _rede(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    return bloco("resource", ["oci_core_vcn", r.logical_name], [
        _compartimento(),
        atributo("cidr_blocks", [str(r.configuration.get("cidr") or "10.0.0.0/16")]),
        atributo("display_name", nome_cloud(bp.project_name, "vcn")),
        atributo("dns_label", nome_cloud(bp.project_name, maximo=15, separador="")),
    ])


def _subnet(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    espelho = texto_espelho(r.configuration)
    bloco_hcl = bloco("resource", ["oci_core_subnet", r.logical_name], [
        _compartimento(),
        atributo("vcn_id", referencia(f"oci_core_vcn.{REDE}.id")),
        atributo("cidr_block", str(r.configuration.get("cidr") or "10.0.0.0/24")),
        atributo(
            "display_name", nome_cloud(bp.project_name, r.logical_name if espelho else "subnet")
        ),
        # `true` aqui deixaria toda instância sem IP público mesmo quando o
        # usuário pediu acesso público — a lógica é invertida no nome.
        atributo(
            "prohibit_public_ip_on_vnic",
            not bool(r.configuration.get("public_access")),
        ),
    ])
    return comentario(espelho) + "\n" + bloco_hcl if espelho else bloco_hcl


def _compute(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    vcpus = max(1, int(float(cfg.get("vcpus") or 1)))
    ram = float(cfg.get("ram_gb") or 0) or vcpus * _GB_POR_VCPU
    instancias = int(cfg.get("instances") or 1)
    # A capacidade do pool é o TOTAL; o shape é por instância. O shape é a família
    # que a análise cotou, e `ocpus` sai da vCPU pela regra da família (x86 e
    # AmpereOne: 2 vCPU por OCPU; A1: 1). Antes era sempre E4 com OCPU = vCPU, o
    # dobro do que foi cotado.
    lido = ler_shape(str(cfg.get("machine_type") or ""))
    familia = lido[0] if lido else FAMILIA_PADRAO
    shape = _SHAPE_DA_FAMILIA.get(familia, f"VM.Standard.{familia}.Flex")
    por_instancia = ocpus_para(vcpus / max(1, instancias), familia)
    ram_instancia = max(1.0, round(ram / max(1, instancias), 1))
    slug = r.logical_name.replace("_", "-")
    texto = (
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total).\n"
        "Shape flexível: a OCI cobra OCPU e memória separadamente, então o\n"
        "dimensionamento vai em `shape_config` em vez de um nome fechado."
    )
    origem = [
        atributo("source_type", "image", nivel=2),
        atributo("source_id", referencia("var.image_ocid"), nivel=2),
    ]
    raiz = disco_raiz(cfg, minimo=50, maximo=32_768, tipo="boot volume")
    if raiz is not None:
        texto += "\n" + raiz[1]
        origem.append(atributo("boot_volume_size_in_gbs", raiz[0], nivel=2))

    return comentario(texto) + "\n" + bloco("resource", ["oci_core_instance", r.logical_name], [
        atributo("count", instancias),
        _compartimento(),
        atributo("availability_domain", referencia("var.availability_domain")),
        atributo("display_name", referencia(
            f'"{nome_cloud(bp.project_name, slug)}-${{count.index}}"'
        )),
        atributo("shape", shape),
        bloco_aninhado("shape_config", [
            atributo("ocpus", por_instancia, nivel=2),
            atributo("memory_in_gbs", ram_instancia, nivel=2),
        ], nivel=1),
        bloco_aninhado("create_vnic_details", [
            atributo(
                "subnet_id",
                referencia(f"oci_core_subnet.{cfg.get('subnet') or SUBNET}.id"),
                nivel=2,
            ),
            atributo("assign_public_ip", bool(cfg.get("public_ip")), nivel=2),
            *(
                [atributo("nsg_ids", referencia(
                    "[" + ", ".join(
                        f"oci_core_network_security_group.{g}.id" for g in cfg["security_groups"]
                    ) + "]"
                ), nivel=2)]
                if cfg.get("security_groups")
                else []
            ),
        ], nivel=1),
        bloco_aninhado("source_details", origem, nivel=1),
    ])


# Número do protocolo IP, como a OCI pede.
_PROTOCOLO_OCI = {"tcp": "6", "udp": "17", "icmp": "1", "todos": "all"}


def _nsg(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    """NSG espelhado: uma regra por origem (faixa ou outro NSG), saída liberada."""
    cfg = r.configuration
    nsg = f"oci_core_network_security_group.{r.logical_name}.id"
    partes = [comentario(texto_grupo(cfg)) + "\n" + bloco(
        "resource", ["oci_core_network_security_group", r.logical_name], [
            _compartimento(),
            atributo("vcn_id", referencia(f"oci_core_vcn.{REDE}.id")),
            atributo("display_name", nome_cloud(bp.project_name, r.logical_name)),
        ])]
    n = 0
    for regra in cfg.get("rules") or []:
        protocolo = _PROTOCOLO_OCI.get(str(regra.get("protocol")), "all")
        opcoes = []
        faixa = faixa_de_portas(regra) if protocolo in ("6", "17") else None
        if faixa is not None:
            opcoes = [bloco_aninhado("tcp_options" if protocolo == "6" else "udp_options", [
                bloco_aninhado("destination_port_range", [
                    atributo("min", faixa[0], nivel=3),
                    atributo("max", faixa[1], nivel=3),
                ], nivel=2),
            ], nivel=1)]
        for tipo, valor in fontes(regra):
            n += 1
            origem = (
                [atributo("source", valor), atributo("source_type", "CIDR_BLOCK")]
                if tipo == "cidr"
                else [
                    atributo("source", referencia(f"oci_core_network_security_group.{valor}.id")),
                    atributo("source_type", "NETWORK_SECURITY_GROUP"),
                ]
            )
            partes.append(bloco(
                "resource",
                ["oci_core_network_security_group_security_rule", f"{r.logical_name}_{n}"],
                [
                    atributo("network_security_group_id", referencia(nsg)),
                    atributo("direction", "INGRESS"),
                    atributo("protocol", protocolo),
                    *origem,
                    *opcoes,
                ],
            ))
    partes.append(bloco(
        "resource",
        ["oci_core_network_security_group_security_rule", f"{r.logical_name}_saida"],
        [
            atributo("network_security_group_id", referencia(nsg)),
            atributo("direction", "EGRESS"),
            atributo("protocol", "all"),
            atributo("destination", "0.0.0.0/0"),
            atributo("destination_type", "CIDR_BLOCK"),
        ],
    ))
    return "\n\n".join(partes)


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    tamanho, nota = ajustar_tamanho(
        int(cfg.get("size_gb") or 50), minimo=50, maximo=32_768, tipo="Block Volume"
    )
    n = quantidade_volumes(cfg)
    nome = nome_cloud(bp.project_name, r.logical_name)
    return comentario(texto_tamanho_disco(cfg) + nota) + "\n" + bloco(
        "resource", ["oci_core_volume", r.logical_name],
        ([atributo("count", n)] if n > 1 else []) + [
        _compartimento(),
        atributo("availability_domain", referencia("var.availability_domain")),
        atributo("display_name", referencia(f'"{nome}-${{count.index}}"') if n > 1 else nome),
        atributo("size_in_gbs", tamanho),
    ])


def _bucket(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    return comentario(
        texto_volume_bucket(cfg) +
        "O bucket não tem tamanho declarado — o número é só referência."
    ) + "\n" + bloco("resource", ["oci_objectstorage_bucket", r.logical_name], [
        _compartimento(),
        atributo("namespace", referencia("var.object_storage_namespace")),
        atributo("name", nome_cloud(bp.project_name, r.logical_name)),
        atributo("access_type", "ObjectRead" if publico else "NoPublicAccess"),
        atributo("versioning", "Enabled"),
    ])


def _db_name(bp: TerraformBlueprint, r: RecursoTerraform, n: int) -> str:
    """`db_name`: só alfanumérico, até 14, único no compartimento.

    Um banco (caminho agregado): o nome de sempre. Grupos medidos: prefixo +
    4 hex do nome lógico (dois grupos truncados para o mesmo prefixo
    colidiriam) + índice do `count` — 6 + 4 + 3 dígitos cabem em 14.
    """
    if not r.configuration.get("source_class"):
        return nome_cloud(bp.project_name, maximo=14, separador="").upper()
    prefixo = nome_cloud(bp.project_name, maximo=6, separador="").upper()
    prefixo += hashlib.sha256(r.logical_name.encode()).hexdigest()[:4].upper()
    return referencia(f'"{prefixo}${{count.index}}"') if n > 1 else prefixo


# Cada motor no produto dele (PROXIMAS_EXECUCOES, item 1). Formas do catálogo da OCI
# lidas em 18/10/2026 (docs.oracle.com, "Supported Shapes" do PostgreSQL e do MySQL
# HeatWave). As contas são as da cotação: OCPU = vCPU ÷ 2 no PostgreSQL, ECPU = vCPU
# no MySQL
_FORMA_POSTGRES = "PostgreSQL.VM.Standard.E5.Flex"
_FAMILIA_POSTGRES = "E5"
_GB_MINIMO_POSTGRES = 16
_GB_MAXIMO_POR_OCPU_POSTGRES = 64
_GB_MAXIMO_POSTGRES = 1024
# Sem a classe da origem, a proporção das formas fixas (16 GB por OCPU)
_GB_POR_OCPU_POSTGRES = 16
# Alta disponibilidade do PostgreSQL da OCI: um nó a mais sobre o mesmo armazenamento
_NOS_COM_HA_POSTGRES = 2
_ECPUS_MYSQL = (2, 4, 8, 16, 32, 48, 64, 96, 192, 256)


def _produto_do_banco(r: RecursoTerraform) -> str:
    """O motor medido escolhe o produto; sem motor (caminho agregado), PostgreSQL, como
    na Azure. SQL Server não chega aqui: o plano o recusa para a OCI."""
    familia = str(r.configuration.get("engine_family") or "")
    return familia if familia in ("mysql", "oracle") else "postgresql"


def _banco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    gerar = {"mysql": _mysql, "oracle": _autonomous}.get(_produto_do_banco(r), _postgresql)
    return gerar(r, bp)


def _subnet_do_banco(cfg: dict[str, Any]) -> str:
    """A subrede de dados espelhada (T3) ou, sem ela, a principal: o PostgreSQL e o
    HeatWave só existem dentro de uma subrede."""
    subredes = [str(x) for x in cfg.get("db_subnets") or []]
    return referencia(f"oci_core_subnet.{subredes[0] if subredes else SUBNET}.id")


def _memoria_postgres(cfg: dict[str, Any], ocpus: int) -> int:
    """A memória da classe da origem, dentro dos limites da forma flexível."""
    ram = ram_da_classe(str(cfg.get("source_class") or "") or None)
    pedida = math.ceil(ram) if ram else ocpus * _GB_POR_OCPU_POSTGRES
    teto = min(ocpus * _GB_MAXIMO_POR_OCPU_POSTGRES, _GB_MAXIMO_POSTGRES)
    return min(max(_GB_MINIMO_POSTGRES, ocpus, pedida), teto)


def _postgresql(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = max(1, int(cfg.get("vcpus") or 1))
    ocpus = ocpus_para(vcpus, _FAMILIA_POSTGRES)
    memoria = _memoria_postgres(cfg, ocpus)
    nos = _NOS_COM_HA_POSTGRES if cfg.get("high_availability") else 1
    n = instancias_banco(cfg)
    return comentario(
        texto_banco(cfg, vcpus) + "\n"
        f"OCI Database with PostgreSQL, forma {_FORMA_POSTGRES}: {ocpus} OCPU (2 vCPU por\n"
        f"OCPU, a conta da cotação) e {memoria} GB, {nos} nó(s). A variável de versão aqui é\n"
        "a versão maior do PostgreSQL (14, 15 ou 16).\n"
        "A senha de admin NÃO é gerada aqui — preencha `db_admin_password`."
    ) + "\n" + bloco("resource", ["oci_psql_db_system", r.logical_name], [
        *([atributo("count", n)] if n > 1 else []),
        _compartimento(),
        atributo("display_name", com_indice(nome_banco(bp, r), n)),
        atributo("db_version", var_versao(cfg)),
        atributo("shape", _FORMA_POSTGRES),
        atributo("instance_ocpu_count", ocpus),
        atributo("instance_memory_size_in_gbs", memoria),
        atributo("instance_count", nos),
        bloco_aninhado("network_details", [
            atributo("subnet_id", _subnet_do_banco(cfg), nivel=2),
        ], nivel=1),
        bloco_aninhado("storage_details", [
            atributo("is_regionally_durable", True, nivel=2),
            atributo("system_type", "OCI_OPTIMIZED_STORAGE", nivel=2),
        ], nivel=1),
        bloco_aninhado("credentials", [
            atributo("username", referencia("var.db_admin_username"), nivel=2),
            bloco_aninhado("password_details", [
                atributo("password_type", "PLAIN_TEXT", nivel=3),
                atributo("password", referencia("var.db_admin_password"), nivel=3),
            ], nivel=2),
        ], nivel=1),
    ])


def _mysql(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = max(1, int(cfg.get("vcpus") or 1))
    ecpus = next((e for e in _ECPUS_MYSQL if e >= vcpus), _ECPUS_MYSQL[-1])
    forma = f"MySQL.{ecpus}"
    n = instancias_banco(cfg)
    return comentario(
        texto_banco(cfg, vcpus) + "\n"
        f"MySQL HeatWave, forma {forma}: {ecpus} ECPU (ECPU = vCPU, a premissa da cotação).\n"
        "A versão é a completa do catálogo da OCI (ex.: 8.4.x): `oci mysql version list`.\n"
        "A senha de admin NÃO é gerada aqui — preencha `db_admin_password`."
    ) + "\n" + bloco("resource", ["oci_mysql_mysql_db_system", r.logical_name], [
        *([atributo("count", n)] if n > 1 else []),
        _compartimento(),
        atributo("availability_domain", referencia("var.availability_domain")),
        atributo("display_name", com_indice(nome_banco(bp, r), n)),
        atributo("shape_name", forma),
        atributo("mysql_version", var_versao(cfg)),
        atributo("subnet_id", _subnet_do_banco(cfg)),
        atributo("admin_username", referencia("var.db_admin_username")),
        atributo("admin_password", referencia("var.db_admin_password")),
        atributo("is_highly_available", bool(cfg.get("high_availability"))),
    ])


def _autonomous(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = max(1, int(cfg.get("vcpus") or 1))
    ocpus = ocpus_para(vcpus, FAMILIA_PADRAO)
    subredes = [str(x) for x in cfg.get("db_subnets") or []]
    return comentario(
        texto_banco(cfg, vcpus) + "\n"
        f"Autonomous Database, o motor Oracle da origem: {ocpus} OCPU (2 vCPU por OCPU).\n"
        "A senha de admin NÃO é gerada aqui — preencha `db_admin_password`."
    ) + "\n" + bloco(
        "resource", ["oci_database_autonomous_database", r.logical_name], [
            *([atributo("count", n)] if (n := instancias_banco(cfg)) > 1 else []),
            _compartimento(),
            atributo("db_name", _db_name(bp, r, n)),
            atributo("display_name", com_indice(nome_banco(bp, r), n)),
            atributo("cpu_core_count", ocpus),
            atributo("data_storage_size_in_tbs", 1),
            atributo("admin_password", referencia("var.db_admin_password")),
            atributo("is_auto_scaling_enabled", True),
            # T3: endpoint privado na subrede de dados espelhada
            *([atributo("subnet_id", referencia(f"oci_core_subnet.{subredes[0]}.id"))]
              if subredes else []),
        ],
    )
