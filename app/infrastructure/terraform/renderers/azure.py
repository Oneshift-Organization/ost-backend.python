"""Adaptador Azure.

A nuvem mais exigente das quatro, por três motivos que moldam o arquivo:

- **Tudo vive num resource group.** Ele não sai da análise — é estrutura da
  Azure —, então o pacote cria um e todo recurso aponta para ele.
- **VM exige NIC como recurso separado.** No GCP a interface é um bloco dentro
  da instância; aqui é `azurerm_network_interface`, um por VM.
- **Nome de storage account é 3 a 24 caracteres, só minúscula e dígito.** Sem
  hífen, sem underscore. É a regra de nome mais restritiva das quatro nuvens.
"""

from __future__ import annotations

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
    disco_ssd,
    faixa_de_portas,
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

GRUPO = "grupo_principal"
REDE = "rede_principal"
SUBNET = "subnet_principal"

_MAX_STORAGE = 24


class AdaptadorAzure:
    provider_nome = "azurerm"
    provider_source = "hashicorp/azurerm"
    provider_version = "~> 3.0"

    def provider_bloco(self, bp: TerraformBlueprint) -> str:
        # `features {}` é obrigatório mesmo vazio — sem ele o provider nem
        # inicializa.
        return bloco("provider", ["azurerm"], [bloco_aninhado("features", [])])

    def variaveis_extras(self, bp: TerraformBlueprint) -> list[VariavelTerraform]:
        return [
            VariavelTerraform(
                name="admin_username",
                description="Usuário administrador das VMs Linux.",
                default="azureuser",
            ),
            VariavelTerraform(
                name="ssh_public_key",
                description=(
                    "Chave SSH pública para acesso às VMs. A Azure exige chave "
                    "ou senha; a plataforma não gera nem armazena nenhuma das duas."
                ),
            ),
        ]

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        return [
            comentario(
                "Resource group: estrutura da Azure, não sai da análise.\n"
                "Todo recurso do pacote vive dentro dele."
            )
            + "\n"
            + bloco("resource", ["azurerm_resource_group", GRUPO], [
                atributo("name", nome_cloud(bp.project_name, "rg")),
                atributo("location", bp.region),
                atributo("tags", {
                    "environment": bp.environment, "managed_by": "oneshift",
                }),
            ])
        ]

    def outputs(self, bp: TerraformBlueprint) -> list[str]:
        saidas = [bloco("output", ["resource_group"], [
            atributo("value", referencia(f"azurerm_resource_group.{GRUPO}.name")),
            atributo("description", "Resource group criado."),
        ])]
        for r in bp.resources:
            if r.resource_type is TipoRecurso.OBJECT_STORAGE:
                saidas.append(bloco("output", [f"{r.logical_name}_endpoint"], [
                    atributo("value", referencia(
                        f"azurerm_storage_account.{r.logical_name}.primary_blob_endpoint"
                    )),
                    atributo("description", "Endpoint do blob storage."),
                ]))
        return saidas

    def recurso(self, r: RecursoTerraform, bp: TerraformBlueprint) -> str | None:
        return {
            TipoRecurso.NETWORK: _rede,
            TipoRecurso.SUBNET: _subnet,
            TipoRecurso.COMPUTE: _compute,
            TipoRecurso.OBJECT_STORAGE: _storage,
            TipoRecurso.MANAGED_DATABASE: _banco,
            TipoRecurso.SECURITY_GROUP: _asg,
        }.get(r.resource_type, lambda *_: None)(r, bp)


def _grupo() -> list[str]:
    return [
        atributo("resource_group_name", referencia(
            f"azurerm_resource_group.{GRUPO}.name"
        )),
        atributo("location", referencia(f"azurerm_resource_group.{GRUPO}.location")),
    ]


def _rede(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    return bloco("resource", ["azurerm_virtual_network", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, "vnet")),
        atributo("address_space", [str(r.configuration.get("cidr") or "10.0.0.0/16")]),
        *_grupo(),
    ])


def _subnet(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    espelho = texto_espelho(r.configuration)
    bloco_hcl = bloco("resource", ["azurerm_subnet", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, r.logical_name if espelho else "subnet")),
        atributo("resource_group_name", referencia(
            f"azurerm_resource_group.{GRUPO}.name"
        )),
        atributo("virtual_network_name", referencia(
            f"azurerm_virtual_network.{REDE}.name"
        )),
        atributo("address_prefixes", [
            str(r.configuration.get("cidr") or "10.0.0.0/24")
        ]),
    ])
    nsg = _nsg_da_subrede(r, bp)
    if espelho:
        bloco_hcl = comentario(espelho) + "\n" + bloco_hcl
    return bloco_hcl + ("\n\n" + nsg if nsg else "")


def _compute(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    slug = r.logical_name.replace("_", "-")
    base = nome_cloud(bp.project_name, slug)
    nic = f"{r.logical_name}_nic"

    disco_so = [
        atributo("caching", "ReadWrite", nivel=2),
        atributo("storage_account_type", "Premium_LRS", nivel=2),
    ]
    # 30 GB é o tamanho da imagem Ubuntu do marketplace: menor que isso, a VM
    # não sobe. 4.095 GB é o teto de disco de SO.
    raiz = disco_raiz(cfg, minimo=30, maximo=4_095, tipo="disco de SO")
    if raiz is not None:
        disco_so = [
            atributo("caching", "ReadWrite", nivel=2),
            atributo(
                "storage_account_type",
                "Standard_LRS" if cfg.get("root_disk_type") == "pd-standard" else "Premium_LRS",
                nivel=2,
            ),
            atributo("disk_size_gb", raiz[0], nivel=2),
        ]

    interface = bloco("resource", ["azurerm_network_interface", nic], [
        atributo("count", int(cfg.get("instances") or 1)),
        atributo("name", referencia(f'"{base}-nic-${{count.index}}"')),
        *_grupo(),
        bloco_aninhado("ip_configuration", [
            atributo("name", "interna", nivel=2),
            atributo(
                "subnet_id", referencia(f"azurerm_subnet.{cfg.get('subnet') or SUBNET}.id"), nivel=2
            ),
            atributo("private_ip_address_allocation", "Dynamic", nivel=2),
        ], nivel=1),
    ])

    vm = bloco("resource", ["azurerm_linux_virtual_machine", r.logical_name], [
        atributo("count", int(cfg.get("instances") or 1)),
        atributo("name", referencia(f'"{base}-${{count.index}}"')),
        *_grupo(),
        atributo("size", str(cfg.get("machine_type"))),
        atributo("admin_username", referencia("var.admin_username")),
        atributo("network_interface_ids", referencia(
            f"[azurerm_network_interface.{nic}[count.index].id]"
        )),
        bloco_aninhado("admin_ssh_key", [
            atributo("username", referencia("var.admin_username"), nivel=2),
            atributo("public_key", referencia("var.ssh_public_key"), nivel=2),
        ], nivel=1),
        bloco_aninhado("os_disk", disco_so, nivel=1),
        bloco_aninhado("source_image_reference", [
            atributo("publisher", "Canonical", nivel=2),
            atributo("offer", "0001-com-ubuntu-server-jammy", nivel=2),
            atributo("sku", "22_04-lts-gen2", nivel=2),
            atributo("version", "latest", nivel=2),
        ], nivel=1),
    ])

    texto = (
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total).\n"
        "A NIC é recurso separado: a Azure exige uma por VM, ao contrário do GCP."
    )
    if raiz is not None:
        texto += "\n" + raiz[1]
    associacoes = [
        bloco("resource", [
            "azurerm_network_interface_application_security_group_association",
            f"{r.logical_name}_{g}",
        ], [
            atributo("count", int(cfg.get("instances") or 1)),
            atributo("network_interface_id", referencia(
                f"azurerm_network_interface.{nic}[count.index].id"
            )),
            atributo("application_security_group_id", referencia(
                f"azurerm_application_security_group.{g}.id"
            )),
        ])
        for g in cfg.get("security_groups") or []
    ]
    return "\n\n".join([comentario(texto) + "\n" + interface, vm, *associacoes])


_PROTOCOLO_AZURE = {"tcp": "Tcp", "udp": "Udp", "icmp": "Icmp", "todos": "*"}
# Faixa de prioridade das regras de usuário num NSG.
_PRIORIDADE_INICIAL = 100
_PRIORIDADE_MAXIMA = 4096
_PASSO = 10


def _asg(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    return comentario(
        texto_grupo(r.configuration)
        + "\nNa Azure o grupo é um ASG: a VM entra pela NIC, e as regras ficam no NSG\n"
        "da subrede de cada membro, com o ASG como destino."
    ) + "\n" + bloco("resource", ["azurerm_application_security_group", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, r.logical_name)),
        *_grupo(),
    ])


def _nsg_da_subrede(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    """Um NSG por subrede de destino, com as regras dos grupos que têm membro nela.

    A NIC aceita um NSG só, e a VM de origem podia ter vários grupos: por isso o
    grupo vira ASG e a regra fica no NSG da subrede, com o ASG como destino.
    Prioridade 100 + 10 por regra, em ordem determinística (RI15.4).
    """
    alvos = sorted(
        (
            g for g in bp.resources
            if g.resource_type is TipoRecurso.SECURITY_GROUP
            and r.logical_name in (g.configuration.get("subnets") or [])
        ),
        key=lambda g: g.logical_name,
    )
    regras = []
    for g in alvos:
        for regra in g.configuration.get("rules") or []:
            protocolo = _PROTOCOLO_AZURE.get(str(regra.get("protocol")), "*")
            faixa = faixa_de_portas(regra) if protocolo in ("Tcp", "Udp") else None
            porta = "*" if faixa is None else (
                str(faixa[0]) if faixa[0] == faixa[1] else f"{faixa[0]}-{faixa[1]}"
            )
            comum = [
                atributo("direction", "Inbound", nivel=2),
                atributo("access", "Allow", nivel=2),
                atributo("protocol", protocolo, nivel=2),
                atributo("source_port_range", "*", nivel=2),
                atributo("destination_port_range", porta, nivel=2),
                atributo("destination_application_security_group_ids", referencia(
                    f"[azurerm_application_security_group.{g.logical_name}.id]"
                ), nivel=2),
            ]
            # Uma regra da Azure não mistura faixa e ASG na origem: vira duas.
            if regra.get("source_cidrs"):
                regras.append([*comum, atributo(
                    "source_address_prefixes", [str(c) for c in regra["source_cidrs"]], nivel=2
                )])
            if regra.get("source_groups"):
                regras.append([*comum, atributo(
                    "source_application_security_group_ids", referencia(
                        "[" + ", ".join(
                            f"azurerm_application_security_group.{x}.id"
                            for x in regra["source_groups"]
                        ) + "]"
                    ), nivel=2,
                )])
    blocos = []
    for i, corpo in enumerate(regras):
        prioridade = _PRIORIDADE_INICIAL + _PASSO * i
        if prioridade > _PRIORIDADE_MAXIMA:
            break
        blocos.append(bloco_aninhado("security_rule", [
            atributo("name", f"regra-{i + 1}", nivel=2),
            atributo("priority", prioridade, nivel=2),
            *corpo,
        ], nivel=1))
    if not blocos:
        return ""
    nsg = f"nsg_{r.logical_name}"
    return bloco("resource", ["azurerm_network_security_group", nsg], [
        atributo("name", nome_cloud(bp.project_name, nsg)),
        *_grupo(),
        *blocos,
    ]) + "\n\n" + bloco(
        "resource",
        ["azurerm_subnet_network_security_group_association", f"{r.logical_name}_nsg"],
        [
            atributo("subnet_id", referencia(f"azurerm_subnet.{r.logical_name}.id")),
            atributo("network_security_group_id", referencia(
                f"azurerm_network_security_group.{nsg}.id"
            )),
        ])


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    tipo = "Premium_LRS" if disco_ssd(cfg) else "Standard_LRS"
    tamanho, nota = ajustar_tamanho(
        int(cfg.get("size_gb") or 10), minimo=1, maximo=32_767, tipo=tipo
    )
    n = quantidade_volumes(cfg)
    nome = nome_cloud(bp.project_name, r.logical_name)
    return comentario(texto_tamanho_disco(cfg) + nota) + "\n" + bloco(
        "resource", ["azurerm_managed_disk", r.logical_name],
        ([atributo("count", n)] if n > 1 else []) + [
        atributo("name", referencia(f'"{nome}-${{count.index}}"') if n > 1 else nome),
        *_grupo(),
        atributo("storage_account_type", tipo),
        atributo("create_option", "Empty"),
        atributo("disk_size_gb", tamanho),
    ])


def _storage(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    # 3 a 24 caracteres, SÓ minúscula e dígito: sem hífen, sem underscore. É a
    # regra de nome mais restritiva das quatro nuvens.
    nome = nome_cloud(bp.project_name, r.logical_name, maximo=_MAX_STORAGE, separador="")
    return comentario(
        texto_volume_bucket(cfg) +
        "Nome sem hífen e com 24 caracteres no máximo — exigência da Azure."
    ) + "\n" + bloco("resource", ["azurerm_storage_account", r.logical_name], [
        atributo("name", nome),
        *_grupo(),
        atributo("account_tier", "Standard"),
        atributo("account_replication_type", "LRS"),
        atributo("allow_nested_items_to_be_public", publico),
        atributo("min_tls_version", "TLS1_2"),
        bloco_aninhado("blob_properties", [
            atributo("versioning_enabled", True, nivel=2),
            bloco_aninhado("delete_retention_policy", [
                atributo("days", 7, nivel=3),
            ], nivel=2),
        ], nivel=1),
    ])


def _banco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = int(cfg.get("vcpus") or 1)
    ha = bool(cfg.get("high_availability"))
    sku = _sku_azure(vcpus)
    return comentario(
        texto_banco(cfg, vcpus) + "\n"
        f"Traduzido para o SKU {sku}, o mais próximo acima.\n"
        "`database_version` aqui é a versão do PostgreSQL (ex.: 15)."
    ) + "\n" + bloco(
        "resource", ["azurerm_postgresql_flexible_server", r.logical_name], [
            *([atributo("count", n)] if (n := instancias_banco(cfg)) > 1 else []),
            atributo("name", com_indice(nome_banco(bp, r), n)),
            *_grupo(),
            atributo("version", var_versao(cfg)),
            atributo("sku_name", sku),
            atributo("storage_mb", 131072),
            atributo("administrator_login", referencia("var.admin_username")),
            atributo("zone", "1"),
        ]
        + ([bloco_aninhado("high_availability", [
            atributo("mode", "ZoneRedundant", nivel=2),
        ], nivel=1)] if ha else []),
    )


_SKUS_AZURE = ((2, "GP_Standard_D2s_v3"), (4, "GP_Standard_D4s_v3"),
               (8, "GP_Standard_D8s_v3"), (16, "GP_Standard_D16s_v3"),
               (32, "GP_Standard_D32s_v3"))


def _sku_azure(vcpus: int) -> str:
    """O menor SKU que comporta o vCPU estimado — nunca para baixo."""
    for limite, sku in _SKUS_AZURE:
        if vcpus <= limite:
            return sku
    return "GP_Standard_D64s_v3"
