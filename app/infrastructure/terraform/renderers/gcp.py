"""Adaptador GCP."""

from __future__ import annotations

import hashlib

from app.infrastructure.terraform.hcl import (
    atributo,
    bloco,
    bloco_aninhado,
    comentario,
    referencia,
)
from app.infrastructure.terraform.renderers.base import (
    TEXTO_SAIDA,
    ajustar_tamanho,
    com_indice,
    disco_raiz,
    faixa_de_portas,
    identificador,
    instancias_banco,
    itens_do_componente,
    nome_banco,
    nome_cloud,
    nome_de_zona,
    quantidade_volumes,
    texto_banco,
    texto_cluster,
    texto_espelho,
    texto_grupo,
    texto_tamanho_disco,
    texto_volume_bucket,
    var_versao,
    variavel_de_versao_do_kubernetes,
)
from app.schemas.terraform import (
    RecursoTerraform,
    TerraformBlueprint,
    TipoRecurso,
    VariavelTerraform,
)

REDE = "rede_principal"
SUBNET = "subnet_principal"


class AdaptadorGCP:
    provider_nome = "google"
    provider_source = "hashicorp/google"
    provider_version = "~> 5.0"

    def provider_bloco(self, bp: TerraformBlueprint) -> str:
        return bloco("provider", ["google"], [
            atributo("project", referencia("var.project_id")),
            atributo("region", bp.region),
        ])

    def variaveis_extras(self, bp: TerraformBlueprint) -> list[VariavelTerraform]:
        return [
            VariavelTerraform(
                name="project_id",
                description="ID do projeto GCP onde os recursos serão criados.",
            ),
            VariavelTerraform(
                name="boot_image",
                description=(
                    "Imagem de boot das VMs. O billing não informa o sistema "
                    "operacional em uso."
                ),
                default="debian-cloud/debian-12",
            ),
            *variavel_de_versao_do_kubernetes(bp, aceita_versao_maior=True, exemplo="1.30"),
        ]

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        return []

    def outputs(self, bp: TerraformBlueprint) -> list[str]:
        saidas = []
        if any(r.resource_type is TipoRecurso.NETWORK for r in bp.resources):
            saidas.append(bloco("output", ["network_id"], [
                atributo("value", referencia(f"google_compute_network.{REDE}.id")),
                atributo("description", "ID da VPC criada."),
            ]))
        for r in bp.resources:
            if r.resource_type is TipoRecurso.OBJECT_STORAGE:
                saidas.append(bloco("output", [f"{r.logical_name}_url"], [
                    atributo("value", referencia(
                        f"google_storage_bucket.{r.logical_name}.url"
                    )),
                    atributo("description", "URL do bucket."),
                ]))
        return saidas

    def recurso(self, r: RecursoTerraform, bp: TerraformBlueprint) -> str | None:
        return {
            TipoRecurso.NETWORK: _rede,
            TipoRecurso.SUBNET: _subnet,
            TipoRecurso.COMPUTE: _compute,
            TipoRecurso.OBJECT_STORAGE: _bucket,
            TipoRecurso.MANAGED_DATABASE: _banco,
            TipoRecurso.SECURITY_GROUP: _grupo,
            TipoRecurso.KUBERNETES_CLUSTER: _cluster,
            TipoRecurso.CONTAINER_REGISTRY: _registro,
            TipoRecurso.VAULT: _segredo,
            TipoRecurso.DNS_ZONE: _zona,
            TipoRecurso.NETWORK_EGRESS: _saida,
        }.get(r.resource_type, lambda *_: None)(r, bp)


def _rede(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    return bloco("resource", ["google_compute_network", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, "vpc")),
        # Subnets automáticas criariam uma faixa por região do mundo inteiro,
        # ignorando o CIDR que o usuário informou.
        atributo("auto_create_subnetworks", False),
        atributo("routing_mode", "REGIONAL"),
    ])


def _subnet(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    espelho = texto_espelho(r.configuration)
    nome = nome_cloud(bp.project_name, r.logical_name if espelho else "subnet")
    bloco_hcl = bloco("resource", ["google_compute_subnetwork", r.logical_name], [
        atributo("name", nome),
        atributo("ip_cidr_range", str(r.configuration.get("cidr") or "10.0.0.0/24")),
        atributo("region", bp.region),
        atributo("network", referencia(f"google_compute_network.{REDE}.id")),
        # Sem isto, tráfego para APIs do Google sairia pela internet pública.
        atributo("private_ip_google_access", True),
    ])
    return comentario(espelho) + "\n" + bloco_hcl if espelho else bloco_hcl


def _compute(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    slug = r.logical_name.replace("_", "-")
    inicializacao = [atributo("image", referencia("var.boot_image"), nivel=3)]
    raiz = disco_raiz(cfg, minimo=10, maximo=65_536, tipo="boot disk")
    if raiz is not None:
        inicializacao += [
            atributo("size", raiz[0], nivel=3),
            # Sem `type`, o provider cria o boot em pd-standard (HDD).
            atributo("type", str(cfg.get("root_disk_type") or "pd-balanced"), nivel=3),
        ]
    corpo = [
        atributo("count", int(cfg.get("instances") or 1)),
        atributo("name", referencia(
            f'"{nome_cloud(bp.project_name, slug)}-${{count.index}}"'
        )),
        atributo("machine_type", str(cfg.get("machine_type"))),
        atributo("zone", f"{bp.region}-a"),
        bloco_aninhado("boot_disk", [
            bloco_aninhado("initialize_params", inicializacao, nivel=2),
        ], nivel=1),
        bloco_aninhado("network_interface", [
            atributo("subnetwork", referencia(
                f"google_compute_subnetwork.{cfg.get('subnet') or SUBNET}.id"
            ), nivel=2),
            # IP externo só onde a subrede de destino é pública.
            *([bloco_aninhado("access_config", [], nivel=2)] if cfg.get("public_ip") else []),
        ], nivel=1),
        atributo("labels", {"environment": bp.environment, "managed_by": "oneshift"}),
        *(
            [atributo("tags", [tag_de_rede(g) for g in cfg["security_groups"]])]
            if cfg.get("security_groups")
            else []
        ),
    ]
    texto = (
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total)."
    )
    if raiz is not None:
        texto += "\n" + raiz[1]
    return comentario(texto) + "\n" + bloco(
        "resource", ["google_compute_instance", r.logical_name], corpo
    )


_PROTOCOLO_GCP = {"tcp": "tcp", "udp": "udp", "icmp": "icmp", "todos": "all"}


def tag_de_rede(nome_logico: str) -> str:
    """No GCP o grupo é uma tag de rede: minúsculas, dígitos e hífen, até 63."""
    return nome_cloud(nome_logico)


def _grupo(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    """Tag de rede, e um firewall por regra traduzida (faixas e tags na mesma regra).

    O nome do firewall leva um hash curto do grupo: truncar para 63 caracteres
    podia dar o mesmo nome a regras de grupos de prefixo longo igual.
    """
    cfg = r.configuration
    tag = tag_de_rede(r.logical_name)
    marca = hashlib.sha256(r.logical_name.encode()).hexdigest()[:6]
    base = nome_cloud(bp.project_name, r.logical_name, maximo=50)
    partes = [comentario(texto_grupo(cfg) + f"\nNo GCP o grupo é a tag de rede `{tag}`.")]
    for i, regra in enumerate(cfg.get("rules") or [], start=1):
        protocolo = _PROTOCOLO_GCP.get(str(regra.get("protocol")), "all")
        permitir = [atributo("protocol", protocolo, nivel=2)]
        faixa = faixa_de_portas(regra) if protocolo in ("tcp", "udp") else None
        if faixa is not None:
            porta = str(faixa[0]) if faixa[0] == faixa[1] else f"{faixa[0]}-{faixa[1]}"
            permitir.append(atributo("ports", [porta], nivel=2))
        corpo = [
            atributo("name", f"{base}-{marca}-{i}"),
            atributo("network", referencia(f"google_compute_network.{REDE}.name")),
            atributo("direction", "INGRESS"),
            atributo("priority", 1000),
            atributo("target_tags", [tag]),
        ]
        cidrs = [str(c) for c in regra.get("source_cidrs") or []]
        grupos = [tag_de_rede(str(g)) for g in regra.get("source_groups") or []]
        if cidrs:
            corpo.append(atributo("source_ranges", cidrs))
        if grupos:
            corpo.append(atributo("source_tags", grupos))
        corpo.append(bloco_aninhado("allow", permitir, nivel=1))
        partes.append(
            bloco("resource", ["google_compute_firewall", f"{r.logical_name}_{i}"], corpo)
        )
    return "\n\n".join(partes)


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    tipo = str(cfg.get("disk_type") or "pd-standard")
    tamanho, nota = ajustar_tamanho(
        int(cfg.get("size_gb") or 10), minimo=10, maximo=65_536, tipo=tipo
    )
    n = quantidade_volumes(cfg)
    nome = nome_cloud(bp.project_name, r.logical_name)
    return comentario(texto_tamanho_disco(cfg) + nota) + "\n" + bloco(
        "resource", ["google_compute_disk", r.logical_name],
        ([atributo("count", n)] if n > 1 else []) + [
        atributo("name", referencia(f'"{nome}-${{count.index}}"') if n > 1 else nome),
        atributo("type", tipo),
        atributo("zone", f"{bp.region}-a"),
        atributo("size", tamanho),
    ])


def _bucket(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    return comentario(
        texto_volume_bucket(cfg)
        + "O bucket não tem tamanho declarado — o número é só referência."
    ) + "\n" + bloco("resource", ["google_storage_bucket", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, r.logical_name)),
        atributo("location", str(cfg.get("location") or bp.region).upper()),
        atributo("storage_class", str(cfg.get("storage_class") or "STANDARD")),
        # Acesso uniforme desliga ACLs por objeto: sem isto, um objeto pode ser
        # público mesmo com o bucket privado.
        atributo("uniform_bucket_level_access", True),
        atributo("public_access_prevention", "inherited" if publico else "enforced"),
        bloco_aninhado("versioning", [
            atributo("enabled", True, nivel=2),
        ], nivel=1),
    ])


_PSA = "acesso_privado"


def _primeiro_banco(r: RecursoTerraform, bp: TerraformBlueprint) -> bool:
    """A faixa e a conexão são uma por VPC: saem com o primeiro banco."""
    primeiro = next(
        (x.logical_name for x in bp.resources if x.resource_type is TipoRecurso.MANAGED_DATABASE),
        None,
    )
    return primeiro == r.logical_name


def _acesso_privado(bp: TerraformBlueprint) -> str:
    """Acesso privado a serviços (T3): o Cloud SQL ganha IP privado na VPC nova.

    Sem a conexão, `private_network` falha no apply."""
    return comentario(
        "Acesso privado a serviços: faixa /16 reservada para os serviços do Google\n"
        "(Cloud SQL) e a conexão de peering com a VPC nova."
    ) + "\n" + bloco("resource", ["google_compute_global_address", f"{_PSA}_faixa"], [
        atributo("name", nome_cloud(bp.project_name, "faixa-servicos")),
        atributo("purpose", "VPC_PEERING"),
        atributo("address_type", "INTERNAL"),
        atributo("prefix_length", 16),
        atributo("network", referencia(f"google_compute_network.{REDE}.id")),
    ]) + "\n\n" + bloco("resource", ["google_service_networking_connection", _PSA], [
        atributo("network", referencia(f"google_compute_network.{REDE}.id")),
        atributo("service", "servicenetworking.googleapis.com"),
        atributo("reserved_peering_ranges", referencia(
            f"[google_compute_global_address.{_PSA}_faixa.name]"
        )),
    ]) + (
        # Item 13: com a rede espelhada, a origem chega ao banco pela VPN; o peering do
        # serviço precisa das rotas personalizadas da VPC
        "\n\n" + bloco("resource", ["google_compute_network_peering_routes_config", _PSA], [
            atributo("peering", referencia(f"google_service_networking_connection.{_PSA}.peering")),
            atributo("network", referencia(f"google_compute_network.{REDE}.name")),
            atributo("import_custom_routes", True),
            atributo("export_custom_routes", True),
        ])
        if any(r.configuration.get("mirror_of") for r in bp.resources)
        else ""
    )


def _banco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = int(cfg.get("vcpus") or 1)
    ha = bool(cfg.get("high_availability"))
    # O banco sempre usa `private_network`: sem o acesso privado a serviços, o apply
    # falhava, com ou sem inventário (T3)
    acesso = _acesso_privado(bp) + "\n\n" if _primeiro_banco(r, bp) else ""
    return acesso + comentario(
        texto_banco(cfg, vcpus) + "\n"
        "`deletion_protection` fica ligado; desligue conscientemente.\n"
        "IP privado na VPC nova, por acesso privado a serviços."
    ) + "\n" + bloco("resource", ["google_sql_database_instance", r.logical_name], [
        *([atributo("count", n)] if (n := instancias_banco(cfg)) > 1 else []),
        atributo("name", com_indice(nome_banco(bp, r), n)),
        atributo("database_version", var_versao(cfg)),
        atributo("region", bp.region),
        atributo("deletion_protection", True),
        atributo("depends_on", referencia(f"[google_service_networking_connection.{_PSA}]")),
        bloco_aninhado("settings", [
            atributo("tier", f"db-custom-{vcpus}-{vcpus * 3840}", nivel=2),
            atributo("availability_type", "REGIONAL" if ha else "ZONAL", nivel=2),
            bloco_aninhado("ip_configuration", [
                atributo("ipv4_enabled", False, nivel=3),
                atributo("private_network", referencia(
                    f"google_compute_network.{REDE}.id"
                ), nivel=3),
            ], nivel=2),
        ], nivel=1),
    ])


# ── Kubernetes e serviços gerenciados do escopo (itens 15 e 19) ─────────────

# Cluster regional: o node pool conta por zona, nas três zonas da região
_ZONAS_DO_CLUSTER = 3


def _cluster(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    pools = list(cfg.get("node_pools") or [])
    subrede = pools[0].get("subnet") if pools else None
    partes = [
        comentario(
            texto_cluster(cfg) + "\nCluster regional: cada node pool conta por zona, então "
            "os nós da origem\nse dividem pelas três zonas, arredondando para cima."
        )
        + "\n"
        + bloco("resource", ["google_container_cluster", r.logical_name], [
            atributo("name", nome_cloud(bp.project_name, cfg.get("source_name"), maximo=40)),
            atributo("location", bp.region),
            atributo("network", referencia(f"google_compute_network.{REDE}.id")),
            atributo(
                "subnetwork", referencia(f"google_compute_subnetwork.{subrede or SUBNET}.id")
            ),
            atributo("min_master_version", referencia("var.kubernetes_version")),
            # Os nós vêm dos node pools abaixo, um por grupo de nós da origem
            atributo("remove_default_node_pool", True),
            atributo("initial_node_count", 1),
        ])
    ]
    cluster = referencia(f"google_container_cluster.{r.logical_name}.id")
    for g in pools:
        n = max(1, int(g.get("instances") or 1))
        nome_do_pool = f"{r.logical_name}_{identificador(str(g.get('name')))}"
        partes.append(
            bloco(
                "resource",
                ["google_container_node_pool", nome_do_pool],
                [
                    atributo("name", nome_cloud(g.get("name"), maximo=40)),
                    atributo("cluster", cluster),
                    atributo("location", bp.region),
                    atributo("node_count", -(-n // _ZONAS_DO_CLUSTER)),
                    bloco_aninhado("node_config", [
                        atributo("machine_type", str(g.get("machine_type")), nivel=2),
                    ], nivel=1),
                ],
            )
        )
    return "\n\n".join(partes)


def _registro(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    origem = itens_do_componente(r.configuration)[0]
    return comentario(
        f"Repositório de origem: {origem}. As imagens não viajam pelo Terraform: copie-as\n"
        "da origem (skopeo copy, crane copy)."
    ) + "\n" + bloco("resource", ["google_artifact_registry_repository", r.logical_name], [
        atributo("location", bp.region),
        atributo("repository_id", nome_cloud(origem, maximo=63) or "imagens"),
        atributo("format", "DOCKER"),
    ])


def _segredo(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    origem = itens_do_componente(r.configuration)[0]
    nome = "".join(c if (c.isascii() and (c.isalnum() or c in "_-")) else "-" for c in origem)
    return comentario(
        f"Segredo de origem: {origem}. Criado sem versão: o valor nunca viaja pelo pacote."
    ) + "\n" + bloco("resource", ["google_secret_manager_secret", r.logical_name], [
        atributo("secret_id", nome[:255] or "segredo"),
        bloco_aninhado("replication", [
            bloco_aninhado("auto", [], nivel=2),
        ], nivel=1),
    ])


def _zona(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    origem = itens_do_componente(r.configuration)[0]
    return comentario(
        f"Zona de origem: {nome_de_zona(origem)}.\n"
        "Privada, visível só na VPC do pacote; os registros\n"
        "não viajam: exporte-os da origem e importe aqui."
    ) + "\n" + bloco("resource", ["google_dns_managed_zone", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, nome_de_zona(origem), maximo=63)),
        atributo("dns_name", nome_de_zona(origem) + "."),
        atributo("visibility", "private"),
        bloco_aninhado("private_visibility_config", [
            bloco_aninhado("networks", [
                atributo("network_url", referencia(f"google_compute_network.{REDE}.id"), nivel=3),
            ], nivel=2),
        ], nivel=1),
    ])


def _saida(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    """Cloud NAT para todas as subredes; a rota para a internet já vem com a VPC."""
    return comentario(TEXTO_SAIDA) + "\n" + bloco(
        "resource", ["google_compute_router", "roteador"], [
            atributo("name", nome_cloud(bp.project_name, "roteador")),
            atributo("region", bp.region),
            atributo("network", referencia(f"google_compute_network.{REDE}.id")),
        ]
    ) + "\n\n" + bloco("resource", ["google_compute_router_nat", "saida_nat"], [
        atributo("name", nome_cloud(bp.project_name, "nat")),
        atributo("router", referencia("google_compute_router.roteador.name")),
        atributo("region", bp.region),
        atributo("nat_ip_allocate_option", "AUTO_ONLY"),
        atributo("source_subnetwork_ip_ranges_to_nat", "ALL_SUBNETWORKS_ALL_IP_RANGES"),
    ])
