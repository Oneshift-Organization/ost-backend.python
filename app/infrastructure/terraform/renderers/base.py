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

import re
from collections import Counter
from typing import Any, Protocol

from app.domain.services.regioes import regiao_canonica, zona_da_regiao
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
from app.use_cases.analysis.topologia_no_payload import sequencia_de_migracao

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

    def outputs(self, bp: TerraformBlueprint) -> list[str]: ...

    def preambulo(self, bp: TerraformBlueprint) -> list[str]:
        """Recursos que a nuvem exige antes de tudo — o grupo de recursos da
        Azure, por exemplo. Vazio na maioria."""
        ...


def montar(
    adaptador: AdaptadorNuvem,
    bp: TerraformBlueprint,
    procedencia: dict[str, Any] | None = None,
    topologia: dict[str, Any] | None = None,
    moeda: str | None = None,
) -> dict[str, str]:
    """Blueprint + adaptador → `{nome do arquivo: conteúdo}`."""
    return {
        "versions.tf": _versions(adaptador),
        "providers.tf": _providers(adaptador, bp),
        "variables.tf": _variables(adaptador, bp),
        "main.tf": _main(adaptador, bp),
        "outputs.tf": _outputs(adaptador, bp),
        "terraform.tfvars.example": _tfvars(adaptador, bp),
        "README.md": readme(bp, procedencia or {}, topologia, moeda),
    }


def _versions(a: AdaptadorNuvem) -> str:
    return arquivo(
        [
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
        ]
    )


def _providers(a: AdaptadorNuvem, bp: TerraformBlueprint) -> str:
    return arquivo(
        [
            comentario(
                "Sem credencial neste arquivo. O provider usa a autenticação do\n"
                "ambiente de quem aplica. A plataforma nunca recebe, armazena nem\n"
                "gera credencial de nuvem."
            ),
            a.provider_bloco(bp),
        ]
    )


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


def readme(
    bp: TerraformBlueprint,
    procedencia: dict[str, Any],
    topologia: dict[str, Any] | None = None,
    moeda: str | None = None,
) -> str:
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
        *([_secao_escopo(topologia["escopo"])] if topologia and topologia.get("escopo") else []),
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

    if topologia:
        partes += [_secao_topologia(topologia, moeda)]
    partes += [_secao_procedencia(procedencia)]
    partes += [_secao_custo(bp.provider.value, bp.region, procedencia)]
    return "\n".join(partes).rstrip() + "\n"


def _secao_escopo(escopo: dict[str, Any]) -> str:
    """Terraform só do escopo do plano de migração (T5): o que entra, em que ordem, e o
    que fica do outro lado e precisa de conexão entre as nuvens."""

    def ligacao(g: dict[str, Any]) -> str:
        ordem = f" (ordem {g['ordem']})" if g.get("ordem") is not None else ""
        portas = f", portas {', '.join(g['portas'])}" if g.get("portas") else ""
        return f"- {g['grupo']}{ordem}{portas}"

    aplicacoes = ", ".join(escopo.get("aplicacoes") or []) or "—"
    linhas = [
        "## Escopo deste pacote",
        "",
        f"Só o plano de migração escolhido: **{aplicacoes}** "
        f"({escopo.get('unidades', 0)} unidade(s)). Máquinas, bancos, subredes e grupos "
        "de segurança são os deles; discos e buckets saem do custo deles na fatura.",
        "",
    ]
    ordens = escopo.get("ordens") or []
    if ordens:
        linhas += ["Ordem de migração:", ""]
        linhas += [
            f"{o['ordem'] if o.get('ordem') is not None else 'livre'}. "
            + ", ".join(o.get("grupos") or [])
            for o in ordens
        ]
        linhas.append("")
    antes, para_tras = escopo.get("antes") or [], escopo.get("para_tras") or []
    if antes or para_tras:
        linhas += ["### Conectividade entre nuvens a prever", ""]
        if antes:
            linhas += [
                "Vão **antes** e não estão aqui: o escopo depende deles. Precisam estar no "
                "destino antes, ou acessíveis por uma conexão entre as nuvens.",
                "",
                *[ligacao(g) for g in antes],
                "",
            ]
        if para_tras:
            linhas += [
                "Ficam **para trás**, na origem, e dependem do escopo: até migrarem, falam "
                "com ele por uma conexão entre as nuvens.",
                "",
                *[ligacao(g) for g in para_tras],
                "",
            ]
    if escopo.get("pools_fora"):
        linhas += [
            "Pools de compute da análise sem máquina no escopo não entram: "
            + ", ".join(escopo["pools_fora"])
            + ".",
            "",
        ]
    linhas += [f"- {a}" for a in escopo.get("avisos") or []]
    return "\n".join(linhas).rstrip() + "\n"


def _secao_custo(destino: str, regiao: str, premissas: dict[str, Any]) -> str:
    """Onde a análise cotou o destino, e se a região escolhida é a mesma metrópole.

    O pacote continua sem número: ele provisiona parte da fatura, e um total
    aqui seria lido como o custo deste pacote. Mas a região importa — São Paulo
    custa ~1,6x a Virgínia — e o cliente precisa saber se escolheu onde a
    análise cotou.
    """
    linhas = [
        "## Sobre custo",
        "",
        "Este pacote **não declara economia**: a comparação de custo está na "
        "análise, com as premissas, e cobre a fatura inteira, não só o que este "
        "pacote provisiona.",
        "",
    ]
    zonas = sorted((premissas.get("regioes") or {}).get("pools_por_zona") or {})
    cotadas = sorted({r for z in zonas if (r := regiao_canonica(z, destino))})
    if not cotadas:
        # Análise anterior à cotação regional: uma região fixa por provedor.
        linhas += [
            "A análise que gerou este pacote cotou cada provedor numa região de "
            "referência fixa, que pode não ser a escolhida aqui.",
            "",
        ]
        return "\n".join(linhas)
    linhas += [
        f"A análise cotou {destino.upper()} em {', '.join(f'`{r}`' for r in cotadas)}: "
        "a região equivalente de onde cada carga roda hoje.",
        "",
    ]
    if zona_da_regiao(destino, regiao) not in zonas:
        linhas += [
            f"⚠️ A região escolhida aqui, `{regiao}`, fica em outra metrópole. O preço "
            "dela não é o que a análise cotou, e pode ser bem diferente (São Paulo "
            "custa cerca de 1,6 vez a Virgínia).",
            "",
        ]
    return "\n".join(linhas)


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
            f"- **Câmbio:** {fx.get('par')} = {fx.get('taxa')} (fonte: {fx.get('fonte')}, {data})"
        )
    confianca = premissas.get("confianca_analise") or {}
    if confianca:
        linhas.append(
            f"- **Confiança da análise:** {confianca.get('nivel')} "
            f"(fração do custo atual cotado com preço oficial: "
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


# ── Topologia de origem (RI15.8 e RI15.9) ───────────────────────────────

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]+")
_FORA_DO_PACOTE = (
    ("balanceador", "balanceador(es)"),
    ("gateway", "gateway(s) (NAT, VPN, internet)"),
    ("cache", "cache(s)"),
    ("cluster_kubernetes", "cluster(s) Kubernetes"),
)


def texto_seguro(valor: object, maximo: int = 160) -> str:
    """Texto vindo do inventário, pronto para comentário do `.tf` ou README (RI17).

    Quebra de linha num comentário do `.tf` vira código HCL na linha seguinte;
    caractere de controle não tem lugar em nenhum dos dois.
    """
    return _CONTROLE.sub(" ", str(valor)).strip()[:maximo]


def _celula(valor: object) -> str:
    return texto_seguro(valor).replace("|", "\\|")


def _secao_topologia(topologia: dict[str, Any], moeda: str | None) -> str:
    """O que o inventário disse da origem: ordem, dependências, riscos, cobertura."""
    resumo = topologia.get("resumo") or {}
    linhas = ["## Topologia de origem", ""]
    linhas += _cobertura(topologia, resumo, moeda)
    linhas += ["### Ordem de migração", ""]
    linhas += [
        f"{i}. {texto_seguro(p, 400)}"
        for i, p in enumerate(sequencia_de_migracao(topologia).passos, start=1)
    ]
    linhas.append("")
    linhas += _dependencias(resumo.get("dependencias_entre_ondas") or [])
    linhas += _riscos(resumo.get("riscos") or [], (resumo.get("contagens") or {}).get("riscos"))
    por_tipo = (resumo.get("contagens") or {}).get("por_tipo") or {}
    fora = [f"{por_tipo[t]} {rotulo}" for t, rotulo in _FORA_DO_PACOTE if por_tipo.get(t)]
    if fora:
        linhas += [
            "### Fora do pacote",
            "",
            "Existem na origem e não viram recurso aqui: " + ", ".join(fora) + ".",
            "",
        ]
    if resumo.get("limitacao"):
        linhas += [f"_{texto_seguro(resumo['limitacao'], 400)}_", ""]
    return "\n".join(linhas)


def _cobertura(topologia: dict[str, Any], resumo: dict[str, Any], moeda: str | None) -> list[str]:
    data = str(topologia.get("capturado_em") or "")[:10] or "data desconhecida"
    estimada = " (data estimada pelo envio)" if topologia.get("capturado_em_estimado") else ""
    cobertura = resumo.get("cobertura") or {}
    fracao = cobertura.get("fracao_encontrada")
    texto = f"Inventário {str(topologia.get('provedor') or '').upper()} de {data}{estimada}."
    if isinstance(fracao, int | float):
        texto += f" {fracao:.0%} do custo com recurso foi achado no inventário."
    linhas = [texto, ""]
    lacunas = cobertura.get("lacunas") or []
    if lacunas:
        linhas += [
            "Custo sem recurso correspondente no inventário, por conta e região. O que não "
            "está no inventário não tem posicionamento: vai para a subrede padrão.",
            "",
        ]
        prefixo = f"{moeda} " if moeda else ""
        linhas += [
            f"- conta {_celula(x.get('conta'))}, {_celula(x.get('regiao'))}: "
            f"{prefixo}{x.get('custo', 0):,.2f}/mês"
            for x in lacunas
        ]
        linhas.append("")
    return linhas


def _dependencias(dependencias: list[dict[str, Any]]) -> list[str]:
    if not dependencias:
        return []
    linhas = [
        "### O que a conectividade híbrida precisa liberar",
        "",
        "Durante a migração, uma onda já no destino fala com outra que ainda está na "
        "origem. Estas portas precisam passar pela VPN:",
        "",
        "| De | Para | Portas |",
        "|---|---|---|",
    ]
    for d in dependencias:
        portas = ", ".join(d.get("portas") or []) or "todas"
        linhas.append(f"| {_celula(d.get('de'))} | {_celula(d.get('para'))} | {_celula(portas)} |")
    linhas.append("")
    return linhas


def _riscos(riscos: list[dict[str, Any]], contagem: dict[str, int] | None) -> list[str]:
    """Riscos altos e médios somados por título. A lista do resumo é cortada;
    a contagem por severidade é a da análise inteira, e é ela que diz se faltou."""
    graves: Counter[tuple[str, str]] = Counter()
    listados = Counter(str(r.get("severidade")) for r in riscos)
    for r in riscos:
        if r.get("severidade") in ("alta", "media"):
            graves[(str(r.get("severidade")), str(r.get("titulo")))] += int(r.get("total") or 0)
    contagem = contagem or {}
    linhas: list[str] = []
    if graves:
        linhas += ["### Riscos da origem", ""]
        linhas += ["| Severidade | Risco | Recursos afetados |", "|---|---|---|"]
        for (severidade, titulo), total in sorted(
            graves.items(), key=lambda kv: (kv[0][0] != "alta", -kv[1], kv[0][1])
        ):
            linhas.append(f"| {severidade} | {_celula(titulo)} | {total} |")
        linhas.append("")
        faltam = sum(max(0, int(contagem.get(s) or 0) - listados[s]) for s in ("alta", "media"))
        if faltam:
            linhas += [
                f"A análise tem mais {faltam} risco(s) alto(s) ou médio(s) fora deste resumo.",
                "",
            ]
        linhas += [
            "Regra de risco alto (administração, banco ou tudo aberto para a internet) "
            "não é replicada no destino: seria migrar a falha.",
            "",
        ]
    informativos = int(contagem.get("informativa") or listados["informativa"])
    if informativos:
        linhas += [f"Mais {informativos} achado(s) informativo(s) na análise.", ""]
    return linhas


def nome_cloud(*partes: object, maximo: int = 63, separador: str = "-") -> str:
    """Nome aceito pelas nuvens: minúsculas, sem acento, começa com letra.

    Não é o mesmo que um identificador Terraform, e confundir os dois foi um bug
    real: `project_name` aceita `_` (válido em HCL) e as nuvens recusam. O erro
    só aparecia no `terraform validate`, contra o schema do provider.
    """
    bruto = (
        separador.join(str(p) for p in partes if p)
        if separador
        else "".join(str(p) for p in partes if p)
    )
    if not separador:
        # Azure exige storage account só com minúscula e dígito: nada de
        # separador. `"".split("")` levanta ValueError, então o caminho é outro.
        limpo = "".join(c for c in bruto.lower() if c.isascii() and c.isalnum())
    else:
        limpo = "".join(c if (c.isascii() and c.isalnum()) else separador for c in bruto.lower())
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


def texto_espelho(cfg: dict[str, Any]) -> str | None:
    """Comentário da subrede espelhada: de qual subrede de origem ela veio."""
    origem = cfg.get("mirror_of")
    if not isinstance(origem, dict):
        return None
    nome = texto_seguro(origem.get("nome") or origem.get("chave") or "?", 80)
    cidr = texto_seguro(origem.get("cidr") or "sem CIDR", 40)
    return (
        f"Espelha a subrede de origem {nome} ({cidr}), com endereçamento novo:\n"
        "reusar o CIDR da origem quebraria a VPN da migração."
    )


def texto_grupo(cfg: dict[str, Any]) -> str:
    """Comentário do grupo espelhado: de onde veio e o que foi deixado de fora."""
    origem = cfg.get("mirror_of") or {}
    nome = texto_seguro(origem.get("nome") or "?", 120)
    if cfg.get("combined"):
        return (
            f"Grupo combinado ({nome}): o recurso herda mais grupos do que o destino aceita\n"
            "por interface, então recebe as regras da união num grupo só."
        )
    return (
        f"Espelha o grupo de segurança de origem {nome}. Regras de entrada traduzidas;\n"
        "as de risco alto não foram replicadas (veja os avisos no README)."
    )


def fontes(regra: dict[str, Any]) -> list[tuple[str, str]]:
    """Cada origem de uma regra traduzida: ("cidr", faixa) ou ("grupo", nome lógico)."""
    return [("cidr", str(c)) for c in regra.get("source_cidrs") or []] + [
        ("grupo", str(g)) for g in regra.get("source_groups") or []
    ]


def faixa_de_portas(regra: dict[str, Any]) -> tuple[int, int] | None:
    """(início, fim) das portas TCP/UDP; ``None`` quando são todas."""
    inicio, fim = regra.get("from_port"), regra.get("to_port")
    if inicio is None and fim is None:
        return None
    inicio = 0 if inicio is None else int(inicio)
    fim = 65535 if fim is None else int(fim)
    return None if (inicio, fim) == (0, 65535) else (inicio, fim)


def disco_raiz(
    cfg: dict[str, Any], *, minimo: int, maximo: int, tipo: str
) -> tuple[int, str] | None:
    """Tamanho do disco raiz medido no inventário (RI15.7) e a linha do comentário.

    ``None`` sem inventário: o disco raiz fica o padrão da imagem, como sempre foi.
    """
    gb = cfg.get("root_gb")
    if not isinstance(gb, int) or gb <= 0:
        return None
    tamanho, nota = ajustar_tamanho(gb, minimo=minimo, maximo=maximo, tipo=tipo)
    return tamanho, f"Disco raiz de {tamanho} GB: média do boot na origem (inventário).{nota}"


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
    if motor and cfg.get("version_default"):
        linhas.append(f"Motor medido na origem: {motor}. Versão medida no inventário:")
        linhas.append(f"default {cfg['version_default']} na variável; confira antes de aplicar.")
    elif motor:
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
