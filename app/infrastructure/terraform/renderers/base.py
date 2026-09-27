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


# ── Texto que acompanha número derivado ─────────────────────────────────────
# O comentário no `.tf` diz de onde veio o número. Com export detalhado ele é
# MEDIDO; sem, é ESTIMADO pelo custo. Dizer "estimado" de um número medido
# subestima a análise; dizer "medido" de um estimado é pior.


# O plano nomeia o disco com os tipos do GCP (ver `terraform_plan._TIPO_DE_DISCO`).
# A classe é o que os outros renderers traduzem; decidir pelo sufixo `ssd`
# levava o pd-balanced, que é SSD, para st1 e Standard_LRS.
_TIPOS_DE_DISCO_SSD = frozenset({"pd-balanced", "pd-ssd", "pd-extreme"})


def disco_ssd(cfg: dict[str, Any]) -> bool:
    return str(cfg.get("disk_type") or "") in _TIPOS_DE_DISCO_SSD


def ajustar_tamanho(gb: int, *, minimo: int, maximo: int, tipo: str) -> tuple[int, str]:
    """Tamanho dentro da faixa que o tipo de disco aceita, e a nota quando mudou.

    A faixa é regra da API do provedor: `terraform validate` não a confere, e um
    volume fora dela só falharia no `apply` (um st1 de 110 GB, por exemplo)."""
    ajustado = min(max(gb, minimo), maximo)
    if ajustado == gb:
        return gb, ""
    return ajustado, (
        f"\n{tipo} aceita de {minimo:,} a {maximo:,} GB: o volume de ~{gb:,} GB "
        f"saiu com {ajustado:,}."
    )


def quantidade_volumes(cfg: dict[str, Any]) -> int:
    """Volumes medidos na origem viram `count`; sem medição, um só."""
    return max(1, int(cfg.get("volumes") or 1))


def texto_tamanho_disco(cfg: dict[str, Any]) -> str:
    if cfg.get("size_source") == "medido":
        n = quantidade_volumes(cfg)
        return (
            f"TAMANHO MEDIDO no billing da origem: {n} volume(s) de ~{cfg.get('size_gb')} GB\n"
            "(soma de GB-mês; num mês inteiro, o tamanho médio provisionado)."
        )
    return (
        "TAMANHO ESTIMADO a partir do custo mensal — veja a premissa no README.\n"
        "Confira contra o uso real antes de aplicar."
    )


def texto_volume_bucket(cfg: dict[str, Any]) -> str:
    if cfg.get("size_source") == "medido":
        return f"Volume medido: ~{cfg.get('estimated_gb')} GB (soma de GB-mês no billing).\n"
    return f"Volume estimado: ~{cfg.get('estimated_gb')} GB (derivado do custo).\n"


def texto_banco(cfg: dict[str, Any], vcpus: int) -> str:
    """vCPU e motor do banco, com a fonte de cada um."""
    if cfg.get("vcpu_source") == "medido":
        linhas = [
            f"vCPU ({vcpus}) da classe medida na origem: {cfg.get('source_class')} "
            f"({cfg.get('instances_measured', 1)} instância(s)).",
        ]
    else:
        linhas = [f"vCPU ESTIMADO ({vcpus}) a partir do custo mensal — veja o README."]
    motor = cfg.get("engine_measured")
    if motor:
        linhas.append(f"Motor medido na origem: {motor}. A versão não aparece no billing:")
        linhas.append("preencha `database_version` antes de aplicar.")
    else:
        linhas.append("Motor e versão NÃO foram deduzidos: o billing não os informa.")
        linhas.append("Preencha `database_version` antes de aplicar.")
    return "\n".join(linhas)


# ── Banco: grupo medido com `count` ─────────────────────────────────────────


def instancias_banco(cfg: dict[str, Any]) -> int:
    return max(1, int(cfg.get("instances") or 1))


def nome_banco(bp: TerraformBlueprint, r: RecursoTerraform, *extra: object) -> str:
    """Banco medido usa o nome lógico (há um por grupo); o do caminho agregado
    mantém `<projeto>-db`, o nome que os pacotes já gerados têm."""
    if r.configuration.get("source_class"):
        return nome_cloud(bp.project_name, r.logical_name, *extra)
    return nome_cloud(bp.project_name, "db", *extra)


def com_indice(nome: str, quantidade: int) -> str:
    """Nome literal, ou interpolado com `count.index` quando há várias instâncias."""
    return referencia(f'"{nome}-${{count.index}}"') if quantidade > 1 else nome


def var_versao(cfg: dict[str, Any]) -> str:
    return referencia(f"var.{cfg.get('version_var') or 'database_version'}")
