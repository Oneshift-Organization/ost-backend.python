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

from app.infrastructure.terraform.hcl import (
    atributo,
    bloco,
    bloco_aninhado,
    comentario,
    referencia,
)
from app.infrastructure.terraform.renderers.base import nome_cloud
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
        if any(r.resource_type is TipoRecurso.MANAGED_DATABASE for r in bp.resources):
            variaveis.append(
                VariavelTerraform(
                    name="db_admin_password",
                    description=(
                        "Senha do administrador do Autonomous Database. A "
                        "plataforma não gera nem armazena senha — escolha uma e "
                        "guarde no seu cofre."
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
    return bloco("resource", ["oci_core_subnet", r.logical_name], [
        _compartimento(),
        atributo("vcn_id", referencia(f"oci_core_vcn.{REDE}.id")),
        atributo("cidr_block", str(r.configuration.get("cidr") or "10.0.0.0/24")),
        atributo("display_name", nome_cloud(bp.project_name, "subnet")),
        # `true` aqui deixaria toda instância sem IP público mesmo quando o
        # usuário pediu acesso público — a lógica é invertida no nome.
        atributo(
            "prohibit_public_ip_on_vnic",
            not bool(r.configuration.get("public_access")),
        ),
    ])


def _compute(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    vcpus = max(1, int(float(cfg.get("vcpus") or 1)))
    ram = float(cfg.get("ram_gb") or 0) or vcpus * _GB_POR_VCPU
    instancias = int(cfg.get("instances") or 1)
    # A capacidade do pool é o TOTAL; o shape é por instância.
    por_instancia = max(1, vcpus // max(1, instancias))
    ram_instancia = max(1.0, round(ram / max(1, instancias), 1))
    slug = r.logical_name.replace("_", "-")

    return comentario(
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total).\n"
        "Shape flexível: a OCI cobra OCPU e memória separadamente, então o\n"
        "dimensionamento vai em `shape_config` em vez de um nome fechado."
    ) + "\n" + bloco("resource", ["oci_core_instance", r.logical_name], [
        atributo("count", instancias),
        _compartimento(),
        atributo("availability_domain", referencia("var.availability_domain")),
        atributo("display_name", referencia(
            f'"{nome_cloud(bp.project_name, slug)}-${{count.index}}"'
        )),
        atributo("shape", "VM.Standard.E4.Flex"),
        bloco_aninhado("shape_config", [
            atributo("ocpus", por_instancia, nivel=2),
            atributo("memory_in_gbs", ram_instancia, nivel=2),
        ], nivel=1),
        bloco_aninhado("create_vnic_details", [
            atributo("subnet_id", referencia(f"oci_core_subnet.{SUBNET}.id"), nivel=2),
            atributo("assign_public_ip", False, nivel=2),
        ], nivel=1),
        bloco_aninhado("source_details", [
            atributo("source_type", "image", nivel=2),
            atributo("source_id", referencia("var.image_ocid"), nivel=2),
        ], nivel=1),
    ])


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    return comentario(
        "TAMANHO ESTIMADO a partir do custo mensal — veja a premissa no README.\n"
        "Confira contra o uso real antes de aplicar."
    ) + "\n" + bloco("resource", ["oci_core_volume", r.logical_name], [
        _compartimento(),
        atributo("availability_domain", referencia("var.availability_domain")),
        atributo("display_name", nome_cloud(bp.project_name, r.logical_name)),
        atributo("size_in_gbs", int(cfg.get("size_gb") or 50)),
    ])


def _bucket(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    return comentario(
        f"Volume estimado: ~{cfg.get('estimated_gb')} GB (derivado do custo).\n"
        "O bucket não tem tamanho declarado — o número é só referência."
    ) + "\n" + bloco("resource", ["oci_objectstorage_bucket", r.logical_name], [
        _compartimento(),
        atributo("namespace", referencia("var.object_storage_namespace")),
        atributo("name", nome_cloud(bp.project_name, r.logical_name)),
        atributo("access_type", "ObjectRead" if publico else "NoPublicAccess"),
        atributo("versioning", "Enabled"),
    ])


def _banco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = max(1, int(cfg.get("vcpus") or 1))
    return comentario(
        f"vCPU ESTIMADO ({vcpus}) a partir do custo mensal — veja o README.\n"
        "Autonomous Database: a OCI cobra por OCPU, que casa com o vCPU derivado.\n"
        "A senha de admin NÃO é gerada aqui — preencha `db_admin_password`."
    ) + "\n" + bloco(
        "resource", ["oci_database_autonomous_database", r.logical_name], [
            _compartimento(),
            atributo("db_name", nome_cloud(
                bp.project_name, maximo=14, separador=""
            ).upper()),
            atributo("display_name", nome_cloud(bp.project_name, "db")),
            atributo("cpu_core_count", vcpus),
            atributo("data_storage_size_in_tbs", 1),
            atributo("admin_password", referencia("var.db_admin_password")),
            atributo("is_auto_scaling_enabled", True),
        ],
    )
