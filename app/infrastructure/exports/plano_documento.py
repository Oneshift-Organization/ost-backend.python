"""O plano de migração organizado para ser lido fora da tela (PLANO_ESCOPO_DE_MIGRACAO, E7).

O PDF (para circular entre pessoas) e a planilha (para quem executa) saem desta
mesma leitura, na ordem em que a tela mostra:

- **blocos**: pré-migração (grupos que o escopo chama e foram incluídos), a
  migração principal (o que foi escolhido e o que vai junto obrigatoriamente) e
  a pós-migração (grupos que chamam o escopo e foram incluídos);
- **ordem**: as etapas da conta inteira, com o que está no plano e o que ficou de
  fora em cada uma; quando o plano não começa na etapa 1, diz por quê;
- **pendências**: o que conversa com o plano e ficou de fora, com o que fazer;
- **serviços**, **ligações** e a lista de **recursos** a migrar.

Só organiza o que o backend já calculou; o único número novo é a soma do custo
de cada bloco.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.schemas.migration_scope import (
    GrupoVizinhoOut,
    MigrationPlanResponse,
    TotalNoDestinoOut,
)
from app.use_cases.analysis.escopo_de_migracao import PlanoCotado

PRE = "pre"
PRINCIPAL = "principal"
POS = "pos"

NOME_DO_BLOCO = {PRE: "Pré-migração", PRINCIPAL: "Migração principal", POS: "Pós-migração"}
DESCRICAO_DO_BLOCO = {
    PRE: "Migram antes da migração principal: o que foi escolhido chama estes grupos.",
    PRINCIPAL: "O que foi escolhido e o que não funciona sem ele no destino.",
    POS: "Migram depois da migração principal: estes grupos chamam o que foi escolhido.",
}
_BLOCO_DO_LADO = {"antes": PRE, "depois": POS, "sem_ligacao": PRINCIPAL}

NATUREZA = {
    "dados": "acessa dados em",
    "servico": "chama",
    "encaminhamento": "encaminha tráfego para",
    "declarada": "depende de",
    "administrativo": "administra",
}
METODO = {
    "pool": "pool da família e região",
    "pool_da_familia": "média da família",
    "media_dos_pools": "média dos pools",
    "categoria": "razão de preço da categoria",
}
_TIPO = {
    "instancia": "Instância",
    "banco": "Banco de dados",
    "cache": "Cache",
    "balanceador": "Balanceador",
    "bucket": "Bucket",
    "cluster_kubernetes": "Cluster Kubernetes",
    "disco": "Disco",
    "interface_rede": "Interface de rede",
    "ip_publico": "IP público",
    "snapshot": "Snapshot",
    "rede": "Rede",
    "subrede": "Sub-rede",
    "grupo_seguranca": "Grupo de segurança",
    "gateway": "Gateway",
    "outro": "Outro",
}

_PLURAL = {
    "Instância": "instâncias",
    "Banco de dados": "bancos de dados",
    "Cache": "caches",
    "Balanceador": "balanceadores",
    "Bucket": "buckets",
    "Cluster Kubernetes": "clusters Kubernetes",
    "Disco": "discos",
    "Interface de rede": "interfaces de rede",
    "IP público": "IPs públicos",
    "Snapshot": "snapshots",
    "Rede": "redes",
    "Sub-rede": "sub-redes",
    "Grupo de segurança": "grupos de segurança",
    "Gateway": "gateways",
    "Outro": "outros",
}


def contagem(tipo: str, n: int) -> str:
    """'1 instância', '14 instâncias'."""
    if n != 1:
        return f"{n} {_PLURAL.get(tipo, tipo.lower())}"
    return f"1 {tipo if tipo[:2].isupper() else tipo[0].lower() + tipo[1:]}"


def etapa(ordem: int | None) -> str:
    return f"{ordem}ª etapa" if ordem is not None else "sem ordem"


@dataclass(frozen=True, slots=True)
class Item:
    nome: str
    papel: str  # "escolhido", "vai junto", "pré-requisito", "depende do escopo", "incluído"
    detalhe: str
    custo: float
    ordem: int | None


@dataclass(frozen=True, slots=True)
class Passo:
    """Um passo do plano: um bloco numa etapa. Numerado do começo ao fim do plano."""

    numero: int
    ordem: int | None
    itens: tuple[Item, ...]


@dataclass(frozen=True, slots=True)
class Bloco:
    chave: str
    titulo: str
    descricao: str
    custo: float
    passos: tuple[Passo, ...]

    @property
    def itens(self) -> tuple[Item, ...]:
        return tuple(i for p in self.passos for i in p.itens)


@dataclass(frozen=True, slots=True)
class GrupoNaEtapa:
    nome: str
    bloco: str | None  # None: fora do plano
    lado: str | None = None  # fora do plano: "antes" ou "depois"


@dataclass(frozen=True, slots=True)
class Etapa:
    ordem: int | None
    grupos: tuple[GrupoNaEtapa, ...]

    @property
    def no_plano(self) -> bool:
        return any(g.bloco for g in self.grupos)


@dataclass(frozen=True, slots=True)
class Pendencia:
    nome: str
    lado: str  # "antes" | "depois"
    ordem: int | None
    ligado_a: tuple[str, ...]
    ligacao: str
    custo: float
    nos_dois_sentidos: bool
    acao: str


@dataclass(frozen=True, slots=True)
class RecursoNoPlano:
    bloco: str
    ordem: int | None
    grupo: str
    unidade: str
    aplicacao: str
    ambiente: str | None
    nome: str
    tipo: str
    tipo_nativo: str
    regiao: str | None
    custo: float
    chave: str


@dataclass(frozen=True, slots=True)
class Documento:
    titulo: str
    escolha: str
    job_id: str
    gerado_em: datetime
    periodo: str | None
    moeda: str
    origem: str
    recomendado: str | None
    plano: MigrationPlanResponse
    blocos: tuple[Bloco, ...]
    etapas: tuple[Etapa, ...]
    aviso_da_ordem: str | None
    pendencias: tuple[Pendencia, ...]
    recursos: tuple[RecursoNoPlano, ...]
    premissas: tuple[str, ...]

    @property
    def destino_recomendado(self) -> TotalNoDestinoOut | None:
        return next((d for d in self.plano.destino if d.provedor == self.recomendado), None)


def _descrever_escolha(p: MigrationPlanResponse) -> tuple[str, str]:
    e = p.escolha
    partes = []
    if e.aplicacoes:
        partes.append(
            ("aplicação " if len(e.aplicacoes) == 1 else "aplicações ") + ", ".join(e.aplicacoes)
        )
    if e.servicos:
        partes.append(("serviço " if len(e.servicos) == 1 else "serviços ") + ", ".join(e.servicos))
    ambiente = f"ambiente {e.ambiente}" if e.ambiente else "todos os ambientes"
    titulo = " + ".join([*e.aplicacoes, *e.servicos])
    return titulo, f"{' e '.join(partes)} · {ambiente}"


# O tipo da ligação, sem sentido: quem chama quem já está na situação da pendência
_LIGACAO = {
    "dados": "acesso a dados",
    "servico": "chamada de serviço",
    "encaminhamento": "encaminhamento de tráfego",
    "declarada": "dependência declarada",
    "administrativo": "acesso administrativo",
}


def _ligacao(naturezas: list[str], portas: list[str]) -> str:
    tipos = ", ".join(dict.fromkeys(_LIGACAO.get(n, n) for n in naturezas))
    return tipos + (f" ({', '.join(portas)})" if portas else "")


def _pendencia(v: GrupoVizinhoOut, lado: str) -> Pendencia:
    if lado == "antes":
        acao = (
            "Migrar antes (pré-migração) ou manter o acesso a partir do destino durante a "
            "transição."
        )
    else:
        acao = (
            "Migrar depois (pós-migração) ou manter este grupo chamando o destino entre nuvens "
            "(latência e tráfego de saída)."
        )
    return Pendencia(
        nome=v.nome,
        lado=lado,
        ordem=v.ordem,
        ligado_a=tuple(v.ligado_a),
        ligacao=_ligacao(list(v.naturezas), v.portas),
        custo=v.custo,
        nos_dois_sentidos=v.nos_dois_sentidos,
        acao=acao,
    )


@dataclass(frozen=True, slots=True)
class _Leitura:
    """O plano junto do que a topologia sabe dele: nomes, etapas e o bloco de cada grupo."""

    plano: MigrationPlanResponse
    cotado: PlanoCotado
    nome_do_grupo: Mapping[str, str]
    ordem_do_grupo: Mapping[str, int]
    bloco_do_grupo: Mapping[str, str]

    @classmethod
    def de(cls, plano: MigrationPlanResponse, cotado: PlanoCotado) -> _Leitura:
        analise = cotado.contexto.catalogo.analise
        return cls(
            plano=plano,
            cotado=cotado,
            nome_do_grupo={g.id: g.nome for g in analise.grupos},
            ordem_do_grupo={g: o.numero for o in analise.ondas for g in o.grupos},
            bloco_do_grupo={
                i.grupo: _BLOCO_DO_LADO.get(i.lado, PRINCIPAL) for i in plano.incluidos
            },
        )

    def grupo_de(self, unidade: str) -> str:
        return self.cotado.contexto.catalogo.analise.grupo_de.get(unidade, unidade)


def _itens_principais(x: _Leitura) -> list[Item]:
    """O escolhido (unidades da escolha), o que vai junto por força e o incluído sem ligação."""
    catalogo = x.cotado.contexto.catalogo
    unidades = {u.id: u for u in catalogo.analise.unidades}
    junto = {v.unidade for v in x.plano.vao_junto}

    def custo(u: str) -> float:
        return float(sum(catalogo.custo_por_unidade.get(u, {}).values()))

    escolhidas = sorted(
        (
            u
            for u in x.cotado.plano.unidades
            if u not in junto and x.grupo_de(u) not in x.bloco_do_grupo
        ),
        key=lambda u: (-custo(u), unidades[u].nome),
    )
    return [
        *(
            Item(
                nome=unidades[u].nome,
                papel="escolhido",
                detalhe=f"Grupo: {x.nome_do_grupo.get(x.grupo_de(u), unidades[u].nome)}",
                custo=round(custo(u), 2),
                ordem=x.ordem_do_grupo.get(x.grupo_de(u)),
            )
            for u in escolhidas
        ),
        *(
            Item(
                nome=v.nome,
                papel="vai junto",
                detalhe=v.motivo,
                custo=v.custo,
                ordem=x.ordem_do_grupo.get(v.grupo),
            )
            for v in x.plano.vao_junto
            if not v.incluida_por_voce
        ),
        *(
            Item(
                nome=i.nome,
                papel="incluído",
                detalhe="Incluído à mão; nada mais no plano conversa com ele.",
                custo=i.custo,
                ordem=i.ordem,
            )
            for i in x.plano.incluidos
            if i.lado == "sem_ligacao"
        ),
    ]


def _itens_incluidos(x: _Leitura, chave: str) -> list[Item]:
    pre = chave == PRE
    return [
        Item(
            nome=i.nome,
            papel="pré-requisito" if pre else "depende do escopo",
            detalhe=(
                ("Chamado por " if pre else "Chama ")
                + ", ".join(i.ligado_a)
                + (f" ({', '.join(i.portas)})" if i.portas else "")
                + (f". Vem inteiro: {' + '.join(i.aplicacoes)}" if len(i.aplicacoes) > 1 else "")
            ),
            custo=i.custo,
            ordem=i.ordem,
        )
        for i in x.plano.incluidos
        if i.lado == ("antes" if pre else "depois")
    ]


def _blocos(x: _Leitura) -> tuple[Bloco, ...]:
    """Pré, principal e pós; dentro de cada um, um passo por etapa da conta."""
    blocos = []
    numero = 0
    for chave in (PRE, PRINCIPAL, POS):
        itens = _itens_principais(x) if chave == PRINCIPAL else _itens_incluidos(x, chave)
        if not itens:
            continue
        por_etapa: dict[int | None, list[Item]] = defaultdict(list)
        for item in itens:
            por_etapa[item.ordem].append(item)
        passos = []
        # A 1ª etapa primeiro, sem ordem no fim
        for ordem in sorted(por_etapa, key=lambda o: (o is None, o or 0)):
            numero += 1
            passos.append(Passo(numero=numero, ordem=ordem, itens=tuple(por_etapa[ordem])))
        blocos.append(
            Bloco(
                chave=chave,
                titulo=NOME_DO_BLOCO[chave],
                descricao=DESCRICAO_DO_BLOCO[chave],
                custo=round(sum(i.custo for i in itens), 2),
                passos=tuple(passos),
            )
        )
    return tuple(blocos)


def _etapas(x: _Leitura, pendencias: tuple[Pendencia, ...]) -> tuple[tuple[Etapa, ...], str | None]:
    """Etapas da conta inteira com o que do plano cai em cada uma, e o aviso da ordem."""
    analise = x.cotado.contexto.catalogo.analise
    max_ordem = max((o.numero for o in analise.ondas), default=0)
    por_ordem: dict[int | None, list[GrupoNaEtapa]] = defaultdict(list)
    grupos_no_plano = {x.grupo_de(u) for u in x.cotado.plano.unidades}
    for g in sorted(grupos_no_plano, key=lambda g: x.nome_do_grupo.get(g, g)):
        por_ordem[x.ordem_do_grupo.get(g)].append(
            GrupoNaEtapa(nome=x.nome_do_grupo.get(g, g), bloco=x.bloco_do_grupo.get(g, PRINCIPAL))
        )
    for p in pendencias:
        por_ordem[p.ordem].append(GrupoNaEtapa(nome=p.nome, bloco=None, lado=p.lado))
    etapas = tuple(
        Etapa(ordem=k, grupos=tuple(por_ordem.get(k, ()))) for k in range(1, max_ordem + 1)
    )
    if por_ordem.get(None):
        etapas += (Etapa(ordem=None, grupos=tuple(por_ordem[None])),)

    no_plano = [e.ordem for e in etapas if e.no_plano and e.ordem is not None]
    if not no_plano or min(no_plano) == 1:
        return etapas, None
    primeira = min(no_plano)
    faltam = [p.nome for p in pendencias if p.ordem is not None and p.ordem < primeira]
    if not faltam:
        return etapas, (
            f"O plano começa na {primeira}ª etapa: as etapas anteriores são de outras "
            "aplicações, sem ligação com este escopo."
        )
    return etapas, (
        f"O plano começa na {primeira}ª etapa porque a etapa anterior não está nele: "
        f"{', '.join(faltam)}. O escopo chama esses grupos; inclua na pré-migração ou "
        "mantenha o acesso entre as nuvens durante a transição."
    )


def _recursos(x: _Leitura) -> tuple[RecursoNoPlano, ...]:
    ctx = x.cotado.contexto
    unidades = {u.id: u for u in ctx.catalogo.analise.unidades}
    recursos = []
    for u in x.cotado.plano.unidades:
        unidade = unidades[u]
        g = x.grupo_de(u)
        for chave in unidade.recursos:
            r = ctx.recursos.get(chave)
            if r is None:
                continue
            recursos.append(
                RecursoNoPlano(
                    bloco=x.bloco_do_grupo.get(g, PRINCIPAL),
                    ordem=x.ordem_do_grupo.get(g),
                    grupo=x.nome_do_grupo.get(g, g),
                    unidade=unidade.nome,
                    aplicacao=unidade.aplicacao or unidade.nome,
                    ambiente=unidade.ambiente,
                    nome=r.nome or chave.rsplit("/", 1)[-1].rsplit(":", 1)[-1],
                    tipo=_TIPO.get(r.tipo, r.tipo),
                    tipo_nativo=r.tipo_nativo,
                    regiao=r.regiao,
                    custo=float(round(r.custo, 2)),
                    chave=chave,
                )
            )
    ordem_do_bloco = {PRE: 0, PRINCIPAL: 1, POS: 2}
    return tuple(
        sorted(
            recursos,
            key=lambda r: (
                ordem_do_bloco[r.bloco],
                r.ordem is None,
                r.ordem or 0,
                r.grupo,
                r.unidade,
                -r.custo,
                r.nome,
            ),
        )
    )


def montar_documento(
    plano: MigrationPlanResponse, cotado: PlanoCotado, *, job_id: str, gerado_em: datetime
) -> Documento:
    x = _Leitura.de(plano, cotado)
    pendencias = tuple(
        [_pendencia(v, "antes") for v in plano.antes]
        + [_pendencia(v, "depois") for v in plano.para_tras]
    )
    etapas, aviso = _etapas(x, pendencias)
    titulo, escolha = _descrever_escolha(plano)
    premissas = tuple(
        dict.fromkeys([plano.limite_das_dependencias, *(p.base for p in plano.premissas if p.base)])
    )
    return Documento(
        titulo=titulo,
        escolha=escolha,
        job_id=job_id,
        gerado_em=gerado_em,
        periodo=cotado.contexto.periodo,
        moeda=plano.moeda,
        origem=(plano.provedor_origem or "").upper(),
        recomendado=plano.provedor_recomendado,
        plano=plano,
        blocos=_blocos(x),
        etapas=etapas,
        aviso_da_ordem=aviso,
        pendencias=pendencias,
        recursos=_recursos(x),
        premissas=premissas,
    )
