"""Renderiza o blueprint em arquivos `.tf` para GCP.

Só este módulo sabe traduzir `resource_type` em recurso do provedor. Um tipo que
não está no mapa não é renderizado — vira `unsupported_items`. É a allowlist na
prática: não existe caminho de código que escreva um recurso desconhecido.

O pacote gerado **não aplica nada**. Não há `backend` remoto, `provisioner` nem
credencial: o cliente roda `terraform init` na máquina dele, com as credenciais
dele.
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.terraform.hcl import (
    arquivo,
    atributo,
    bloco,
    bloco_aninhado,
    comentario,
    referencia,
)
from app.schemas.terraform import (
    Premissa,
    RecursoTerraform,
    TerraformBlueprint,
    TipoRecurso,
)

# Versões fixas. `latest` num arquivo que o cliente aplica meses depois é como
# não versionar: o mesmo pacote geraria planos diferentes com o tempo.
_VERSAO_TERRAFORM = ">= 1.5.0"
_VERSAO_PROVIDER_GOOGLE = "~> 5.0"

# Recursos que sabemos escrever. Fora daqui, nada é renderizado.
_SERVICO_POR_TIPO: dict[TipoRecurso, str] = {
    TipoRecurso.NETWORK: "google_compute_network",
    TipoRecurso.SUBNET: "google_compute_subnetwork",
    TipoRecurso.COMPUTE: "google_compute_instance",
    TipoRecurso.OBJECT_STORAGE: "google_storage_bucket",
    TipoRecurso.MANAGED_DATABASE: "google_sql_database_instance",
}

def nome_cloud(*partes: str) -> str:
    """Nome aceito pelo GCP: `^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$`.

    Não é o mesmo que um identificador Terraform, e confundir os dois foi um bug
    real: `project_name` aceita `_` (válido em HCL) e o GCP recusa. O erro só
    aparecia no `terraform validate`, contra o schema do provider.
    """
    bruto = "-".join(str(p) for p in partes if p)
    limpo = "".join(
        c if (c.isascii() and (c.isalnum() or c == "-")) else "-"
        for c in bruto.lower()
    )
    limpo = "-".join(parte for parte in limpo.split("-") if parte)
    # Precisa começar com letra e terminar com letra ou dígito.
    while limpo and not (limpo[0].isascii() and limpo[0].isalpha()):
        limpo = limpo[1:]
    limpo = limpo[:63].rstrip("-")
    return limpo or "oneshift"


_ARQUIVOS = (
    "versions.tf",
    "providers.tf",
    "variables.tf",
    "main.tf",
    "outputs.tf",
    "terraform.tfvars.example",
    "README.md",
)


def renderizar(
    blueprint: TerraformBlueprint,
    *,
    procedencia: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Blueprint → `{nome do arquivo: conteúdo}`.

    ``procedencia`` é o bloco `premissas` da análise (preços, câmbio, confiança).
    Vai para o README porque um `.tf` parece preciso: sem dizer de quando é o
    preço e de quando é o câmbio, o cliente lê uma foto antiga como estado atual.
    """
    return {
        "versions.tf": _versions(),
        "providers.tf": _providers(blueprint),
        "variables.tf": _variables(blueprint),
        "main.tf": _main(blueprint),
        "outputs.tf": _outputs(blueprint),
        "terraform.tfvars.example": _tfvars(blueprint),
        "README.md": _readme(blueprint, procedencia or {}),
    }


def arquivos_esperados() -> tuple[str, ...]:
    return _ARQUIVOS


# ── arquivos ────────────────────────────────────────────────────────────


def _versions() -> str:
    return arquivo([
        comentario(
            "Versões fixas de propósito.\n"
            "`latest` num arquivo aplicado meses depois é o mesmo que não\n"
            "versionar: o plano mudaria sozinho."
        ),
        bloco("terraform", [], [
            atributo("required_version", _VERSAO_TERRAFORM),
            bloco_aninhado("required_providers", [
                atributo("google", referencia(
                    f'{{\n{"      "}source  = "hashicorp/google"\n'
                    f'      version = "{_VERSAO_PROVIDER_GOOGLE}"\n    }}'
                ), nivel=2),
            ]),
        ]),
    ])


def _providers(bp: TerraformBlueprint) -> str:
    return arquivo([
        comentario(
            "Sem credencial neste arquivo. O provider usa a autenticação do\n"
            "ambiente (`gcloud auth application-default login` ou\n"
            "GOOGLE_APPLICATION_CREDENTIALS). A plataforma nunca recebe,\n"
            "armazena nem gera credencial de nuvem."
        ),
        bloco("provider", ["google"], [
            atributo("project", referencia("var.project_id")),
            atributo("region", bp.region),
        ]),
    ])


def _variables(bp: TerraformBlueprint) -> str:
    secoes = [
        comentario(
            "Variáveis sem `default` são o que a análise NÃO soube responder.\n"
            "Preencher é obrigatório — o valor não foi adivinhado de propósito."
        ),
        bloco("variable", ["project_id"], [
            atributo("type", referencia("string")),
            atributo("description", "ID do projeto GCP onde os recursos serão criados."),
        ]),
    ]
    for v in bp.variables:
        corpo = [
            atributo("type", referencia(v.type)),
            atributo("description", v.description),
        ]
        if v.default is not None:
            corpo.append(atributo("default", v.default))
        if v.sensitive:
            corpo.append(atributo("sensitive", True))
        secoes.append(bloco("variable", [v.name], corpo))
    return arquivo(secoes)


def _main(bp: TerraformBlueprint) -> str:
    secoes: list[str] = [
        comentario(
            f"Infraestrutura derivada da análise de billing — projeto "
            f"'{bp.project_name}', ambiente '{bp.environment}'.\n"
            "Gerado pela OneShift. Revise antes de aplicar: veja as premissas\n"
            "no README, especialmente os tamanhos estimados."
        )
    ]
    for recurso in bp.resources:
        escrito = _recurso(recurso, bp)
        if escrito:
            secoes.append(escrito)
    return arquivo(secoes)


def _outputs(bp: TerraformBlueprint) -> str:
    secoes: list[str] = []
    if any(r.resource_type is TipoRecurso.NETWORK for r in bp.resources):
        secoes.append(bloco("output", ["network_id"], [
            atributo("value", referencia("google_compute_network.rede_principal.id")),
            atributo("description", "ID da VPC criada."),
        ]))
    for r in bp.resources:
        if r.resource_type is TipoRecurso.OBJECT_STORAGE:
            secoes.append(bloco("output", [f"{r.logical_name}_url"], [
                atributo("value", referencia(
                    f"google_storage_bucket.{r.logical_name}.url"
                )),
                atributo("description", "URL do bucket."),
            ]))
    if not secoes:
        secoes.append(comentario("Nenhum output: o pacote não criou recursos."))
    return arquivo(secoes)


def _tfvars(bp: TerraformBlueprint) -> str:
    linhas = [
        comentario(
            "Copie para `terraform.tfvars` e preencha.\n"
            "NUNCA versione o arquivo preenchido nem ponha credencial aqui."
        ),
        'project_id = "SEU-PROJETO-GCP"',
    ]
    for v in bp.variables:
        if v.default is None:
            linhas.append(f'# {v.description}\n{v.name} = ""')
    return arquivo(linhas)


# ── recursos ────────────────────────────────────────────────────────────


def _recurso(r: RecursoTerraform, bp: TerraformBlueprint) -> str | None:
    servico = _SERVICO_POR_TIPO.get(r.resource_type)
    if servico is None:
        return None
    construtor = {
        TipoRecurso.NETWORK: _rede,
        TipoRecurso.SUBNET: _subnet,
        TipoRecurso.COMPUTE: _compute,
        TipoRecurso.OBJECT_STORAGE: _bucket,
        TipoRecurso.MANAGED_DATABASE: _banco,
    }[r.resource_type]
    return construtor(r, bp, servico)


def _rede(r: RecursoTerraform, bp: TerraformBlueprint, servico: str) -> str:
    return bloco("resource", [servico, r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, "vpc")),
        # Subnets automáticas criariam uma faixa por região do mundo inteiro,
        # ignorando o CIDR que o usuário informou.
        atributo("auto_create_subnetworks", False),
        atributo("routing_mode", "REGIONAL"),
    ])


def _subnet(r: RecursoTerraform, bp: TerraformBlueprint, servico: str) -> str:
    cidr = str(r.configuration.get("cidr") or "10.0.0.0/16")
    return bloco("resource", [servico, r.logical_name], [
        atributo("name", nome_cloud(bp.project_name, "subnet")),
        atributo("ip_cidr_range", cidr),
        atributo("region", bp.region),
        atributo("network", referencia("google_compute_network.rede_principal.id")),
        # Sem isto, tráfego para APIs do Google sairia pela internet pública.
        atributo("private_ip_google_access", True),
    ])


def _compute(r: RecursoTerraform, bp: TerraformBlueprint, servico: str) -> str:
    cfg = r.configuration
    if cfg.get("disk_only"):
        return _disco(r, bp)

    instancias = int(cfg.get("instances") or 1)
    slug = r.logical_name.replace("_", "-")
    corpo = [
        atributo("count", instancias),
        # `referencia` e não `atributo` com string: aqui a interpolação é
        # desejada, e `_literal` escaparia `${` para literal — o que geraria
        # quatro máquinas com o nome "...-${count.index}".
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
                "google_compute_subnetwork.subnet_principal.id"
            ), nivel=2),
        ], nivel=1),
    ]
    if bp.environment:
        # `labels` em google_compute_instance é atributo de mapa, não bloco.
        corpo.append(atributo("labels", {
            "environment": bp.environment,
            "managed_by": "oneshift",
        }))

    origem = cfg.get("source_family")
    cabecalho = comentario(
        f"Derivado do pool de família '{origem}' "
        f"({cfg.get('vcpus')} vCPU, {cfg.get('ram_gb')} GB no total)."
    )
    return cabecalho + "\n" + bloco("resource", [servico, r.logical_name], corpo)


def _disco(r: RecursoTerraform, bp: TerraformBlueprint) -> str:
    cfg = r.configuration
    cabecalho = comentario(
        "TAMANHO ESTIMADO a partir do custo mensal — veja a premissa no README.\n"
        "Confira contra o uso real antes de aplicar."
    )
    corpo = [
        atributo("name", nome_cloud(bp.project_name, r.logical_name)),
        atributo("type", str(cfg.get("disk_type") or "pd-standard")),
        atributo("zone", f"{bp.region}-a"),
        atributo("size", int(cfg.get("size_gb") or 10)),
    ]
    return cabecalho + "\n" + bloco(
        "resource", ["google_compute_disk", r.logical_name], corpo
    )


def _bucket(r: RecursoTerraform, bp: TerraformBlueprint, servico: str) -> str:
    cfg = r.configuration
    publico = bool(cfg.get("public_access"))
    cabecalho = comentario(
        f"Volume estimado: ~{cfg.get('estimated_gb')} GB (derivado do custo).\n"
        "O bucket não tem tamanho declarado — o número é só referência."
    )
    corpo = [
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
    ]
    return cabecalho + "\n" + bloco("resource", [servico, r.logical_name], corpo)


def _banco(r: RecursoTerraform, bp: TerraformBlueprint, servico: str) -> str:
    cfg = r.configuration
    vcpus = int(cfg.get("vcpus") or 1)
    ha = bool(cfg.get("high_availability"))
    cabecalho = comentario(
        f"vCPU ESTIMADO ({vcpus}) a partir do custo mensal — veja o README.\n"
        "Motor e versão NÃO foram deduzidos: o billing não os informa.\n"
        "Preencha `database_version` antes de aplicar.\n"
        "`deletion_protection` fica ligado; desligue conscientemente."
    )
    corpo = [
        atributo("name", nome_cloud(bp.project_name, "db")),
        atributo("database_version", referencia("var.database_version")),
        atributo("region", bp.region),
        atributo("deletion_protection", True),
        bloco_aninhado("settings", [
            atributo("tier", f"db-custom-{vcpus}-{vcpus * 3840}", nivel=2),
            atributo("availability_type", "REGIONAL" if ha else "ZONAL", nivel=2),
            bloco_aninhado("ip_configuration", [
                atributo("ipv4_enabled", False, nivel=3),
                atributo("private_network", referencia(
                    "google_compute_network.rede_principal.id"
                ), nivel=3),
            ], nivel=2),
        ], nivel=1),
    ]
    return cabecalho + "\n" + bloco("resource", [servico, r.logical_name], corpo)


# ── README ──────────────────────────────────────────────────────────────

# Cabeçalho + linha em branco. Abaixo disso a seção não tem conteúdo nenhum.
_SO_CABECALHO = 2

_ROTULO_CATEGORIA = {
    "derivacao": "Calculado",
    "estimativa": "Estimado",
    "sem_base": "Não deduzível",
    "escolha_usuario": "Informado por você",
}


def _readme(bp: TerraformBlueprint, procedencia: dict[str, Any]) -> str:
    partes = [
        f"# Terraform — {bp.project_name}",
        "",
        f"Gerado pela OneShift a partir da análise de billing. "
        f"Destino: **{bp.provider.value.upper()}**, região **{bp.region}**, "
        f"ambiente **{bp.environment}**.",
        "",
        "## Antes de aplicar",
        "",
        "Este pacote **descreve** infraestrutura; ele não provisiona nada. "
        "Nenhuma credencial de nuvem foi usada para gerá-lo e nenhuma está aqui "
        "dentro.",
        "",
        "```bash",
        "terraform init",
        "terraform plan    # leia o plano inteiro antes de aplicar",
        "```",
        "",
        _secao_premissas(bp.assumptions),
    ]
    if bp.unsupported_items:
        partes += [
            "## O que ficou de fora",
            "",
            "Estes custos aparecem na sua fatura e **não** viraram recurso aqui:",
            "",
            *[f"- {item}" for item in bp.unsupported_items],
            "",
        ]
    if bp.warnings:
        partes += ["## Avisos", "", *[f"- {a}" for a in bp.warnings], ""]

    partes += [_secao_procedencia(procedencia)]
    partes += [
        "## Sobre custo",
        "",
        "Este pacote **não declara economia**. A cotação da análise usa uma "
        "região de referência por provedor, que pode não ser a região escolhida "
        "aqui — publicar um número seria comparar preços de lugares diferentes.",
        "",
    ]
    return "\n".join(partes).rstrip() + "\n"


def _secao_premissas(premissas: list[Premissa]) -> str:
    if not premissas:
        return ""
    linhas = [
        "## Premissas",
        "",
        "Cada valor do pacote, com a origem. **Estimado** significa que o número "
        "veio de uma conta sobre custo, não de uma medição — confira contra o uso "
        "real.",
        "",
        "| Campo | Origem | Valor | Base |",
        "|---|---|---|---|",
    ]
    for p in premissas:
        rotulo = _ROTULO_CATEGORIA.get(p.categoria.value, p.categoria.value)
        base = p.base.replace("|", "\\|").replace("\n", " ")
        linhas.append(f"| `{p.campo}` | {rotulo} | {p.valor or '—'} | {base} |")
    linhas.append("")
    return "\n".join(linhas)


def _secao_procedencia(premissas: dict[str, Any]) -> str:
    """De quando são os dados que sustentam este pacote.

    Sem isto o cliente lê uma foto antiga como estado atual — e o `.tf` não tem
    como dizer sozinho que o preço usado tem três meses.
    """
    if not premissas:
        return ""
    linhas = ["## Procedência dos dados", ""]

    fx = premissas.get("fx") or {}
    if fx:
        data = fx.get("data") or "sem data"
        linhas.append(
            f"- **Câmbio:** {fx.get('par')} = {fx.get('taxa')} "
            f"(fonte: {fx.get('fonte')}, {data})"
        )
    confianca = premissas.get("confianca_analise") or {}
    if confianca:
        linhas.append(
            f"- **Confiança da análise:** {confianca.get('nivel')} "
            f"(razão de custo com preço oficial: "
            f"{confianca.get('razao_custo_confiavel')})"
        )
        for motivo in confianca.get("rebaixada_por") or []:
            linhas.append(f"  - rebaixada por: {motivo}")
    fonte = premissas.get("fonte_precos")
    if fonte:
        linhas.append(f"- **Preços:** {fonte}")
    for sugestao in premissas.get("sugestoes_backend") or []:
        linhas.append(f"- **{sugestao.get('titulo')}** — {sugestao.get('detalhe')}")

    linhas.append("")
    return "\n".join(linhas) if len(linhas) > _SO_CABECALHO else ""
