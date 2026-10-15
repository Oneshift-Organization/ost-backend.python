"""Correlação entre billing e inventário (RI13 do PLANO_INVENTARIO_API).

Responde três perguntas, cada uma com o seu número:

- **Quanto do custo tem inventário?** O billing cita o recurso pelo
  ``resource_id``; o inventário tem os apelidos de cada recurso (ARN, id nativo,
  a forma com ID numérico do GCP). Casa por igualdade, sem diferenciar caixa.
- **O que falta, e por quê?** O custo sem inventário é agrupado por conta e
  região ("a conta X não foi exportada") e por serviço ("o Config não registra
  snapshot de EBS"). Um número sem a causa não diz o que fazer.
- **O inventário é do mesmo momento do billing?** Captura muito antes ou muito
  depois do período faz recurso criado ou removido aparecer como lacuna.

Linha que chega já ligada pelo tipo (``atribuicao_por_tipo``) entra como custo do
recurso, separada do medido: ``custo_estimado``. A que tinha ID conta na fração
coberta (o nó trocado pelo autoscaling é coberto pelo tipo dele); a que não tinha
fica fora da fração, que é sobre o custo com ID.

Módulo puro, sem I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.entities.topologia import (
    ANEXO,
    UNIDADE,
    Inventario,
    TipoDeRecurso,
    TipoDeRelacao,
)
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import Risco, Severidade
from app.domain.services.topologia.riscos import ociosos

_ZERO = Decimal("0")
COBERTURA_MINIMA = Decimal("0.8")
DIAS_ANTES_DO_PERIODO = 30
DIAS_DEPOIS_DO_PERIODO = 45
MAX_LACUNAS = 50
_MAX_EXEMPLOS = 5


@dataclass(frozen=True, slots=True)
class CustoDeRecurso:
    """Custo de base (sem imposto, crédito, reembolso nem ajuste) de um recurso."""

    resource_id: str
    conta: str | None
    regiao: str | None
    servico: str
    custo: Decimal
    uso: str | None = None  # tipo de uso (`BoxUsage:t4g.xlarge`)
    quantidade: Decimal | None = None
    # Ligada ao recurso pelo tipo, não pelo ID (atribuicao_por_tipo)
    estimado: bool = False
    # O ID que a fatura trazia, quando a linha estimada tinha um
    origem: str | None = None
    base: str | None = None  # como a estimativa foi feita
    # Dividido entre recursos de aplicações diferentes: aí a divisão muda o custo de cada
    # aplicação, e a estimativa é fraca (o CUR diria quanto cada um custou)
    dividido: bool = False


@dataclass(frozen=True, slots=True)
class LinhaSemRecurso:
    """Custo de base que a fatura não liga a recurso nenhum, por tipo de uso."""

    conta: str | None
    regiao: str | None
    servico: str
    uso: str | None
    custo: Decimal
    quantidade: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CustosDoBilling:
    periodo: str | None  # "YYYY-MM"
    moeda: str
    custo_da_base: Decimal  # com e sem resource_id
    por_recurso: tuple[CustoDeRecurso, ...]
    sem_recurso: tuple[LinhaSemRecurso, ...] = ()
    provedor: str | None = None


@dataclass(frozen=True, slots=True)
class Lacuna:
    conta: str | None
    regiao: str | None
    servico: str | None
    recursos: int
    custo: Decimal
    exemplos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Alinhamento:
    capturado_em: datetime | None
    periodo_inicio: datetime | None
    periodo_fim: datetime | None
    desalinhado: bool
    dias_fora: int
    # A captura foi estimada pela data do envio (o Resource Graph não informa).
    estimado: bool = False


@dataclass(frozen=True, slots=True)
class CustoOcioso:
    recurso: str
    motivo: str
    custo: Decimal


@dataclass(frozen=True, slots=True)
class Correlacao:
    moeda: str
    custo_da_base: Decimal
    custo_com_recurso: Decimal
    custo_encontrado: Decimal
    fracao_encontrada: Decimal | None
    recursos_do_billing: int
    recursos_encontrados: int
    ambiguos: int
    custo_por_chave: Mapping[str, Decimal]
    lacunas_por_escopo: tuple[Lacuna, ...]
    lacunas_por_servico: tuple[Lacuna, ...]
    sem_cobranca: Mapping[str, int]
    ociosos: tuple[CustoOcioso, ...]
    alinhamento: Alinhamento
    riscos: tuple[Risco, ...]
    # Ligado pelo tipo (estimado): todo, e a parte que vinha de linha com ID
    custo_estimado: Decimal = _ZERO
    custo_estimado_com_id: Decimal = _ZERO
    custo_estimado_dividido: Decimal = _ZERO


def _prioridade(tipo: TipoDeRecurso) -> int:
    if tipo in UNIDADE:
        return 0
    if tipo in ANEXO:
        return 1
    return 2


def _recurso_pai_arm(rid: str) -> str | None:
    """Id da Azure com sub-recurso (`.../virtualmachines/vm/extensions/x`) → o
    recurso de primeiro nível. Só para ids do ARM."""
    partes = rid.split("/")
    if "providers" not in partes:
        return None
    i = partes.index("providers")
    return "/".join(partes[: i + 4]) if len(partes) > i + 4 else None


class IndiceDeApelidos:
    """``resource_id`` do billing → chave do recurso no inventário.

    Igualdade sem diferenciar caixa, contra todos os apelidos do recurso. Id de
    sub-recurso da Azure tenta o pai. Quando mais de um recurso tem o mesmo
    apelido, vence o de papel unidade, depois o de anexo, depois a menor chave:
    determinístico, e o custo vai para quem de fato o gera.
    """

    def __init__(self, inventario: Inventario) -> None:
        tipos = {r.chave: r.tipo for r in inventario.recursos}
        self._por_apelido: dict[str, list[str]] = defaultdict(list)
        for r in inventario.recursos:
            for apelido in r.apelidos | {r.chave.lower()}:
                self._por_apelido[apelido].append(r.chave)
        for lista in self._por_apelido.values():
            lista.sort(key=lambda c: (_prioridade(tipos[c]), c))

    def candidatos(self, resource_id: str) -> list[str]:
        rid = resource_id.strip().lower()
        achados = self._por_apelido.get(rid)
        if not achados and (pai := _recurso_pai_arm(rid)):
            achados = self._por_apelido.get(pai)
        return achados or []

    def casar(self, resource_id: str) -> str | None:
        achados = self.candidatos(resource_id)
        return achados[0] if achados else None


def correlacionar(
    inventario: Inventario, custos: CustosDoBilling, *, captura_estimada: bool = False
) -> Correlacao:
    apelidos = IndiceDeApelidos(inventario)
    por_id: dict[str, list[CustoDeRecurso]] = defaultdict(list)
    custo_por_chave: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for linha in custos.por_recurso:
        if linha.estimado and not linha.origem:
            # Linha sem ID que o tipo ligou: custo do recurso, mas não "recurso da fatura"
            custo_por_chave[linha.resource_id] += linha.custo
            continue
        rid = linha.resource_id.strip().lower()
        if rid:
            por_id[rid].append(linha)

    faltando: list[CustoDeRecurso] = []
    encontrados = ambiguos = 0
    for rid, linhas in sorted(por_id.items()):
        candidatos = apelidos.candidatos(rid)
        if not candidatos:
            faltando.extend(linhas)
            continue
        encontrados += 1
        ambiguos += len(set(candidatos)) > 1
        custo_por_chave[candidatos[0]] += sum((x.custo for x in linhas), _ZERO)

    estimado = sum((x.custo for x in custos.por_recurso if x.estimado), _ZERO)
    estimado_com_id = sum((x.custo for x in custos.por_recurso if x.estimado and x.origem), _ZERO)
    # A fração é sobre o custo que a fatura ligou a um ID; o estimado sem ID fica de fora
    custo_com_recurso = (
        sum((x.custo for x in custos.por_recurso if not x.estimado), _ZERO) + estimado_com_id
    )
    encontrado = sum(custo_por_chave.values(), _ZERO) - (estimado - estimado_com_id)
    fracao = (encontrado / custo_com_recurso) if custo_com_recurso > 0 else None

    sem_cobranca: dict[str, int] = defaultdict(int)
    for r in inventario.recursos:
        if (r.tipo in UNIDADE or r.tipo is TipoDeRecurso.DISCO) and r.chave not in custo_por_chave:
            sem_cobranca[r.tipo.value] += 1

    alinhamento = replace(
        _alinhamento(inventario.capturado_em, custos.periodo), estimado=captura_estimada
    )
    riscos = []
    if fracao is not None and fracao < COBERTURA_MINIMA:
        riscos.append(
            Risco(
                regra="cobertura_baixa",
                severidade=Severidade.MEDIA,
                titulo="Parte do custo não tem inventário",
                detalhe=f"{fracao:.0%} do custo com recurso identificado está no inventário. "
                "As maiores lacunas por conta e região dizem o que exportar.",
                total=len(por_id) - encontrados,
            )
        )
    if alinhamento.desalinhado:
        riscos.append(
            Risco(
                regra="inventario_desalinhado",
                severidade=Severidade.MEDIA,
                titulo="Inventário e billing de momentos diferentes",
                detalhe=f"A captura fica {alinhamento.dias_fora} dias fora do período do billing: "
                "recursos criados ou removidos no meio aparecem como lacuna."
                + (
                    " A data de captura é estimada pela data do envio (o formato não a "
                    "informa): se o export é de outra data, o desalinhamento pode não existir."
                    if captura_estimada
                    else ""
                ),
            )
        )
    return Correlacao(
        moeda=custos.moeda,
        custo_da_base=custos.custo_da_base,
        custo_com_recurso=custo_com_recurso,
        custo_encontrado=encontrado,
        fracao_encontrada=fracao,
        recursos_do_billing=len(por_id),
        recursos_encontrados=encontrados,
        ambiguos=ambiguos,
        custo_por_chave=dict(sorted(custo_por_chave.items())),
        lacunas_por_escopo=_lacunas(faltando, lambda x: (x.conta, x.regiao, None)),
        lacunas_por_servico=_lacunas(faltando, lambda x: (None, None, x.servico)),
        sem_cobranca=dict(sorted(sem_cobranca.items())),
        ociosos=_custo_dos_ociosos(inventario, custo_por_chave),
        alinhamento=alinhamento,
        riscos=tuple(riscos),
        custo_estimado=estimado,
        custo_estimado_com_id=estimado_com_id,
        custo_estimado_dividido=sum(
            (x.custo for x in custos.por_recurso if x.estimado and x.dividido), _ZERO
        ),
    )


_Agrupador = Callable[[CustoDeRecurso], tuple[str | None, str | None, str | None]]


def _lacunas(linhas: Iterable[CustoDeRecurso], chave: _Agrupador) -> tuple[Lacuna, ...]:
    grupos: dict[tuple[str | None, str | None, str | None], list[CustoDeRecurso]] = defaultdict(
        list
    )
    for linha in linhas:
        grupos[chave(linha)].append(linha)
    lacunas = [
        Lacuna(
            conta=c,
            regiao=r,
            servico=s,
            recursos=len({x.resource_id for x in ls}),
            custo=sum((x.custo for x in ls), _ZERO),
            exemplos=tuple(sorted({x.resource_id for x in ls})[:_MAX_EXEMPLOS]),
        )
        for (c, r, s), ls in grupos.items()
    ]
    lacunas.sort(key=lambda x: (-x.custo, x.conta or "", x.regiao or "", x.servico or ""))
    return tuple(lacunas[:MAX_LACUNAS])


def _custo_dos_ociosos(
    inventario: Inventario, custo_por_chave: Mapping[str, Decimal]
) -> tuple[CustoOcioso, ...]:
    """Custo do que está pago e parado (RI12): a instância parada leva o custo
    dos discos dela, que é o que continua sendo cobrado."""
    indice = Indice(inventario)
    resultado = []
    for o in ociosos(indice):
        chaves = [o.recurso]
        if o.motivo == "instancia_parada":
            chaves += [
                d
                for d in indice.de(o.recurso, TipoDeRelacao.ANEXADO_A)
                if indice.tipo(d) is TipoDeRecurso.DISCO
            ]
        custo = sum((custo_por_chave.get(c, _ZERO) for c in chaves), _ZERO)
        resultado.append(CustoOcioso(o.recurso, o.motivo, custo))
    return tuple(resultado)


def _alinhamento(capturado_em: datetime | None, periodo: str | None) -> Alinhamento:
    if not periodo or len(periodo) != len("2026-03"):
        return Alinhamento(capturado_em, None, None, False, 0)
    try:
        ano, mes = int(periodo[:4]), int(periodo[5:7])
        inicio = datetime(ano, mes, 1, tzinfo=UTC)
        fim = datetime(ano + (mes == 12), mes % 12 + 1, 1, tzinfo=UTC)  # noqa: PLR2004
    except ValueError:
        return Alinhamento(capturado_em, None, None, False, 0)
    if capturado_em is None:
        return Alinhamento(None, inicio, fim, False, 0)
    captura = capturado_em if capturado_em.tzinfo else capturado_em.replace(tzinfo=UTC)
    antes = inicio - timedelta(days=DIAS_ANTES_DO_PERIODO)
    depois = fim + timedelta(days=DIAS_DEPOIS_DO_PERIODO)
    if captura < antes:
        return Alinhamento(captura, inicio, fim, True, (inicio - captura).days)
    if captura > depois:
        return Alinhamento(captura, inicio, fim, True, (captura - fim).days)
    return Alinhamento(captura, inicio, fim, False, 0)
