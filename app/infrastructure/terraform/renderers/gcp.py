"""Adaptador GCP."""

from __future__ import annotations

from app.infrastructure.terraform.hcl import (
    atributo,
    bloco,
    bloco_aninhado,
    comentario,
    referencia,
)
from app.infrastructure.terraform.renderers.base import (
    com_indice,
    instancias_banco,
    nome_banco,
    nome_cloud,
    quantidade_volumes,
    texto_banco,
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
    return bloco("resource", ["google_compute_subnetwork", r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, "subnet")),
        atributo("ip_cidr_range", str(r.configuration.get("cidr") or "10.0.0.0/24")),
        atributo("region", bp.region),
        atributo("network", referencia(f"google_compute_network.{REDE}.id")),
        # Sem isto, tráfego para APIs do Google sairia pela internet pública.
        atributo("private_ip_google_access", True),
    ])


def _compute(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    slug = r.logical_name.replace("_", "-")
    corpo = [
        atributo("count", int(cfg.get("instances") or 1)),
        atributo("name", referencia(
            f'"{nome_cloud(bp.project_name, slug)}-${{count.index}}"'
        )),
        atributo("machine_type", str(cfg.get("machine_type"))),
        atributo("zone", f"{bp.region}-a"),
        bloco_aninhado("boot_disk", [
            bloco_aninhado("initialize_params", [
                atributo("image", referencia("var.boot_image"), nivel=3),
            ], nivel=2),
        ], nivel=1),
        bloco_aninhado("network_interface", [
            atributo("subnetwork", referencia(
                f"google_compute_subnetwork.{SUBNET}.id"
            ), nivel=2),
        ], nivel=1),
        atributo("labels", {"environment": bp.environment, "managed_by": "oneshift"}),
    ]
    return comentario(
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total)."
    ) + "\n" + bloco("resource", ["google_compute_instance", r.logical_name], corpo)


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    n = quantidade_volumes(cfg)
    nome = nome_cloud(bp.project_name, r.logical_name)
    return comentario(texto_tamanho_disco(cfg)) + "\n" + bloco(
        "resource", ["google_compute_disk", r.logical_name],
        ([atributo("count", n)] if n > 1 else []) + [
        atributo("name", referencia(f'"{nome}-${{count.index}}"') if n > 1 else nome),
        atributo("type", str(cfg.get("disk_type") or "pd-standard")),
        atributo("zone", f"{bp.region}-a"),
        atributo("size", int(cfg.get("size_gb") or 10)),
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


def _banco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = int(cfg.get("vcpus") or 1)
    ha = bool(cfg.get("high_availability"))
    return comentario(
        texto_banco(cfg, vcpus) + "\n"
        "`deletion_protection` fica ligado; desligue conscientemente."
    ) + "\n" + bloco("resource", ["google_sql_database_instance", r.logical_name], [
        *([atributo("count", n)] if (n := instancias_banco(cfg)) > 1 else []),
        atributo("name", com_indice(nome_banco(bp, r), n)),
        atributo("database_version", var_versao(cfg)),
        atributo("region", bp.region),
        atributo("deletion_protection", True),
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
