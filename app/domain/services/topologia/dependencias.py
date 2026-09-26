"""Dependências entre unidades (RI5).

Uma regra de entrada que permite, no grupo ``G``, diz que a origem pode falar
com os membros de ``G``. Cada par origem → alvo em unidades diferentes vira
"a unidade da origem depende da unidade do alvo".

É regra, não tráfego: o que a configuração permite, não o que acontece. Por
isso a inferência é conservadora onde a regra é genérica demais para dizer
alguma coisa — e cada caso descartado vira um achado com o motivo, em vez de
sumir.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.domain.entities.topologia import (
    DADOS,
    Acao,
    Direcao,
    RegraDeRede,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.domain.services.topologia.declaracoes import (
    PREFIXO_APLICACAO,
    Declaracoes,
)
from app.domain.services.topologia.indice import Indice, rede
from app.domain.services.topologia.modelo import (
    Acoplamento,
    Dependencia,
    Evidencia,
    Natureza,
    Unidade,
)

PORTAS_ADMINISTRATIVAS = frozenset({22, 3389, 5985, 5986})
PORTAS_DE_BANCO = frozenset({1433, 1521, 1522, 3306, 5432, 6379, 11211, 27017, 9042, 9200})
TETO_DE_PARES = 5000
# Faixa larga (1024-65535) cobre a porta do banco por acaso: não diz "banco".
_FAIXA_MAXIMA_DE_BANCO = 16
_MAX_EVIDENCIAS = 5

# Mais forte primeiro: é a ordem em que duas naturezas da mesma aresta disputam.
_FORCA = {
    Natureza.ENCAMINHAMENTO: 4,
    Natureza.DADOS: 4,
    Natureza.DECLARADA: 3,
    Natureza.SERVICO: 2,
    Natureza.ADMINISTRATIVO: 1,
}


@dataclass(frozen=True, slots=True)
class Achado:
    """Uma regra ou declaração que não virou dependência, e por quê."""

    regra: str  # "regra_ampla", "grupo_interno", "origem_externa", "origem_nao_identificada"
    sujeito: str  # grupo de segurança ou declaração
    detalhe: str


@dataclass
class _Aresta:
    natureza: Natureza
    acoplamento: Acoplamento
    portas: set[str] = field(default_factory=set)
    evidencias: list[Evidencia] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResultadoDasDependencias:
    dependencias: tuple[Dependencia, ...]
    achados: tuple[Achado, ...]
    # balanceador → unidades para onde encaminha, contando a dele (RI7.2)
    destinos_do_balanceador: Mapping[str, frozenset[str]]
    # declarações que não acharam unidade
    ignoradas: tuple[str, ...]


def natureza_da_regra(regra: RegraDeRede) -> Natureza | None:
    """Administrativo quando a regra só libera porta de administração."""
    if regra.todas_as_portas or regra.porta_inicio is None:
        return None
    fim = regra.porta_fim if regra.porta_fim is not None else regra.porta_inicio
    portas = range(regra.porta_inicio, fim + 1)
    if len(portas) <= len(PORTAS_ADMINISTRATIVAS) and all(
        p in PORTAS_ADMINISTRATIVAS for p in portas
    ):
        return Natureza.ADMINISTRATIVO
    return None


def libera_porta_de_banco(regra: RegraDeRede) -> bool:
    if regra.todas_as_portas or regra.porta_inicio is None:
        return False
    fim = regra.porta_fim if regra.porta_fim is not None else regra.porta_inicio
    return fim - regra.porta_inicio < _FAIXA_MAXIMA_DE_BANCO and any(
        regra.porta_inicio <= p <= fim for p in PORTAS_DE_BANCO
    )


def chave_de_regra(r: RegraDeRede) -> tuple[object, ...]:
    return (
        r.grupo,
        r.direcao.value,
        r.acao.value,
        r.protocolo,
        -1 if r.porta_inicio is None else r.porta_inicio,
        -1 if r.porta_fim is None else r.porta_fim,
        r.origem.tipo.value,
        r.origem.valor or "",
        r.destino.tipo.value if r.destino else "",
        (r.destino.valor or "") if r.destino else "",
        -1 if r.prioridade is None else r.prioridade,
        r.descricao or "",
    )


def rotulo_de_porta(regra: RegraDeRede) -> str:
    if regra.todas_as_portas:
        return f"{regra.protocolo}/todas"
    if regra.porta_inicio is None:
        return regra.protocolo
    if regra.porta_fim in (None, regra.porta_inicio):
        return f"{regra.protocolo}/{regra.porta_inicio}"
    return f"{regra.protocolo}/{regra.porta_inicio}-{regra.porta_fim}"


def derivar_dependencias(
    indice: Indice,
    unidades: Mapping[str, Unidade],
    unidade_de: Mapping[str, str],
    declaracoes: Declaracoes,
    chave_aplicacao: str | None,
) -> ResultadoDasDependencias:
    d = _Derivacao(indice, unidade_de)
    # Ordem canônica: a evidência guardada (as 5 primeiras) e o desempate entre
    # naturezas de mesma força não podem depender da ordem do arquivo.
    for regra in sorted(indice.inventario.regras, key=chave_de_regra):
        if regra.direcao is Direcao.ENTRADA and regra.acao is Acao.PERMITIR:
            d.regra(regra)
    d.encaminhamentos()
    ignoradas = _declaradas(unidades, unidade_de, declaracoes, chave_aplicacao, d)
    dependencias = tuple(
        Dependencia(
            de=u,
            para=v,
            natureza=a.natureza,
            acoplamento=a.acoplamento,
            portas=tuple(sorted(a.portas)),
            evidencias=tuple(a.evidencias),
        )
        for (u, v), a in sorted(d.arestas.items())
    )
    return ResultadoDasDependencias(
        dependencias=dependencias,
        achados=tuple(sorted(set(d.achados), key=lambda x: (x.regra, x.sujeito, x.detalhe))),
        destinos_do_balanceador={k: frozenset(v) for k, v in sorted(d.destinos_lb.items())},
        ignoradas=ignoradas,
    )


class _Derivacao:
    """Estado da derivação: arestas por par de unidades, achados e o destino de
    cada balanceador (que a RI7.2 precisa, incluindo a unidade do próprio)."""

    def __init__(self, indice: Indice, unidade_de: Mapping[str, str]) -> None:
        self.indice = indice
        self.unidade_de = unidade_de
        self.arestas: dict[tuple[str, str], _Aresta] = {}
        self.achados: list[Achado] = []
        self.destinos_lb: dict[str, set[str]] = defaultdict(set)
        self._grupos_internos: set[str] = set()

    def registrar(
        self,
        u: str,
        v: str,
        natureza: Natureza,
        *,
        ev: Evidencia,
        porta: str | None = None,
        acoplamento: Acoplamento | None = None,
    ) -> None:
        if u == v:
            return
        acopl = acoplamento or _acoplamento(natureza)
        atual = self.arestas.get((u, v))
        if atual is None:
            atual = self.arestas[(u, v)] = _Aresta(natureza, acopl)
        elif _FORCA[natureza] > _FORCA[atual.natureza]:
            atual.natureza, atual.acoplamento = natureza, acopl
        if porta:
            atual.portas.add(porta)
        if len(atual.evidencias) < _MAX_EVIDENCIAS and ev not in atual.evidencias:
            atual.evidencias.append(ev)

    def regra(self, regra: RegraDeRede) -> None:
        grupo = self.indice.recursos.get(regra.grupo)
        if grupo is None:
            return
        if grupo.atributos.get("amplo"):
            self.achados.append(
                Achado("regra_ampla", regra.grupo, "o grupo vale para a rede inteira")
            )
            return
        alvos = _alvos(self.indice, regra)
        fontes = _fontes(self.indice, regra, self._grupos_internos, self.achados)
        if not fontes or not alvos:
            return
        # Por unidade, não por par de recursos: uma regra entre dois grupos de
        # 1.000 máquinas seriam 1 milhão de pares; por unidade, poucos.
        balanceadores: dict[str, list[str]] = defaultdict(list)
        outras_fontes: set[str] = set()
        for f in fontes:
            u = self.unidade_de.get(f)
            if u is not None and self.indice.tipo(f) is TipoDeRecurso.BALANCEADOR:
                balanceadores[u].append(f)
            elif u is not None:
                outras_fontes.add(u)
        destinos = {v for a in alvos if (v := self.unidade_de.get(a))}
        com_dados = {
            v for a in alvos if (v := self.unidade_de.get(a)) and self.indice.tipo(a) in DADOS
        }
        if (len(balanceadores) + len(outras_fontes)) * len(destinos) > TETO_DE_PARES:
            self.achados.append(
                Achado(
                    "regra_ampla",
                    regra.grupo,
                    f"a regra ligaria mais de {TETO_DE_PARES} pares de unidades",
                )
            )
            return
        porta = rotulo_de_porta(regra)
        administrativa = natureza_da_regra(regra)
        porta_de_banco = libera_porta_de_banco(regra)
        ev = Evidencia(
            origem="regra",
            grupo=regra.grupo,
            descricao=f"{porta} liberada em {grupo.nome or regra.grupo}",
        )
        for v in sorted(destinos):
            for u, lbs in sorted(balanceadores.items()):
                if administrativa is None:
                    for lb in lbs:
                        self.destinos_lb[lb].add(v)
                self.registrar(u, v, administrativa or Natureza.ENCAMINHAMENTO, ev=ev, porta=porta)
            natureza = administrativa or (
                Natureza.DADOS if v in com_dados or porta_de_banco else Natureza.SERVICO
            )
            for u in sorted(outras_fontes):
                self.registrar(u, v, natureza, ev=ev, porta=porta)

    def encaminhamentos(self) -> None:
        """Encaminhamento dito pelo provedor (backend pool, ELB clássico)."""
        encaminhamentos = sorted(
            (r for r in self.indice.inventario.relacoes if r.tipo is TipoDeRelacao.ENCAMINHA_PARA),
            key=lambda r: (r.de, r.para),
        )
        for rel in encaminhamentos:
            u = self.unidade_de.get(rel.de)
            if self.indice.tipo(rel.para) is TipoDeRecurso.INTERFACE_REDE:
                alvos = set(self.indice.donos(rel.para))
            else:
                alvos = {rel.para}
            for a in sorted(alvos):
                v = self.unidade_de.get(a)
                if u is None or v is None:
                    continue
                self.destinos_lb[rel.de].add(v)
                self.registrar(
                    u,
                    v,
                    Natureza.ENCAMINHAMENTO,
                    ev=Evidencia(
                        origem="relacao",
                        descricao="balanceador encaminha para o destino",
                        de_recurso=rel.de,
                        para_recurso=a,
                    ),
                )


def _acoplamento(natureza: Natureza) -> Acoplamento:
    if natureza in (Natureza.DADOS, Natureza.ENCAMINHAMENTO):
        return Acoplamento.FORTE
    if natureza is Natureza.ADMINISTRATIVO:
        return Acoplamento.NENHUM
    return Acoplamento.FRACO


def _alvos(indice: Indice, regra: RegraDeRede) -> frozenset[str]:
    alvos = indice.membros(regra.grupo)
    destino = regra.destino
    if destino is None:
        return alvos
    if destino.tipo is TipoDeSeletor.GRUPO and destino.valor:
        return alvos & indice.membros(destino.valor)
    if destino.tipo is TipoDeSeletor.CIDR and destino.valor:
        faixa = rede(destino.valor)
        com_ip = {a for a in alvos if indice.ips(a)}
        if faixa is None or not com_ip:
            return alvos  # sem IP para estreitar: infere a mais, nunca a menos (RI4)
        return frozenset(
            a
            for a in com_ip
            if any(ip.version == faixa.version and ip in faixa for ip in indice.ips(a))
        )
    return alvos


def _fontes(
    indice: Indice,
    regra: RegraDeRede,
    grupos_internos: set[str],
    achados: list[Achado],
) -> frozenset[str]:
    origem = regra.origem
    if origem.tipo is TipoDeSeletor.GRUPO and origem.valor:
        if origem.valor == regra.grupo:
            if regra.grupo not in grupos_internos:
                grupos_internos.add(regra.grupo)
                achados.append(
                    Achado("grupo_interno", regra.grupo, "o grupo libera os próprios membros")
                )
            return frozenset()
        return indice.membros(origem.valor)
    if origem.tipo is TipoDeSeletor.CIDR and origem.valor:
        return _fontes_por_cidr(indice, regra, origem.valor, achados)
    return frozenset()  # internet e rótulo do provedor: exposição, não dependência


def _fontes_por_cidr(
    indice: Indice, regra: RegraDeRede, cidr: str, achados: list[Achado]
) -> frozenset[str]:
    faixa = rede(cidr)
    if faixa is None:
        return frozenset()
    tocadas = sorted(
        s
        for s, r in indice.redes_das_subredes.items()
        if r.version == faixa.version and r.overlaps(faixa)
    )
    if not tocadas:
        achados.append(
            Achado("origem_externa", regra.grupo, f"{cidr} fica fora das subredes do inventário")
        )
        return frozenset()
    if len(tocadas) > 1:
        achados.append(Achado("regra_ampla", regra.grupo, f"{cidr} cobre {len(tocadas)} subredes"))
        return frozenset()
    subrede = tocadas[0]
    dentro = indice.recursos_por_subrede.get(subrede, frozenset())
    if faixa.supernet_of(indice.redes_das_subredes[subrede]):  # type: ignore[arg-type]
        return dentro
    com_ip = [r for r in dentro if indice.ips(r)]
    if not com_ip:
        achados.append(
            Achado(
                "origem_nao_identificada",
                regra.grupo,
                f"{cidr} é um pedaço da subrede e os recursos não têm IP conhecido",
            )
        )
        return frozenset()
    return frozenset(
        r
        for r in com_ip
        if any(ip.version == faixa.version and ip in faixa for ip in indice.ips(r))
    )


def _declaradas(
    unidades: Mapping[str, Unidade],
    unidade_de: Mapping[str, str],
    declaracoes: Declaracoes,
    chave_aplicacao: str | None,
    derivacao: _Derivacao,
) -> tuple[str, ...]:
    ignoradas: list[str] = []
    for d in declaracoes.dependencias:
        origens = resolver_referencia(d.de, unidades, unidade_de, chave_aplicacao)
        destinos = resolver_referencia(d.para, unidades, unidade_de, chave_aplicacao)
        if not origens or not destinos:
            ignoradas.append(f"{d.de} -> {d.para}")
            continue
        pares = [
            (u, v)
            for u in origens
            for v in destinos
            if u != v and _mesmo_escopo(unidades[u], unidades[v], d.de, d.para)
        ]
        if not pares:
            ignoradas.append(f"{d.de} -> {d.para}")
            continue
        ev = Evidencia(origem="declaracao", descricao=d.motivo or "declarada pelo cliente")
        for u, v in pares:
            derivacao.registrar(u, v, Natureza.DECLARADA, ev=ev, acoplamento=d.acoplamento)
    return tuple(ignoradas)


def resolver_referencia(
    ref: str,
    unidades: Mapping[str, Unidade],
    unidade_de: Mapping[str, str],
    chave_aplicacao: str | None,
) -> list[str]:
    """``aplicacao:<valor>`` → unidades da aplicação; chave de recurso → a unidade dele."""
    if ref.startswith(PREFIXO_APLICACAO):
        if not chave_aplicacao:
            return []
        valor = ref[len(PREFIXO_APLICACAO) :]
        return sorted(u.id for u in unidades.values() if u.aplicacao == valor)
    unidade = unidade_de.get(ref)
    return [unidade] if unidade else []


def _mesmo_escopo(a: Unidade, b: Unidade, ref_a: str, ref_b: str) -> bool:
    """Entre aplicações, só no mesmo escopo: o dev de uma não depende do prod
    da outra (§4). Entre recursos nomeados, vale o que foi declarado."""
    if not (ref_a.startswith(PREFIXO_APLICACAO) and ref_b.startswith(PREFIXO_APLICACAO)):
        return True
    return (a.conta, a.regiao, a.ambiente) == (b.conta, b.regiao, b.ambiente)


def portas_de(dependencias: Iterable[Dependencia]) -> tuple[str, ...]:
    return tuple(sorted({p for d in dependencias for p in d.portas}))
