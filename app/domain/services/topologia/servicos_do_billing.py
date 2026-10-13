"""Dependências por serviço da fatura (PLANO_LOCKIN_INVENTARIO, B1 e B2).

A topologia sabe o que migra junto: agrupa recursos em unidades, acha as
dependências entre elas e junta as fortes em grupos de movimentação. O billing
detalhado sabe de qual serviço é cada recurso. Juntando os dois, cada serviço
da fatura ganha a segunda dimensão do acoplamento: a dificuldade de migrar por
causa do que precisa ir junto ou antes.

Nível da dependência:

- ``high``: o serviço tem um banco que outras aplicações acessam, ou está num
  grupo de movimentação grande. Separar é latência em cada consulta, ou uma
  migração só para muita coisa;
- ``medium``: está num grupo com recursos de outros serviços, que migram juntos;
- ``low``: nenhuma dependência forte com outro serviço. Pode ter dependência de
  ordem (``depende_de``), que só define a sequência.

Dependência que a topologia rebaixou (ferramenta, hub, balanceador
compartilhado) já chega como fraca e não junta nada, pela mesma regra que
impede um grupo de engolir o ambiente inteiro.

Função pura, sem I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.entities.topologia import Inventario
from app.domain.services.correlacao_inventario import CustosDoBilling, IndiceDeApelidos
from app.domain.services.topologia.modelo import Acoplamento, AnaliseDeTopologia

_ZERO = Decimal(0)
_MESMA_UNIDADE = "mesma_unidade"
_MESMO_GRUPO = "mesmo_grupo"
# Da mais concreta para a mais vaga: é a que aparece quando há mais de uma.
_ORDEM_DA_NATUREZA = (
    _MESMA_UNIDADE,
    "dados",
    "encaminhamento",
    "servico",
    "declarada",
    "administrativo",
    _MESMO_GRUPO,
)


@dataclass(frozen=True, slots=True)
class Vizinho:
    servico: str
    custo: Decimal
    natureza: str


@dataclass(frozen=True, slots=True)
class DependenciasDoServico:
    servico: str
    nivel: str  # low | medium | high
    motivo: str
    grupos: tuple[str, ...]
    ondas: tuple[int, ...]
    migra_junto_com: tuple[Vizinho, ...]
    depende_de: tuple[Vizinho, ...]
    dependentes: int
    banco_compartilhado: bool
    grupo_grande: bool
    custo: Decimal
    custo_no_inventario: Decimal
    custo_do_grupo: Decimal
    regioes: tuple[str, ...]

    @property
    def cobertura(self) -> Decimal | None:
        if self.custo <= 0:
            return None
        return (self.custo_no_inventario / self.custo).quantize(Decimal("0.0001"))


def dependencias_por_servico(
    inventario: Inventario, analise: AnaliseDeTopologia, custos: CustosDoBilling
) -> dict[str, DependenciasDoServico]:
    """Só os serviços com ao menos um recurso encontrado no inventário."""
    apelidos = IndiceDeApelidos(inventario)
    regiao_do_recurso = {r.chave: r.regiao for r in inventario.recursos}

    custo: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    casado: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    chaves: dict[str, set[str]] = defaultdict(set)
    regioes: dict[str, set[str]] = defaultdict(set)
    por_unidade: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    for linha in custos.por_recurso:
        custo[linha.servico] += linha.custo
        chave = apelidos.casar(linha.resource_id)
        if chave is None:
            continue
        casado[linha.servico] += linha.custo
        chaves[linha.servico].add(chave)
        if regiao := (regiao_do_recurso.get(chave) or linha.regiao):
            regioes[linha.servico].add(regiao)
        unidade = analise.unidade_de.get(chave)
        if unidade is not None:
            por_unidade[unidade][linha.servico] += linha.custo

    unidades: dict[str, set[str]] = defaultdict(set)
    for unidade, servicos in por_unidade.items():
        for s in servicos:
            unidades[s].add(unidade)
    grupo_de = analise.grupo_de
    por_grupo: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    for unidade, servicos in por_unidade.items():
        if (g := grupo_de.get(unidade)) is not None:
            for s, c in servicos.items():
                por_grupo[g][s] += c
    nome_do_grupo = {g.id: g.nome for g in analise.grupos}
    onda_do_grupo = {g: o.numero for o in analise.ondas for g in o.grupos}
    compartilhados = _recursos_do_risco(analise, "banco_compartilhado")
    grandes = _recursos_do_risco(analise, "grupo_acoplado_grande")

    resultado: dict[str, DependenciasDoServico] = {}
    for servico in sorted(chaves):
        minhas = unidades.get(servico, set())
        grupos = {grupo_de[u] for u in minhas if u in grupo_de}
        junto = _migra_junto(
            servico,
            minhas=minhas,
            grupos=grupos,
            por_grupo=por_grupo,
            por_unidade=por_unidade,
            unidades=unidades,
            analise=analise,
        )
        nomes_junto = {v.servico for v in junto}
        ordem = _depende_de(servico, minhas, unidades, analise, ignorar=nomes_junto)
        banco = bool(chaves[servico] & compartilhados)
        grande = bool(minhas & grandes)
        nivel, motivo = _nivel(junto, ordem, banco=banco, grande=grande)
        resultado[servico] = DependenciasDoServico(
            servico=servico,
            nivel=nivel,
            motivo=motivo,
            grupos=tuple(sorted(nome_do_grupo.get(g, g) for g in grupos)),
            ondas=tuple(sorted({onda_do_grupo[g] for g in grupos if g in onda_do_grupo})),
            migra_junto_com=junto,
            depende_de=ordem,
            dependentes=_dependentes(servico, minhas, unidades, analise),
            banco_compartilhado=banco,
            grupo_grande=grande,
            custo=custo[servico],
            custo_no_inventario=casado[servico],
            custo_do_grupo=sum((c for g in grupos for c in por_grupo[g].values()), _ZERO),
            regioes=tuple(sorted(regioes[servico])),
        )
    return resultado


def _migra_junto(
    servico: str,
    *,
    minhas: set[str],
    grupos: set[str],
    por_grupo: Mapping[str, Mapping[str, Decimal]],
    por_unidade: Mapping[str, Mapping[str, Decimal]],
    unidades: Mapping[str, set[str]],
    analise: AnaliseDeTopologia,
) -> tuple[Vizinho, ...]:
    custos: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    naturezas: dict[str, set[str]] = defaultdict(set)
    for g in grupos:
        for outro, c in por_grupo[g].items():
            if outro != servico:
                custos[outro] += c
                naturezas[outro].add(_MESMO_GRUPO)
    for u in minhas:
        for outro in por_unidade[u]:
            if outro != servico:
                naturezas[outro].add(_MESMA_UNIDADE)
    for d in analise.dependencias:
        if d.acoplamento is not Acoplamento.FORTE:
            continue
        for outro in custos:
            alheias = unidades.get(outro, set())
            if (d.de in minhas and d.para in alheias) or (d.para in minhas and d.de in alheias):
                naturezas[outro].add(d.natureza.value)
    return tuple(
        Vizinho(servico=s, custo=custos[s], natureza=_mais_concreta(naturezas[s]))
        for s in sorted(custos, key=lambda s: (-custos[s], s))
    )


def _depende_de(
    servico: str,
    minhas: set[str],
    unidades: Mapping[str, set[str]],
    analise: AnaliseDeTopologia,
    *,
    ignorar: set[str],
) -> tuple[Vizinho, ...]:
    """Dependências de ordem: o outro serviço precisa estar no destino antes."""
    naturezas: dict[str, set[str]] = defaultdict(set)
    for d in analise.dependencias:
        if d.acoplamento is not Acoplamento.FRACO or d.de not in minhas:
            continue
        for outro, alheias in unidades.items():
            if outro != servico and outro not in ignorar and d.para in alheias:
                naturezas[outro].add(d.natureza.value)
    return tuple(
        Vizinho(servico=s, custo=_ZERO, natureza=_mais_concreta(n))
        for s, n in sorted(naturezas.items())
    )


def _dependentes(
    servico: str, minhas: set[str], unidades: Mapping[str, set[str]], analise: AnaliseDeTopologia
) -> int:
    """Quantos outros serviços dependem deste (forte ou fraco)."""
    quem: set[str] = set()
    for d in analise.dependencias:
        if d.acoplamento is Acoplamento.NENHUM or d.para not in minhas:
            continue
        quem.update(s for s, us in unidades.items() if s != servico and d.de in us)
    return len(quem)


def _nivel(
    junto: Iterable[Vizinho], ordem: Iterable[Vizinho], *, banco: bool, grande: bool
) -> tuple[str, str]:
    nomes = [v.servico for v in junto]
    if banco:
        return "high", (
            "Banco acessado por outras aplicações: migram juntos, ou exigem janela "
            "conjunta ou replicação."
        )
    if grande:
        return "high", (
            "Está num grupo de movimentação grande: muita coisa presa por acoplamento "
            "forte, uma migração só ou quebrar o acoplamento antes."
        )
    if nomes:
        return "medium", f"Migra junto com {', '.join(nomes)}."
    antes = [v.servico for v in ordem]
    if antes:
        return "low", (
            f"Sem dependência forte com outro serviço; migra depois de {', '.join(antes)}."
        )
    return "low", "Sem dependência forte com outro serviço; pode migrar sozinho."


def _recursos_do_risco(analise: AnaliseDeTopologia, regra: str) -> set[str]:
    return {r for risco in analise.riscos if risco.regra == regra for r in risco.recursos}


def _mais_concreta(naturezas: set[str]) -> str:
    return min(
        naturezas,
        key=lambda n: (
            _ORDEM_DA_NATUREZA.index(n) if n in _ORDEM_DA_NATUREZA else len(_ORDEM_DA_NATUREZA)
        ),
    )
