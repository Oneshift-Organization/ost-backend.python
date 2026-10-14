"""Aplicações da conta e o plano de migração de um escopo (PLANO_ESCOPO_DE_MIGRACAO, E1).

Duas leituras do mesmo grafo da topologia:

- **catálogo**: cada aplicação com os serviços que usa (e quanto custa cada um
  nela), e cada serviço com as aplicações que o usam e os serviços de que depende;
- **plano**: a escolha do usuário (aplicações e/ou serviços, um ambiente) fechada
  pelas regras da topologia:
  - *vão junto*: o grupo de movimentação inteiro de cada unidade escolhida (o
    grupo já é a união pelo que é forte: banco compartilhado, encaminhamento);
  - *antes*: grupos que o escopo chama (dependência fraca, escopo → fora);
  - *para trás*: grupos que chamam o escopo (fraca, fora → escopo). Conversa nos
    dois sentidos aparece uma vez, em *antes*;
  - *ligações entre serviços* dentro do escopo (E6: o EC2 que acessa o RDS).

Escolher um serviço leva as aplicações inteiras que o usam: uma VM sem o banco
da própria aplicação não funciona no destino. O plano diz quais serviços vieram
junto e por quê.

Ferramenta de acesso (bastion) não é aplicação: fica fora do catálogo e do plano,
como fica fora das ondas. Dependência administrativa não restringe nada.

Função pura, sem I/O. Dinheiro em ``Decimal``, na moeda da análise.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.services.topologia.dependencias_de_servico import (
    DependenciaDeServico,
    agrupar_por_par,
)
from app.domain.services.topologia.modelo import (
    Acoplamento,
    AnaliseDeTopologia,
    Natureza,
    Unidade,
)

_ZERO = Decimal(0)


class EscolhaInvalidaError(ValueError):
    """Escolha vazia, ou com aplicação ou serviço que a análise não tem."""


@dataclass(frozen=True, slots=True)
class ServicoLigado:
    servico: str
    natureza: Natureza
    portas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Aplicacao:
    nome: str
    unidades: tuple[str, ...]
    ambientes: tuple[str, ...]
    regioes: tuple[str, ...]
    custo: Decimal
    servicos: tuple[tuple[str, Decimal], ...]  # maior custo primeiro
    ordens: tuple[int, ...]  # ondas dos grupos dela; vazio = sem restrição de ordem
    migra_junto_com: tuple[str, ...]  # outras aplicações no mesmo grupo de movimentação
    recursos: int


@dataclass(frozen=True, slots=True)
class UsoDoServico:
    nome: str
    aplicacoes: tuple[tuple[str, Decimal], ...]
    sem_aplicacao: Decimal  # custo do serviço que não casou com nenhuma unidade
    depende_de: tuple[ServicoLigado, ...]
    usado_por: tuple[ServicoLigado, ...]


@dataclass(frozen=True, slots=True)
class GrupoResumo:
    id: str
    nome: str
    ordem: int | None
    aplicacoes: tuple[str, ...]
    custo: Decimal


@dataclass(frozen=True, slots=True)
class CatalogoDeAplicacoes:
    aplicacoes: tuple[Aplicacao, ...]
    servicos: tuple[UsoDoServico, ...]
    grupos: tuple[GrupoResumo, ...]
    ferramentas: tuple[str, ...]
    sem_aplicacao: Decimal
    # A tag de onde veio "aplicação"; sem ela, cada unidade conta como uma
    chave_aplicacao: str | None
    # Para o fechamento: não sai na API
    analise: AnaliseDeTopologia
    custo_por_unidade: Mapping[str, Mapping[str, Decimal]]
    recursos_por_unidade: Mapping[str, int]
    deps_de_servico: tuple[DependenciaDeServico, ...]


@dataclass(frozen=True, slots=True)
class Escolha:
    aplicacoes: frozenset[str] = frozenset()
    servicos: frozenset[str] = frozenset()
    ambiente: str | None = None
    incluir_grupos: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class UnidadeQueVaiJunto:
    unidade: str
    nome: str
    aplicacao: str
    custo: Decimal
    motivo: str
    incluida_por_voce: bool
    grupo: str


@dataclass(frozen=True, slots=True)
class ServicoQueVemJunto:
    servico: str
    custo: Decimal
    motivo: str


@dataclass(frozen=True, slots=True)
class GrupoVizinho:
    grupo: str
    nome: str
    ordem: int | None
    naturezas: tuple[Natureza, ...]
    portas: tuple[str, ...]
    custo: Decimal
    # Em "antes": o grupo também chama o escopo (conversa nos dois sentidos)
    nos_dois_sentidos: bool = False
    # Os grupos do escopo com que ele conversa (o que o liga ao plano)
    ligado_a: tuple[str, ...] = ()


ANTES = "antes"
DEPOIS = "depois"
SEM_LIGACAO = "sem_ligacao"


@dataclass(frozen=True, slots=True)
class GrupoIncluido:
    """Grupo de fora que o usuário pôs no plano, e de que lado da migração ele fica.

    ``antes``: o escopo chama o grupo, ele precisa estar no destino primeiro
    (pré-migração). ``depois``: o grupo chama o escopo e vai depois dele
    (pós-migração). Conversa nos dois sentidos conta como ``antes``, como em
    ``GrupoVizinho``. ``sem_ligacao``: nada mais no plano conversa com ele (o
    grupo que o trouxe saiu).
    """

    grupo: str
    nome: str
    lado: str
    ordem: int | None
    custo: Decimal
    aplicacoes: tuple[str, ...]
    ligado_a: tuple[str, ...]
    naturezas: tuple[Natureza, ...]
    portas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LigacaoNoEscopo:
    de: str
    para: str
    natureza: Natureza
    portas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrdemDoEscopo:
    ordem: int | None
    grupos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanoDeMigracao:
    escolha: Escolha
    unidades: tuple[str, ...]
    aplicacoes: tuple[str, ...]
    servicos: tuple[tuple[str, Decimal], ...]
    recursos: int
    custo: Decimal
    vao_junto: tuple[UnidadeQueVaiJunto, ...]
    servicos_que_vem_junto: tuple[ServicoQueVemJunto, ...]
    antes: tuple[GrupoVizinho, ...]
    para_tras: tuple[GrupoVizinho, ...]
    ligacoes: tuple[LigacaoNoEscopo, ...]
    ordens: tuple[OrdemDoEscopo, ...]
    # Custo dos serviços escolhidos que não casa com nenhuma aplicação
    fora_do_plano: tuple[tuple[str, Decimal], ...]
    # Os grupos postos à mão, cada um com o lado (pré ou pós-migração)
    incluidos: tuple[GrupoIncluido, ...] = ()


# ── catálogo ────────────────────────────────────────────────────────────


def _aplicacao_de(u: Unidade) -> str:
    return u.aplicacao or u.nome


def montar_catalogo(
    analise: AnaliseDeTopologia,
    custo_por_unidade: Mapping[str, Mapping[str, Decimal]],
    sem_unidade: Mapping[str, Decimal],
    deps_de_servico: Iterable[DependenciaDeServico],
    chave_aplicacao: str | None,
) -> CatalogoDeAplicacoes:
    """``custo_por_unidade``: unidade → serviço → custo. ``sem_unidade``: serviço → custo
    das linhas da fatura que não casaram com nenhuma unidade."""
    deps = tuple(deps_de_servico)
    unidades = {u.id: u for u in analise.unidades}
    ferramentas = _aplicacoes_ferramenta(analise)
    onda_do_grupo = {g: o.numero for o in analise.ondas for g in o.grupos}
    recursos = {u.id: len(u.recursos) for u in analise.unidades}

    por_app: dict[str, list[Unidade]] = defaultdict(list)
    for u in analise.unidades:
        if _aplicacao_de(u) not in ferramentas:
            por_app[_aplicacao_de(u)].append(u)

    aplicacoes = []
    for nome, us in por_app.items():
        servicos: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        for u in us:
            for s, c in custo_por_unidade.get(u.id, {}).items():
                servicos[s] += c
        grupos = {analise.grupo_de[u.id] for u in us if u.id in analise.grupo_de}
        juntos = {
            _aplicacao_de(unidades[x])
            for g in grupos
            for x in _membros(analise, g)
            if _aplicacao_de(unidades[x]) != nome and _aplicacao_de(unidades[x]) not in ferramentas
        }
        aplicacoes.append(
            Aplicacao(
                nome=nome,
                unidades=tuple(sorted(u.id for u in us)),
                ambientes=tuple(sorted({u.ambiente for u in us if u.ambiente})),
                regioes=tuple(sorted({u.regiao for u in us if u.regiao})),
                custo=sum(servicos.values(), _ZERO),
                servicos=tuple(sorted(servicos.items(), key=lambda kv: (-kv[1], kv[0]))),
                ordens=tuple(sorted({onda_do_grupo[g] for g in grupos if g in onda_do_grupo})),
                migra_junto_com=tuple(sorted(juntos)),
                recursos=sum(recursos[u.id] for u in us),
            )
        )
    aplicacoes.sort(key=lambda a: (-a.custo, a.nome))

    por_servico: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    for u in analise.unidades:
        app = _aplicacao_de(u)
        if app in ferramentas:
            continue
        for s, c in custo_por_unidade.get(u.id, {}).items():
            if c > 0:
                por_servico[s][app] += c
    pares = agrupar_por_par(deps)
    nomes = set(por_servico) | set(sem_unidade)
    servicos_uso = tuple(
        UsoDoServico(
            nome=s,
            aplicacoes=tuple(sorted(por_servico[s].items(), key=lambda kv: (-kv[1], kv[0]))),
            sem_aplicacao=sem_unidade.get(s, _ZERO),
            depende_de=tuple(
                ServicoLigado(p, n, portas) for (d, p), (n, portas, _) in pares.items() if d == s
            ),
            usado_por=tuple(
                ServicoLigado(d, n, portas) for (d, p), (n, portas, _) in pares.items() if p == s
            ),
        )
        for s in sorted(nomes)
    )

    resumo_dos_grupos = tuple(
        GrupoResumo(
            id=g.id,
            nome=g.nome,
            ordem=onda_do_grupo.get(g.id),
            aplicacoes=tuple(sorted({_aplicacao_de(unidades[x]) for x in g.unidades})),
            custo=sum(
                (sum(custo_por_unidade.get(x, {}).values(), _ZERO) for x in g.unidades), _ZERO
            ),
        )
        for g in analise.grupos
        if not {_aplicacao_de(unidades[x]) for x in g.unidades} <= ferramentas
    )
    return CatalogoDeAplicacoes(
        aplicacoes=tuple(aplicacoes),
        servicos=servicos_uso,
        grupos=resumo_dos_grupos,
        ferramentas=tuple(sorted(ferramentas)),
        sem_aplicacao=sum(sem_unidade.values(), _ZERO),
        chave_aplicacao=chave_aplicacao,
        analise=analise,
        custo_por_unidade=custo_por_unidade,
        recursos_por_unidade=recursos,
        deps_de_servico=deps,
    )


def _membros(analise: AnaliseDeTopologia, grupo: str) -> tuple[str, ...]:
    for g in analise.grupos:
        if g.id == grupo:
            return g.unidades
    return ()


def _aplicacoes_ferramenta(analise: AnaliseDeTopologia) -> set[str]:
    """Aplicação com alguma unidade tratada como ferramenta (bastion, CI)."""
    ferramentas = set(analise.ferramentas)
    return {_aplicacao_de(u) for u in analise.unidades if u.id in ferramentas}


# ── plano ───────────────────────────────────────────────────────────────


def fechar_plano(catalogo: CatalogoDeAplicacoes, escolha: Escolha) -> PlanoDeMigracao:
    analise = catalogo.analise
    unidades = {u.id: u for u in analise.unidades}
    apps = {a.nome for a in catalogo.aplicacoes}
    servicos_conhecidos = {s.nome for s in catalogo.servicos}
    grupos_conhecidos = {g.id for g in catalogo.grupos}
    _validar(escolha, apps, servicos_conhecidos, grupos_conhecidos)

    ferramentas = set(catalogo.ferramentas)
    fora_das_ferramentas = [u for u in analise.unidades if _aplicacao_de(u) not in ferramentas]
    custo = catalogo.custo_por_unidade

    semente = {u.id for u in fora_das_ferramentas if _aplicacao_de(u) in escolha.aplicacoes}
    semente |= {
        u.id
        for u in fora_das_ferramentas
        if any(custo.get(u.id, {}).get(s, _ZERO) > 0 for s in escolha.servicos)
    }
    if escolha.ambiente:
        semente = {x for x in semente if unidades[x].ambiente == escolha.ambiente}

    grupos = {analise.grupo_de[x] for x in semente if x in analise.grupo_de}
    grupos |= escolha.incluir_grupos
    no_escopo = {
        u.id for u in fora_das_ferramentas if analise.grupo_de.get(u.id) in grupos
    } | semente

    def custo_de(unidade: str) -> Decimal:
        return sum(custo.get(unidade, {}).values(), _ZERO)

    grupos_da_semente = {analise.grupo_de.get(x) for x in semente}
    vao_junto = tuple(
        sorted(
            (
                UnidadeQueVaiJunto(
                    unidade=x,
                    nome=unidades[x].nome,
                    aplicacao=_aplicacao_de(unidades[x]),
                    custo=custo_de(x),
                    motivo=_motivo(analise, x, no_escopo, unidades),
                    incluida_por_voce=analise.grupo_de.get(x) not in grupos_da_semente,
                    grupo=analise.grupo_de.get(x, x),
                )
                for x in no_escopo - semente
            ),
            key=lambda v: (-v.custo, v.nome),
        )
    )

    antes, para_tras = _vizinhos(catalogo, no_escopo, ferramentas)
    incluidos = _incluidos(
        catalogo, escolha.incluir_grupos - grupos_da_semente, no_escopo, ferramentas
    )

    servicos_escopo: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for x in no_escopo:
        for s, c in custo.get(x, {}).items():
            servicos_escopo[s] += c

    ligacoes_pares = agrupar_por_par(
        tuple(
            d
            for d in catalogo.deps_de_servico
            if d.unidade_de in no_escopo and d.unidade_para in no_escopo
        )
    )
    ligacoes = tuple(
        LigacaoNoEscopo(de, para, n, portas)
        for (de, para), (n, portas, _) in ligacoes_pares.items()
    )

    vem_junto = []
    if escolha.servicos:
        for s, c in sorted(servicos_escopo.items(), key=lambda kv: (-kv[1], kv[0])):
            if s in escolha.servicos or c <= 0:
                continue
            motivo = next(
                (
                    f"{de} {_verbo(n)} {s}" + (f" ({', '.join(portas)})" if portas else "")
                    for (de, para), (n, portas, _) in ligacoes_pares.items()
                    if para == s and de in escolha.servicos
                ),
                None,
            ) or next(
                (
                    f"{s} chama {para}" + (f" ({', '.join(portas)})" if portas else "")
                    for (de, para), (n, portas, _) in ligacoes_pares.items()
                    if de == s and para in escolha.servicos
                ),
                "Faz parte das mesmas aplicações",
            )
            vem_junto.append(ServicoQueVemJunto(servico=s, custo=c, motivo=motivo))

    onda_do_grupo = {g: o.numero for o in analise.ondas for g in o.grupos}
    nome_do_grupo = {g.id: g.nome for g in analise.grupos}
    grupos_no_escopo = {analise.grupo_de[x] for x in no_escopo if x in analise.grupo_de}
    por_ordem: dict[int | None, list[str]] = defaultdict(list)
    for g in sorted(grupos_no_escopo, key=lambda g: nome_do_grupo.get(g, g)):
        por_ordem[onda_do_grupo.get(g)].append(nome_do_grupo.get(g, g))
    ordens = tuple(
        OrdemDoEscopo(ordem=o, grupos=tuple(gs))
        for o, gs in sorted(por_ordem.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    )

    sem = {s.nome: s.sem_aplicacao for s in catalogo.servicos}
    return PlanoDeMigracao(
        escolha=escolha,
        unidades=tuple(sorted(no_escopo)),
        aplicacoes=tuple(sorted({_aplicacao_de(unidades[x]) for x in no_escopo})),
        servicos=tuple(sorted(servicos_escopo.items(), key=lambda kv: (-kv[1], kv[0]))),
        recursos=sum(catalogo.recursos_por_unidade.get(x, 0) for x in no_escopo),
        custo=sum(servicos_escopo.values(), _ZERO),
        vao_junto=vao_junto,
        servicos_que_vem_junto=tuple(vem_junto),
        antes=antes,
        para_tras=para_tras,
        ligacoes=ligacoes,
        ordens=ordens,
        fora_do_plano=tuple((s, sem[s]) for s in sorted(escolha.servicos) if sem.get(s, _ZERO) > 0),
        incluidos=incluidos,
    )


def _validar(escolha: Escolha, apps: set[str], servicos: set[str], grupos: set[str]) -> None:
    if not escolha.aplicacoes and not escolha.servicos:
        raise EscolhaInvalidaError("escolha ao menos uma aplicação ou um serviço")
    erros = [
        *(f"aplicação {a!r} não existe nesta análise" for a in sorted(escolha.aplicacoes - apps)),
        *(f"serviço {s!r} não existe nesta análise" for s in sorted(escolha.servicos - servicos)),
        *(f"grupo {g!r} não existe nesta análise" for g in sorted(escolha.incluir_grupos - grupos)),
    ]
    if erros:
        raise EscolhaInvalidaError("; ".join(erros))


_VERBO = {
    Natureza.DADOS: "acessa dados em",
    Natureza.ENCAMINHAMENTO: "encaminha tráfego para",
    Natureza.SERVICO: "chama",
}


def _verbo(n: Natureza) -> str:
    return _VERBO.get(n, "depende de")


def _motivo(
    analise: AnaliseDeTopologia, x: str, no_escopo: set[str], unidades: Mapping[str, Unidade]
) -> str:
    """Por que a unidade ``x`` vai junto: a dependência forte com outra do escopo."""
    for d in analise.dependencias:
        if d.acoplamento is not Acoplamento.FORTE:
            continue
        if d.de == x and d.para in no_escopo:
            outra = unidades[d.para].nome
        elif d.para == x and d.de in no_escopo:
            outra = unidades[d.de].nome
        else:
            continue
        portas = f" ({', '.join(d.portas)})" if d.portas else ""
        if d.natureza is Natureza.DADOS:
            return f"Compartilha banco com {outra}{portas}"
        if d.natureza is Natureza.ENCAMINHAMENTO:
            return f"Recebe tráfego encaminhado de {outra}{portas}"
        return f"Dependência forte com {outra}{portas}"
    return "Mesmo grupo de migração"


@dataclass
class _Ligacoes:
    """O que liga o escopo a um grupo de fora: naturezas e portas."""

    naturezas: set[Natureza] = field(default_factory=set)
    portas: set[str] = field(default_factory=set)
    grupos: set[str] = field(default_factory=set)  # do escopo


def _fracas_de_fora(
    analise: AnaliseDeTopologia, ferramentas: set[str]
) -> Iterable[tuple[str, str, Natureza, tuple[str, ...]]]:
    """Dependências fracas entre unidades que entram em plano (sem ferramenta de acesso)."""
    unidades = {u.id: u for u in analise.unidades}
    for d in analise.dependencias:
        if d.acoplamento is not Acoplamento.FRACO:
            continue
        if {_aplicacao_de(unidades[d.de]), _aplicacao_de(unidades[d.para])} & ferramentas:
            continue
        yield d.de, d.para, d.natureza, d.portas


def _custo_do_grupo(catalogo: CatalogoDeAplicacoes, membros: Iterable[str]) -> Decimal:
    return sum((sum(catalogo.custo_por_unidade.get(x, {}).values(), _ZERO) for x in membros), _ZERO)


def _incluidos(
    catalogo: CatalogoDeAplicacoes,
    grupos: Iterable[str],
    no_escopo: set[str],
    ferramentas: set[str],
) -> tuple[GrupoIncluido, ...]:
    """De que lado da migração fica cada grupo incluído à mão.

    O lado sai do sentido das ligações com o resto do plano, não de onde o
    usuário clicou: o mesmo plano pedido de novo dá o mesmo resultado.
    """
    analise = catalogo.analise
    unidades = {u.id: u for u in analise.unidades}
    onda_do_grupo = {g: o.numero for o in analise.ondas for g in o.grupos}
    por_id = {g.id: g for g in analise.grupos}
    nome_do_grupo = {g.id: g.nome for g in analise.grupos}
    saida = []
    for g in grupos:
        membros = set(por_id[g].unidades)
        resto = no_escopo - membros
        chamado, chama = _Ligacoes(), _Ligacoes()
        for de, para, natureza, portas in _fracas_de_fora(analise, ferramentas):
            if de in resto and para in membros:
                alvo, outro = chamado, de
            elif de in membros and para in resto:
                alvo, outro = chama, para
            else:
                continue
            alvo.naturezas.add(natureza)
            alvo.portas.update(portas)
            alvo.grupos.add(analise.grupo_de.get(outro, outro))
        lado = ANTES if chamado.grupos else DEPOIS if chama.grupos else SEM_LIGACAO
        saida.append(
            GrupoIncluido(
                grupo=g,
                nome=nome_do_grupo.get(g, g),
                lado=lado,
                ordem=onda_do_grupo.get(g),
                custo=_custo_do_grupo(catalogo, membros),
                aplicacoes=tuple(sorted({_aplicacao_de(unidades[x]) for x in membros})),
                # Pré: quem do plano o chama; pós: quem do plano ele chama
                ligado_a=tuple(
                    sorted(
                        nome_do_grupo.get(x, x)
                        for x in (chamado.grupos if lado == ANTES else chama.grupos)
                    )
                ),
                naturezas=tuple(sorted(chamado.naturezas | chama.naturezas)),
                portas=tuple(sorted(chamado.portas | chama.portas)),
            )
        )
    return tuple(sorted(saida, key=lambda i: (i.ordem is None, i.ordem or 0, i.nome)))


def _vizinhos(
    catalogo: CatalogoDeAplicacoes, no_escopo: set[str], ferramentas: set[str]
) -> tuple[tuple[GrupoVizinho, ...], tuple[GrupoVizinho, ...]]:
    analise = catalogo.analise
    onda_do_grupo = {g: o.numero for o in analise.ondas for g in o.grupos}
    nome_do_grupo = {g.id: g.nome for g in analise.grupos}
    membros = {g.id: g.unidades for g in analise.grupos}

    def acumular(
        alvo: dict[str, _Ligacoes],
        grupo: str,
        natureza: Natureza,
        portas: Iterable[str],
        do_escopo: str,
    ) -> None:
        cur = alvo.setdefault(grupo, _Ligacoes())
        cur.naturezas.add(natureza)
        cur.portas.update(portas)
        cur.grupos.add(analise.grupo_de.get(do_escopo, do_escopo))

    antes: dict[str, _Ligacoes] = {}
    depois: dict[str, _Ligacoes] = {}
    for de, para, natureza, portas in _fracas_de_fora(analise, ferramentas):
        if de in no_escopo and para not in no_escopo and para in analise.grupo_de:
            acumular(antes, analise.grupo_de[para], natureza, portas, de)
        elif para in no_escopo and de not in no_escopo and de in analise.grupo_de:
            acumular(depois, analise.grupo_de[de], natureza, portas, para)

    def vizinho(g: str, info: _Ligacoes, dois_sentidos: bool = False) -> GrupoVizinho:
        return GrupoVizinho(
            grupo=g,
            nome=nome_do_grupo.get(g, g),
            ordem=onda_do_grupo.get(g),
            naturezas=tuple(sorted(info.naturezas)),
            portas=tuple(sorted(info.portas)),
            custo=_custo_do_grupo(catalogo, membros[g]),
            nos_dois_sentidos=dois_sentidos,
            ligado_a=tuple(sorted(nome_do_grupo.get(x, x) for x in info.grupos)),
        )

    def ordenar(vs: Iterable[GrupoVizinho]) -> tuple[GrupoVizinho, ...]:
        return tuple(sorted(vs, key=lambda v: (v.ordem is None, v.ordem or 0, v.nome)))

    # Conversa nos dois sentidos: uma linha só, em "antes", com o que vem dos dois lados
    for g, info in antes.items():
        if g in depois:
            info.naturezas |= depois[g].naturezas
            info.portas |= depois[g].portas
            info.grupos |= depois[g].grupos
    return (
        ordenar(vizinho(g, info, g in depois) for g, info in antes.items()),
        ordenar(vizinho(g, info) for g, info in depois.items() if g not in antes),
    )
