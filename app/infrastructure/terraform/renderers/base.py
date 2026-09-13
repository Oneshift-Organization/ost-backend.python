"""O que é igual em toda nuvem: montagem dos arquivos, README, tfvars.

Cada provedor implementa `AdaptadorNuvem` e cuida só do que é dele — o bloco
`provider`, a tradução de `resource_type` em recurso nativo, e as regras de nome
de cada serviço. O resto (ordem dos arquivos, tabela de premissas, procedência,
a ressalva sobre economia) é idêntico, e duplicar isso em quatro lugares é como
se perde a coerência do pacote.

A estrutura de arquivos também é a mesma em todas: quem já viu um pacote nosso
de GCP encontra as mesmas coisas nos mesmos lugares num de AWS.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.infrastructure.terraform.hcl import (
    arquivo,
    atributo,
    bloco,
    comentario,
    referencia,
)
from app.schemas.terraform import (
    Premissa,
    RecursoTerraform,
    TerraformBlueprint,
    VariavelTerraform,
)

# Versão mínima do CLI. Fixa por provedor seria pior: o pacote é um só.
VERSAO_TERRAFORM = ">= 1.5.0"

ARQUIVOS = (
    "versions.tf",
    "providers.tf",
    "variables.tf",
    "main.tf",
    "outputs.tf",
    "terraform.tfvars.example",
    "README.md",
)


class AdaptadorNuvem(Protocol):
    """O que cada nuvem precisa saber responder."""

    provider_nome: str
    """Nome local do provider no HCL — `google`, `aws`, `azurerm`, `oci`."""

    provider_source: str
    """Endereço no registry — `hashicorp/google`, `oracle/oci`."""

    provider_version: str

    def provider_bloco(self, bp: TerraformBlueprint) -> str:
        """Conteúdo do `provider "<nome>" { ... }`. Nunca credencial."""
        ...

    def recurso(self, r: RecursoTerraform, bp: TerraformBlueprint) -> str | None:
        """Um recurso do blueprint em HCL, ou ``None`` se a nuvem não o suporta.

        Devolver ``None`` é a allowlist na prática: o tipo vira
        `unsupported_items` em vez de um bloco que ninguém revisou.
        """
        ...

    def variaveis_extras(self, bp: TerraformBlueprint) -> list[VariavelTerraform]:
        """O que esta nuvem exige e as outras não — `compartment_id` na OCI,
        `resource_group` na Azure."""
        ...

    def outputs(self, bp: TerraformBlueprint) -> list[str]:
        ...

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        """Recursos que a nuvem exige antes de tudo — o grupo de recursos da
        Azure, por exemplo. Vazio na maioria."""
        ...


def montar(
    adaptador: AdaptadorNuvem,
    bp: TerraformBlueprint,
    procedencia: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Blueprint + adaptador → `{nome do arquivo: conteúdo}`."""
    return {
        "versions.tf": _versions(adaptador),
        "providers.tf": _providers(adaptador, bp),
        "variables.tf": _variables(adaptador, bp),
        "main.tf": _main(adaptador, bp),
        "outputs.tf": _outputs(adaptador, bp),
        "terraform.tfvars.example": _tfvars(adaptador, bp),
        "README.md": readme(bp, procedencia or {}),
    }


def _versions(a: AdaptadorNuvem) -> str:
    return arquivo([
        comentario(
            "Versões fixas de propósito.\n"
            "`latest` num arquivo aplicado meses depois é o mesmo que não\n"
            "versionar: o plano mudaria sozinho."
        ),
        "terraform {\n"
        f'  required_version = "{VERSAO_TERRAFORM}"\n'
        "  required_providers {\n"
        f"    {a.provider_nome} = {{\n"
        f'      source  = "{a.provider_source}"\n'
        f'      version = "{a.provider_version}"\n'
        "    }\n"
        "  }\n"
        "}",
    ])


def _providers(a: AdaptadorNuvem, bp: TerraformBlueprint) -> str:
    return arquivo([
        comentario(
            "Sem credencial neste arquivo. O provider usa a autenticação do\n"
            "ambiente de quem aplica. A plataforma nunca recebe, armazena nem\n"
            "gera credencial de nuvem."
        ),
        a.provider_bloco(bp),
    ])


def _variables(a: AdaptadorNuvem, bp: TerraformBlueprint) -> str:
    secoes = [
        comentario(
            "Variáveis sem `default` são o que a análise NÃO soube responder.\n"
            "Preencher é obrigatório — o valor não foi adivinhado de propósito."
        )
    ]
    for v in [*a.variaveis_extras(bp), *bp.variables]:
        corpo = [
            atributo("type", _tipo(v.type)),
            atributo("description", v.description),
        ]
        if v.default is not None:
            corpo.append(atributo("default", v.default))
        if v.sensitive:
            corpo.append(atributo("sensitive", True))
        secoes.append(bloco("variable", [v.name], corpo))
    return arquivo(secoes)


def _main(a: AdaptadorNuvem, bp: TerraformBlueprint) -> str:
    secoes: list[str] = [
        comentario(
            f"Infraestrutura derivada da análise de billing — projeto "
            f"'{bp.project_name}', ambiente '{bp.environment}'.\n"
            "Gerado pela OneShift. Revise antes de aplicar: veja as premissas\n"
            "no README, especialmente os tamanhos estimados."
        ),
        *a.preambulo(bp),
    ]
    for recurso in bp.resources:
        escrito = a.recurso(recurso, bp)
        if escrito:
            secoes.append(escrito)
    return arquivo(secoes)


def _outputs(a: AdaptadorNuvem, bp: TerraformBlueprint) -> str:
    saidas = a.outputs(bp)
    if not saidas:
        saidas = [comentario("Nenhum output: o pacote não criou recursos.")]
    return arquivo(saidas)


def _tfvars(a: AdaptadorNuvem, bp: TerraformBlueprint) -> str:
    linhas = [
        comentario(
            "Copie para `terraform.tfvars` e preencha.\n"
            "NUNCA versione o arquivo preenchido nem ponha credencial aqui."
        )
    ]
    for v in [*a.variaveis_extras(bp), *bp.variables]:
        if v.default is None:
            linhas.append(f'# {v.description}\n{v.name} = ""')
    return arquivo(linhas)


def _tipo(t: str) -> Any:
    """Tipo de variável sai sem aspas em HCL (`string`, não `"string"`)."""
    return referencia(t)


# ── README, igual em toda nuvem ─────────────────────────────────────────

_SO_CABECALHO = 2

_ROTULO_CATEGORIA = {
    "derivacao": "Calculado",
    "estimativa": "Estimado",
    "sem_base": "Não deduzível",
    "escolha_usuario": "Informado por você",
}


def readme(bp: TerraformBlueprint, procedencia: dict[str, Any]) -> str:
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
    coberto = premissas.get("cobertura_por_compromisso") or {}
    if coberto:
        linhas.append(
            f"- **Pools cobertos por compromisso:** {', '.join(coberto['pools'])} "
            f"— a economia calculada contra eles está superestimada"
        )
    for sugestao in premissas.get("sugestoes_backend") or []:
        linhas.append(f"- **{sugestao.get('titulo')}** — {sugestao.get('detalhe')}")

    linhas.append("")
    return "\n".join(linhas) if len(linhas) > _SO_CABECALHO else ""


def nome_cloud(*partes: object, maximo: int = 63, separador: str = "-") -> str:
    """Nome aceito pelas nuvens: minúsculas, sem acento, começa com letra.

    Não é o mesmo que um identificador Terraform, e confundir os dois foi um bug
    real: `project_name` aceita `_` (válido em HCL) e as nuvens recusam. O erro
    só aparecia no `terraform validate`, contra o schema do provider.
    """
    bruto = separador.join(str(p) for p in partes if p) if separador else "".join(
        str(p) for p in partes if p
    )
    if not separador:
        # Azure exige storage account só com minúscula e dígito: nada de
        # separador. `"".split("")` levanta ValueError, então o caminho é outro.
        limpo = "".join(c for c in bruto.lower() if c.isascii() and c.isalnum())
    else:
        limpo = "".join(
            c if (c.isascii() and c.isalnum()) else separador for c in bruto.lower()
        )
        limpo = separador.join(p for p in limpo.split(separador) if p)
    while limpo and not (limpo[0].isascii() and limpo[0].isalpha()):
        limpo = limpo[1:]
    limpo = limpo[:maximo]
    if separador:
        limpo = limpo.rstrip(separador)
    return limpo or "oneshift"
