"""Riscos (RI11) e recursos ociosos (RI12).

Cada risco sai com a regra que o gerou, a severidade e exemplos. Os achados da
inferência (regra ampla, grupo interno, ferramenta…) também saem, como
informativos: são o "por que esta dependência não aparece", e sem eles o
cliente leria ausência de aresta como ausência de dependência.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from app.domain.entities.topologia import (
    DADOS,
    Acao,
    Direcao,
    EstadoDoRecurso,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.domain.services.topologia.acoplamento import BancoCompartilhado
from app.domain.services.topologia.dependencias import (
    PORTAS_ADMINISTRATIVAS,
    PORTAS_DE_BANCO,
    Achado,
)
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import (
    Criticidade,
    Dependencia,
    Natureza,
    Nivel,
    Ocioso,
    Risco,
    Severidade,
    Unidade,
)

MAX_EXEMPLOS = 10

_TITULOS_DOS_ACHADOS = {
    "regra_ampla": "Regras amplas demais para indicar dependência",
    "grupo_interno": "Grupos que liberam os próprios membros",
    "origem_externa": "Origens fora do inventário (escritório, parceiro)",
    "origem_nao_identificada": "Origens por IP sem recurso identificável",
    "ferramenta_administrativa": "Ferramentas de administração (fora das ondas)",
    "hub_de_dados": "Acesso a dados de muitas aplicações (rebaixado a fraco)",
    "balanceador_compartilhado": "Balanceadores compartilhados (rebaixados a fraco)",
}
_DETALHE_DOS_ACHADOS = {
    "regra_ampla": "Nenhuma dependência inferida dessas regras: cobrem várias subredes ou a rede "
    "inteira. Declare as dependências reais, se houver.",
    "grupo_interno": "Membros do mesmo grupo podem se falar; isso não vira dependência entre "
    "aplicações diferentes que compartilham o grupo.",
    "origem_externa": "A origem não é recurso do inventário: continua onde está e fala com o "
    "destino pela conectividade híbrida.",
    "origem_nao_identificada": "A regra cita um pedaço de subrede e os recursos não têm IP no "
    "inventário: a origem não pôde ser identificada.",
    "ferramenta_administrativa": "Precisa alcançar as duas nuvens durante a migração e migra por "
    "último. As dependências dela não entram nas ondas nem na criticidade.",
    "hub_de_dados": "Acessa bancos de várias aplicações (monitoramento, ETL): não junta essas "
    "aplicações num grupo; depende delas.",
    "balanceador_compartilhado": "Encaminha para várias aplicações: não junta essas aplicações "
    "num grupo; migra depois delas.",
}


def _risco(
    regra: str, severidade: Severidade, titulo: str, detalhe: str, recursos: Iterable[str]
) -> Risco:
    lista = sorted(set(recursos))
    return Risco(regra, severidade, titulo, detalhe, tuple(lista[:MAX_EXEMPLOS]), len(lista))


def riscos_de_exposicao(indice: Indice) -> list[Risco]:
    """Entrada liberada para a internet: administração, banco, tudo aberto."""
    portas: dict[tuple[str, str], set[str]] = defaultdict(set)
    for regra in indice.inventario.regras:
        if (
            regra.direcao is not Direcao.ENTRADA
            or regra.acao is not Acao.PERMITIR
            or regra.origem.tipo is not TipoDeSeletor.QUALQUER
        ):
            continue
        if regra.todas_as_portas:
            portas[("porta_ampla_aberta", regra.grupo)].add("todas")
            continue
        admin = [p for p in sorted(PORTAS_ADMINISTRATIVAS) if regra.cobre_porta(p)]
        if admin:
            portas[("exposicao_administrativa", regra.grupo)].update(map(str, admin))
        banco = [p for p in sorted(PORTAS_DE_BANCO) if regra.cobre_porta(p)]
        if banco:
            portas[("banco_exposto", regra.grupo)].update(map(str, banco))

    riscos = []
    titulos = {
        "porta_ampla_aberta": "Todas as portas abertas para a internet",
        "exposicao_administrativa": "Porta de administração aberta para a internet",
        "banco_exposto": "Porta de banco aberta para a internet",
    }
    for (id_regra, grupo), ps in sorted(portas.items()):
        g = indice.recursos.get(grupo)
        nome = (g.nome if g else None) or grupo
        membros = indice.membros(grupo) or {grupo}
        riscos.append(
            _risco(
                id_regra,
                Severidade.ALTA,
                titulos[id_regra],
                f"{nome}: {', '.join(sorted(ps))} liberada(s) de qualquer origem. "
                "O Terraform não replica esta regra.",
                membros,
            )
        )
    publicos = [
        r.chave
        for r in indice.recursos.values()
        if r.tipo in DADOS
        and (
            r.atributos.get("publico") is True
            or "0.0.0.0/0" in (r.atributos.get("redes_autorizadas") or [])
        )
    ]
    if publicos:
        riscos.append(
            _risco(
                "banco_exposto",
                Severidade.ALTA,
                "Banco com acesso público",
                "O banco aceita conexão de fora da rede privada.",
                publicos,
            )
        )
    return riscos


def riscos_de_disponibilidade(
    indice: Indice,
    *,
    unidades: Mapping[str, Unidade],
    unidade_de: Mapping[str, str],
    dependencias: Iterable[Dependencia],
    ferramentas: Iterable[str],
    grupos: Mapping[str, tuple[str, ...]],
    criticidade: Mapping[str, Criticidade],
) -> list[Risco]:
    fora = set(ferramentas)
    dependentes: dict[str, set[str]] = defaultdict(set)
    for d in dependencias:
        if d.natureza is Natureza.DADOS and d.de not in fora:
            dependentes[d.para].add(d.de)
    sem_ha = [
        r
        for r in indice.recursos.values()
        if r.tipo in DADOS and r.atributos.get("alta_disponibilidade") is False
    ]
    spof = []
    for r in sem_ha:
        v = unidade_de.get(r.chave)
        if v is None:
            continue
        usam = dependentes[v] | ({v} if unidades[v].aplicacao_dentro else set())
        if len(usam) >= 2:  # noqa: PLR2004 - "ponto único" é servir a duas ou mais
            spof.append(r.chave)
    riscos = []
    if spof:
        riscos.append(
            _risco(
                "ponto_unico_de_falha",
                Severidade.MEDIA,
                "Banco sem alta disponibilidade usado por várias unidades",
                "Uma falha nele para todas as unidades que dependem dele. Avalie alta "
                "disponibilidade no destino.",
                spof,
            )
        )
    criticos_sem_ha = [
        r.chave
        for r in sem_ha
        if (u := unidade_de.get(r.chave)) is not None
        and criticidade.get(_grupo_da_unidade(u, grupos), Criticidade(None, None, "")).efetiva
        is Nivel.ALTA
    ]
    if criticos_sem_ha:
        riscos.append(
            _risco(
                "critico_sem_ha",
                Severidade.MEDIA,
                "Banco sem alta disponibilidade num grupo crítico",
                "O grupo é crítico (declarado ou herdado) e o banco dele não tem alta "
                "disponibilidade na origem.",
                criticos_sem_ha,
            )
        )
    return riscos


def _grupo_da_unidade(u: str, grupos: Mapping[str, tuple[str, ...]]) -> str:
    return next((g for g, membros in grupos.items() if u in membros), u)


def riscos_entre_ambientes(
    unidades: Mapping[str, Unidade],
    dependencias: Iterable[Dependencia],
    ferramentas: Iterable[str],
) -> list[Risco]:
    """Produção que depende de dev (ou o contrário): a regra permite, e migrar
    um sem o outro quebra o ambiente que ninguém esperava que dependesse."""
    fora = set(ferramentas)
    pares = sorted(
        {
            (unidades[d.de].nome, unidades[d.para].nome, d.de)
            for d in dependencias
            if d.de not in fora
            and d.para not in fora
            and unidades[d.de].ambiente
            and unidades[d.para].ambiente
            and unidades[d.de].ambiente != unidades[d.para].ambiente
        }
    )
    if not pares:
        return []
    exemplos = "; ".join(f"{a} → {b}" for a, b, _ in pares[:5])
    return [
        _risco(
            "dependencia_entre_ambientes",
            Severidade.MEDIA,
            "Dependência entre ambientes diferentes",
            f"{len(pares)} dependência(s) cruzam ambientes (ex.: {exemplos}). Confirme se é "
            "intencional antes de migrar um ambiente sem o outro.",
            [u for _, _, u in pares],
        )
    ]


def riscos_de_acoplamento(
    compartilhados: Iterable[BancoCompartilhado],
    grandes: Iterable[str],
    grupos: Mapping[str, tuple[str, ...]],
    nomes_de_unidade: Mapping[str, str],
) -> list[Risco]:
    riscos = []
    for c in compartilhados:
        consumidores = ", ".join(sorted(nomes_de_unidade[u] for u in c.consumidores))
        riscos.append(
            _risco(
                "banco_compartilhado",
                Severidade.MEDIA,
                "Banco compartilhado entre aplicações",
                f"{nomes_de_unidade[c.unidade]} é acessado também por {consumidores}: "
                "migram juntos, ou exigem janela conjunta ou replicação.",
                c.bancos,
            )
        )
    for g in grandes:
        riscos.append(
            _risco(
                "grupo_acoplado_grande",
                Severidade.MEDIA,
                "Grupo de movimentação grande",
                f"{len(grupos[g])} unidades presas pelo acoplamento forte: uma migração só, "
                "ou quebre o acoplamento antes.",
                grupos[g],
            )
        )
    return riscos


def riscos_informativos(
    indice: Indice,
    achados: Iterable[Achado],
    ciclos: Iterable[tuple[str, ...]],
) -> list[Risco]:
    riscos = []
    por_regra: dict[str, set[str]] = defaultdict(set)
    for a in achados:
        por_regra[a.regra].add(a.sujeito)
    for regra, sujeitos in sorted(por_regra.items()):
        riscos.append(
            _risco(
                regra,
                Severidade.INFORMATIVA,
                _TITULOS_DOS_ACHADOS.get(regra, regra),
                _DETALHE_DOS_ACHADOS.get(regra, ""),
                sujeitos,
            )
        )
    lista_ciclos = list(ciclos)
    if lista_ciclos:
        riscos.append(
            _risco(
                "ciclo",
                Severidade.INFORMATIVA,
                "Dependências em ciclo",
                "Grupos que dependem uns dos outros migram na mesma onda.",
                [g for c in lista_ciclos for g in c],
            )
        )
    publicas = [
        r.chave
        for r in indice.recursos.values()
        if r.tipo is TipoDeRecurso.INSTANCIA and r.atributos.get("ip_publico")
    ]
    if publicas:
        riscos.append(
            _risco(
                "recurso_com_ip_publico",
                Severidade.INFORMATIVA,
                "Máquinas com IP público próprio",
                "Expostas sem balanceador na frente. O Terraform só cria IP público se a "
                "requisição pedir acesso público.",
                publicas,
            )
        )
    entradas = [r for r in indice.inventario.regras if r.direcao is Direcao.ENTRADA]
    if not entradas:
        riscos.append(
            _risco(
                "sem_regras",
                Severidade.INFORMATIVA,
                "Inventário sem regras de rede",
                "Nenhuma dependência pôde ser inferida. Declare as dependências conhecidas.",
                [],
            )
        )
    return riscos


def ociosos(indice: Indice) -> list[Ocioso]:
    """Pago e sem servir a ninguém (RI12). Oportunidade, não decisão."""
    resultado = []
    for r in sorted(indice.recursos.values(), key=lambda x: x.chave):
        if r.tipo is TipoDeRecurso.DISCO and r.estado is EstadoDoRecurso.DISPONIVEL:
            resultado.append(Ocioso(r.chave, r.tipo.value, "disco_sem_anexo", _gb(r.atributos)))
        elif r.tipo is TipoDeRecurso.INSTANCIA and r.estado is EstadoDoRecurso.PARADO:
            discos = [
                d
                for d in indice.de(r.chave, TipoDeRelacao.ANEXADO_A)
                if indice.tipo(d) is TipoDeRecurso.DISCO
            ]
            gb = sum(_gb(indice.recursos[d].atributos) or 0 for d in discos)
            resultado.append(Ocioso(r.chave, r.tipo.value, "instancia_parada", gb or None))
        elif r.tipo is TipoDeRecurso.IP_PUBLICO and r.estado is EstadoDoRecurso.DISPONIVEL:
            resultado.append(Ocioso(r.chave, r.tipo.value, "ip_sem_associacao"))
        elif r.tipo is TipoDeRecurso.SNAPSHOT and not indice.para(
            r.chave, TipoDeRelacao.SNAPSHOT_DE
        ):
            resultado.append(Ocioso(r.chave, r.tipo.value, "snapshot_sem_disco", _gb(r.atributos)))
    return resultado


def _gb(atributos: Mapping[str, object]) -> int | None:
    valor = atributos.get("tamanho_gb")
    return int(valor) if isinstance(valor, int | float) else None
