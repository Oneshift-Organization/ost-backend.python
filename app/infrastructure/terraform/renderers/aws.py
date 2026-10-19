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

from typing import Any

from app.infrastructure.terraform.hcl import (
    atributo,
    bloco,
    bloco_aninhado,
    comentario,
    referencia,
)
from app.infrastructure.terraform.renderers.base import (
    ajustar_tamanho,
    clusters_do_pacote,
    com_indice,
    disco_raiz,
    disco_ssd,
    faixa_de_portas,
    fontes,
    identificador,
    instancias_banco,
    itens_do_componente,
    nome_banco,
    nome_cloud,
    nome_de_repositorio,
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
ZONAS = "disponiveis"

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
        variaveis = [
            VariavelTerraform(
                name="ami_id",
                description=(
                    "AMI das instâncias. O billing não informa a imagem em uso — "
                    "consulte a AMI da sua distribuição na região escolhida."
                ),
            )
        ]
        if clusters_do_pacote(bp):
            variaveis += variavel_de_versao_do_kubernetes(
                bp, aceita_versao_maior=True, exemplo="1.30"
            )
            variaveis += [
                VariavelTerraform(
                    name="eks_cluster_role_arn",
                    description=(
                        "ARN do papel IAM do cluster EKS (AmazonEKSClusterPolicy). O pacote "
                        "não cria IAM: use o papel que a sua conta aprova."
                    ),
                ),
                VariavelTerraform(
                    name="eks_node_role_arn",
                    description=(
                        "ARN do papel IAM dos nós (AmazonEKSWorkerNodePolicy, "
                        "AmazonEKS_CNI_Policy, AmazonEC2ContainerRegistryReadOnly)."
                    ),
                ),
            ]
        return variaveis

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        if not any(r.configuration.get("mirror_of") for r in bp.resources):
            return []
        # A letra da zona muda de região para região (ap-northeast-1 não tem `b`
        # para contas novas): as subredes espelhadas pegam a zona pela posição.
        return [
            comentario("Zonas disponíveis na região, para as subredes espelhadas da origem.")
            + "\n"
            + bloco("data", ["aws_availability_zones", ZONAS], [atributo("state", "available")])
        ]

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
            TipoRecurso.SECURITY_GROUP: _grupo,
            TipoRecurso.KUBERNETES_CLUSTER: _cluster,
            TipoRecurso.CONTAINER_REGISTRY: _registro,
            TipoRecurso.VAULT: _segredos,
            TipoRecurso.DNS_ZONE: _zona,
        }.get(r.resource_type, lambda *_: None)(r, bp)


def _rede(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    return bloco("resource", ["aws_vpc", r.logical_name], [
        atributo("cidr_block", str(r.configuration.get("cidr") or "10.0.0.0/16")),
        atributo("enable_dns_hostnames", True),
        atributo("enable_dns_support", True),
        atributo("tags", {"Name": nome_cloud(bp.project_name, "vpc")}),
    ])


def _subnet(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    espelho = texto_espelho(r.configuration)
    if espelho is not None:
        return comentario(espelho) + "\n" + bloco("resource", ["aws_subnet", r.logical_name], [
            atributo("vpc_id", referencia(f"aws_vpc.{REDE}.id")),
            atributo("cidr_block", str(r.configuration.get("cidr"))),
            atributo("availability_zone", referencia(
                f"element(data.aws_availability_zones.{ZONAS}.names, "
                f"{int(r.configuration.get('zone_index') or 0)})"
            )),
            atributo("map_public_ip_on_launch", bool(r.configuration.get("public_access"))),
            atributo("tags", {"Name": nome_cloud(bp.project_name, r.logical_name)}),
        ])
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
    texto = (
        f"Derivado do pool de família '{cfg.get('source_family')}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total)."
    )
    corpo = [
        atributo("count", int(cfg.get("instances") or 1)),
        atributo("ami", referencia("var.ami_id")),
        atributo("instance_type", str(cfg.get("machine_type"))),
        atributo("subnet_id", referencia(f"aws_subnet.{cfg.get('subnet') or SUBNET}.id")),
        *_grupos_da_instancia(cfg),
        atributo("tags", referencia(
            f'{{\n    Name = "{nome_cloud(bp.project_name, slug)}-${{count.index}}"\n  }}'
        )),
    ]
    # st1 e sc1 não podem ser disco de boot: o raiz é gp3, qualquer que seja a
    # classe na origem. 8 GB é o menor raiz das AMIs Linux comuns.
    raiz = disco_raiz(cfg, minimo=8, maximo=16_384, tipo="gp3")
    if raiz is not None:
        tamanho, linha = raiz
        texto += "\n" + linha
        corpo.append(bloco_aninhado("root_block_device", [
            atributo("volume_size", tamanho, nivel=2),
            atributo("volume_type", "gp3", nivel=2),
            atributo("encrypted", True, nivel=2),
        ], nivel=1))
    return comentario(texto) + "\n" + bloco("resource", ["aws_instance", r.logical_name], corpo)


_PROTOCOLO_AWS = {"tcp": "tcp", "udp": "udp", "icmp": "icmp", "todos": "-1"}


def _grupos_da_instancia(cfg: dict[str, Any]) -> list[str]:
    grupos = cfg.get("security_groups") or []
    if not grupos:
        return []
    return [atributo("vpc_security_group_ids", referencia(
        "[" + ", ".join(f"aws_security_group.{g}.id" for g in grupos) + "]"
    ))]


def _grupo(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    """Grupo espelhado, uma regra de entrada por origem, saída liberada.

    Regra como recurso separado (`aws_vpc_security_group_ingress_rule`), e não
    bloco `ingress`: dois grupos que se referenciam formariam um ciclo.
    """
    cfg = r.configuration
    nome = nome_cloud(bp.project_name, r.logical_name)
    grupo = f"aws_security_group.{r.logical_name}.id"
    partes = [comentario(texto_grupo(cfg)) + "\n" + bloco(
        "resource", ["aws_security_group", r.logical_name], [
            atributo("name", nome),
            atributo("description", "Espelhado da origem pela OneShift"),
            atributo("vpc_id", referencia(f"aws_vpc.{REDE}.id")),
            atributo("tags", {"Name": nome}),
        ])]
    n = 0
    for regra in cfg.get("rules") or []:
        protocolo = _PROTOCOLO_AWS.get(str(regra.get("protocol")), "-1")
        portas: list[str] = []
        if protocolo == "icmp":
            portas = [atributo("from_port", -1), atributo("to_port", -1)]
        elif protocolo != "-1":
            faixa = faixa_de_portas(regra) or (0, 65535)
            portas = [atributo("from_port", faixa[0]), atributo("to_port", faixa[1])]
        for tipo, valor in fontes(regra):
            n += 1
            origem = (
                atributo("cidr_ipv4", valor)
                if tipo == "cidr"
                else atributo(
                    "referenced_security_group_id", referencia(f"aws_security_group.{valor}.id")
                )
            )
            partes.append(bloco(
                "resource", ["aws_vpc_security_group_ingress_rule", f"{r.logical_name}_{n}"],
                [atributo("security_group_id", referencia(grupo)),
                 atributo("ip_protocol", protocolo), *portas, origem],
            ))
    partes.append(bloco(
        "resource", ["aws_vpc_security_group_egress_rule", f"{r.logical_name}_saida"], [
            atributo("security_group_id", referencia(grupo)),
            atributo("ip_protocol", "-1"),
            atributo("cidr_ipv4", "0.0.0.0/0"),
        ]))
    return "\n\n".join(partes)


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    # gp3 e st1 são os equivalentes diretos do que o pool classificou como
    # SSD e HDD na origem.
    tipo = "gp3" if disco_ssd(cfg) else "st1"
    tamanho, nota = ajustar_tamanho(
        int(cfg.get("size_gb") or 10),
        minimo=125 if tipo == "st1" else 1,
        maximo=16_384,
        tipo=tipo,
    )
    n = quantidade_volumes(cfg)
    nome = nome_cloud(bp.project_name, r.logical_name)
    return comentario(texto_tamanho_disco(cfg) + nota) + "\n" + bloco(
        "resource", ["aws_ebs_volume", r.logical_name],
        ([atributo("count", n)] if n > 1 else []) + [
        atributo("availability_zone", f"{bp.region}a"),
        atributo("size", tamanho),
        atributo("type", tipo),
        atributo("encrypted", True),
        atributo("tags", referencia(f'{{\n    Name = "{nome}-${{count.index}}"\n  }}')
                 if n > 1 else {"Name": nome}),
    ])


def _bucket(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    nome = nome_cloud(bp.project_name, r.logical_name, maximo=_MAX_BUCKET)

    partes = [
        comentario(
            texto_volume_bucket(cfg)
            + "`bucket_prefix` e não `bucket`: nome de S3 é GLOBAL, e um nome fixo\n"
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
    subredes = [str(s) for s in cfg.get("db_subnets") or []]
    grupo = f"{r.logical_name}_subredes"
    rede = []
    antes = ""
    if subredes:
        antes = comentario(
            "Subredes espelhadas de onde o banco estava na origem (duas zonas ou mais)."
        ) + "\n" + bloco("resource", ["aws_db_subnet_group", grupo], [
            atributo("name", nome_cloud(bp.project_name, r.logical_name, "subredes")),
            atributo("subnet_ids", referencia(
                "[" + ", ".join(f"aws_subnet.{s}.id" for s in subredes) + "]"
            )),
        ]) + "\n\n"
        rede = [atributo("db_subnet_group_name", referencia(f"aws_db_subnet_group.{grupo}.name"))]
    return antes + comentario(
        texto_banco(cfg, vcpus) + "\n"
        f"Traduzido para a classe {classe}, a mais próxima acima.\n"
        "`deletion_protection` fica ligado; desligue conscientemente."
    ) + "\n" + bloco("resource", ["aws_db_instance", r.logical_name], [
        *([atributo("count", n)] if (n := instancias_banco(cfg)) > 1 else []),
        atributo("identifier", com_indice(nome_banco(bp, r), n)),
        *_motor(cfg),
        atributo("instance_class", classe),
        atributo("allocated_storage", 100),
        atributo("storage_encrypted", True),
        atributo("multi_az", ha),
        *rede,
        *_grupos_da_instancia(cfg),
        atributo("publicly_accessible", False),
        atributo("deletion_protection", True),
        atributo("skip_final_snapshot", False),
        atributo("final_snapshot_identifier", com_indice(nome_banco(bp, r, "final"), n)),
    ])


# Família do motor medido → `engine` do RDS. Aurora vira o motor base: o pacote
# gera `aws_db_instance`, não cluster Aurora.
_MOTOR_RDS = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "sqlserver": "sqlserver-se",
    "oracle": "oracle-se2",
}


def _motor(cfg: dict) -> list[str]:
    """Motor medido vira literal e a variável fica só com a versão; sem medida,
    a variável segue carregando o motor, como no caminho agregado."""
    motor = _MOTOR_RDS.get(str(cfg.get("engine_family") or ""))
    if motor is None:
        return [atributo("engine", referencia("var.database_version"))]
    return [atributo("engine", motor), atributo("engine_version", var_versao(cfg))]


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


# ── Kubernetes e serviços gerenciados do escopo (itens 15 e 19) ─────────────


def _subredes_do_pacote(bp: TerraformBlueprint) -> str:
    """O EKS pede subredes em duas zonas: todas as do pacote (o espelho cria por zona)."""
    nomes = [r.logical_name for r in bp.resources if r.resource_type is TipoRecurso.SUBNET]
    return "[" + ", ".join(f"aws_subnet.{n}.id" for n in nomes or [SUBNET]) + "]"


def _cluster(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    subredes = referencia(_subredes_do_pacote(bp))
    partes = [
        comentario(
            texto_cluster(cfg) + "\nOs papéis IAM do cluster e dos nós vêm de variável: o "
            "pacote não cria IAM."
        )
        + "\n"
        + bloco("resource", ["aws_eks_cluster", r.logical_name], [
            atributo("name", nome_cloud(bp.project_name, cfg.get("source_name"), maximo=100)),
            atributo("role_arn", referencia("var.eks_cluster_role_arn")),
            atributo("version", referencia("var.kubernetes_version")),
            bloco_aninhado("vpc_config", [
                atributo("subnet_ids", subredes, nivel=2),
                atributo("endpoint_private_access", True, nivel=2),
            ], nivel=1),
        ])
    ]
    for g in cfg.get("node_pools") or []:
        n = max(1, int(g.get("instances") or 1))
        partes.append(
            bloco(
                "resource",
                ["aws_eks_node_group", f"{r.logical_name}_{identificador(str(g.get('name')))}"],
                [
                    atributo("cluster_name", referencia(f"aws_eks_cluster.{r.logical_name}.name")),
                    atributo("node_group_name", nome_cloud(g.get("name"), maximo=63)),
                    atributo("node_role_arn", referencia("var.eks_node_role_arn")),
                    atributo("subnet_ids", subredes),
                    atributo("instance_types", [str(g.get("machine_type"))]),
                    bloco_aninhado("scaling_config", [
                        atributo("desired_size", n, nivel=2),
                        atributo("min_size", n, nivel=2),
                        atributo("max_size", n, nivel=2),
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
    ) + "\n" + bloco("resource", ["aws_ecr_repository", r.logical_name], [
        atributo("name", nome_de_repositorio(origem)),
        bloco_aninhado("image_scanning_configuration", [
            atributo("scan_on_push", True, nivel=2),
        ], nivel=1),
    ])


def _segredos(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    origem = itens_do_componente(r.configuration)[0]
    nome = "".join(c if (c.isascii() and (c.isalnum() or c in "/_+=.@-")) else "-" for c in origem)
    return comentario(
        f"Segredo de origem: {origem}. Criado vazio: o valor nunca viaja pelo pacote."
    ) + "\n" + bloco("resource", ["aws_secretsmanager_secret", r.logical_name], [
        atributo("name", nome[:512] or "segredo"),
    ])


def _zona(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    origem = itens_do_componente(r.configuration)[0]
    return comentario(
        f"Zona de origem: {nome_de_zona(origem)}.\n"
        "Privada, ligada à VPC do pacote; os registros não\n"
        "viajam: exporte-os da origem e importe aqui."
    ) + "\n" + bloco("resource", ["aws_route53_zone", r.logical_name], [
        atributo("name", nome_de_zona(origem)),
        bloco_aninhado("vpc", [
            atributo("vpc_id", referencia(f"aws_vpc.{REDE}.id"), nivel=2),
        ], nivel=1),
    ])
