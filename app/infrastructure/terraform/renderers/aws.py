"""Adaptador AWS.

Duas diferenças que importam em relação ao GCP:

- **Nome de bucket S3 é global.** Dois clientes com o mesmo `project_name`
  colidiriam, então o nome sai como prefixo e o Terraform completa com sufixo
  aleatório (`bucket_prefix`). Nome fixo daria um erro que só aparece no apply.
- **Bloqueio de acesso público é recurso separado.** No GCP é atributo do
  bucket; aqui, sem o `aws_s3_bucket_public_access_block`, o bucket nasce
  sujeito a ACL pública.
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

# S3: nome final tem 3 a 63 caracteres, mas `bucket_prefix` é limitado a 37 pelo
# provider — ele reserva o resto para o sufixo aleatório. O hífen que separa
# prefixo e sufixo conta, daí 36 para o nome.
_MAX_BUCKET = 36


class AdaptadorAWS:
    provider_nome = "aws"
    provider_source = "hashicorp/aws"
    provider_version = "~> 5.0"

    def provider_bloco(self, bp: TerraformBlueprint) -> str:
        return bloco("provider", ["aws"], [
            atributo("region", bp.region),
            bloco_aninhado("default_tags", [
                atributo("tags", {
                    "environment": bp.environment,
                    "managed_by": "oneshift",
                }, nivel=2),
            ], nivel=1),
        ])

    def variaveis_extras(self, bp: TerraformBlueprint) -> list[VariavelTerraform]:
        return [
            VariavelTerraform(
                name="ami_id",
                description=(
                    "AMI das instâncias. O billing não informa a imagem em uso — "
                    "consulte a AMI da sua distribuição na região escolhida."
                ),
            )
        ]

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        return []

    def outputs(self, bp: TerraformBlueprint) -> list[str]:
        saidas = []
        if any(r.resource_type is TipoRecurso.NETWORK for r in bp.resources):
            saidas.append(bloco("output", ["vpc_id"], [
                atributo("value", referencia(f"aws_vpc.{REDE}.id")),
                atributo("description", "ID da VPC criada."),
            ]))
        for r in bp.resources:
            if r.resource_type is TipoRecurso.OBJECT_STORAGE:
                saidas.append(bloco("output", [f"{r.logical_name}_nome"], [
                    atributo("value", referencia(f"aws_s3_bucket.{r.logical_name}.id")),
                    atributo("description", "Nome final do bucket (com sufixo)."),
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
    return bloco("resource", ["aws_vpc", r.logical_name], [
        atributo("cidr_block", str(r.configuration.get("cidr") or "10.0.0.0/16")),
        atributo("enable_dns_hostnames", True),
        atributo("enable_dns_support", True),
        atributo("tags", {"Name": nome_cloud(bp.project_name, "vpc")}),
    ])


def _subnet(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    return bloco("resource", ["aws_subnet", r.logical_name], [
        atributo("vpc_id", referencia(f"aws_vpc.{REDE}.id")),
        atributo("cidr_block", str(r.configuration.get("cidr") or "10.0.0.0/24")),
        atributo("availability_zone", f"{bp.region}a"),
        # Sem isto, toda instância nasceria com IP público.
        atributo("map_public_ip_on_launch", bool(r.configuration.get("public_access"))),
        atributo("tags", {"Name": nome_cloud(bp.project_name, "subnet")}),
    ])


def _compute(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    slug = r.logical_name.replace("_", "-")
    return comentario(
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total)."
    ) + "\n" + bloco("resource", ["aws_instance", r.logical_name], [
        atributo("count", int(cfg.get("instances") or 1)),
        atributo("ami", referencia("var.ami_id")),
        atributo("instance_type", str(cfg.get("machine_type"))),
        atributo("subnet_id", referencia(f"aws_subnet.{SUBNET}.id")),
        atributo("tags", referencia(
            f'{{\n    Name = "{nome_cloud(bp.project_name, slug)}-${{count.index}}"\n  }}'
        )),
    ])


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    # gp3 e st1 são os equivalentes diretos do que o pool classificou como
    # SSD e HDD na origem.
    tipo = "gp3" if str(cfg.get("disk_type", "")).endswith("ssd") else "st1"
    return comentario(
        "TAMANHO ESTIMADO a partir do custo mensal — veja a premissa no README.\n"
        "Confira contra o uso real antes de aplicar."
    ) + "\n" + bloco("resource", ["aws_ebs_volume", r.logical_name], [
        atributo("availability_zone", f"{bp.region}a"),
        atributo("size", int(cfg.get("size_gb") or 10)),
        atributo("type", tipo),
        atributo("encrypted", True),
        atributo("tags", {"Name": nome_cloud(bp.project_name, r.logical_name)}),
    ])


def _bucket(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    nome = nome_cloud(bp.project_name, r.logical_name, maximo=_MAX_BUCKET)

    partes = [
        comentario(
            f"Volume estimado: ~{cfg.get('estimated_gb')} GB (derivado do custo).\n"
            "`bucket_prefix` e não `bucket`: nome de S3 é GLOBAL, e um nome fixo\n"
            "colidiria com outro cliente — erro que só apareceria no apply."
        ),
        bloco("resource", ["aws_s3_bucket", r.logical_name], [
            atributo("bucket_prefix", f"{nome}-"),
        ]),
        bloco("resource", ["aws_s3_bucket_versioning", f"{r.logical_name}_versao"], [
            atributo("bucket", referencia(f"aws_s3_bucket.{r.logical_name}.id")),
            bloco_aninhado("versioning_configuration", [
                atributo("status", "Enabled", nivel=2),
            ], nivel=1),
        ]),
    ]
    if not publico:
        partes.append(
            comentario(
                "Recurso separado, ao contrário do GCP: sem ele o bucket nasce\n"
                "sujeito a ACL pública."
            )
            + "\n"
            + bloco(
                "resource",
                ["aws_s3_bucket_public_access_block", f"{r.logical_name}_privado"],
                [
                    atributo("bucket", referencia(f"aws_s3_bucket.{r.logical_name}.id")),
                    atributo("block_public_acls", True),
                    atributo("block_public_policy", True),
                    atributo("ignore_public_acls", True),
                    atributo("restrict_public_buckets", True),
                ],
            )
        )
    return "\n\n".join(partes)


def _banco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    vcpus = int(cfg.get("vcpus") or 1)
    ha = bool(cfg.get("high_availability"))
    # RDS cobra por classe de instância, não por vCPU avulso. `m6g` é a linha
    # de propósito geral; o número de vCPU vira o tamanho mais próximo.
    classe = _classe_rds(vcpus)
    return comentario(
        f"vCPU ESTIMADO ({vcpus}) a partir do custo mensal — veja o README.\n"
        f"Traduzido para a classe {classe}, a mais próxima acima.\n"
        "Motor e versão NÃO foram deduzidos: o billing não os informa.\n"
        "Preencha `database_version` antes de aplicar.\n"
        "`deletion_protection` fica ligado; desligue conscientemente."
    ) + "\n" + bloco("resource", ["aws_db_instance", r.logical_name], [
        atributo("identifier", nome_cloud(bp.project_name, "db")),
        atributo("engine", referencia("var.database_version")),
        atributo("instance_class", classe),
        atributo("allocated_storage", 100),
        atributo("storage_encrypted", True),
        atributo("multi_az", ha),
        atributo("publicly_accessible", False),
        atributo("deletion_protection", True),
        atributo("skip_final_snapshot", False),
        atributo("final_snapshot_identifier", nome_cloud(bp.project_name, "db", "final")),
    ])


_CLASSES_RDS = ((2, "db.m6g.large"), (4, "db.m6g.xlarge"), (8, "db.m6g.2xlarge"),
                (16, "db.m6g.4xlarge"), (32, "db.m6g.8xlarge"))


def _classe_rds(vcpus: int) -> str:
    """A menor classe que comporta o vCPU estimado.

    Arredondar para baixo entregaria um banco menor que o atual, que é o tipo de
    erro que só aparece em produção sob carga.
    """
    for limite, classe in _CLASSES_RDS:
        if vcpus <= limite:
            return classe
    return "db.m6g.16xlarge"
