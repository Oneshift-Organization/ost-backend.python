"""O que os quatro normalizadores fazem igual (RI1 e RI3 do PLANO_INVENTARIO_API).

Um normalizador lê o formato do provedor e chama o ``Construtor``; o construtor
cuida do resto:

- **Deduplicação.** O mesmo recurso em dois arquivos fica com a captura mais
  recente; em empate, com o arquivo que veio depois (ordem do envio).
- **Referência resolvida no fim.** A VM cita a subrede pelo id, e a subrede pode
  estar noutro arquivo. Toda referência espera até ``finalizar``; a que não
  resolve é descartada e contada — nunca vira recurso fantasma.
- **Relação validada pelo par de tipos.** ``anexado_a`` de subrede para VM não
  existe na RI3: é descartada e contada, em vez de contaminar a topologia.
- **Ordem estável.** Recursos, relações e regras saem ordenados.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.domain.entities.topologia import (
    Inventario,
    Recurso,
    RegraDeRede,
    Relacao,
    Seletor,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)

_T = TipoDeRecurso
_R = TipoDeRelacao

_ANTES_DE_TUDO = datetime.min.replace(tzinfo=UTC)

_POSICIONAVEIS = (
    _T.INSTANCIA,
    _T.INTERFACE_REDE,
    _T.BANCO,
    _T.CACHE,
    _T.BALANCEADOR,
    _T.GATEWAY,
    _T.CLUSTER_KUBERNETES,
)
_PROTEGIVEIS = (
    _T.INSTANCIA,
    _T.INTERFACE_REDE,
    _T.BANCO,
    _T.CACHE,
    _T.BALANCEADOR,
    _T.SUBREDE,
    _T.CLUSTER_KUBERNETES,
)

# RI3: o que cada relação pode ligar. Fora daqui, a relação é descartada.
PARES_VALIDOS: Mapping[TipoDeRelacao, frozenset[tuple[TipoDeRecurso, TipoDeRecurso]]] = {
    _R.CONTIDO_EM: frozenset(
        {(_T.SUBREDE, _T.REDE)}
        | {(x, _T.SUBREDE) for x in _POSICIONAVEIS}
        | {(x, _T.REDE) for x in (*_POSICIONAVEIS, _T.GRUPO_SEGURANCA)}
    ),
    _R.PROTEGIDO_POR: frozenset((x, _T.GRUPO_SEGURANCA) for x in _PROTEGIVEIS),
    _R.ANEXADO_A: frozenset(
        {
            (_T.DISCO, _T.INSTANCIA),
            (_T.INTERFACE_REDE, _T.INSTANCIA),
            (_T.IP_PUBLICO, _T.INSTANCIA),
            (_T.IP_PUBLICO, _T.INTERFACE_REDE),
            (_T.IP_PUBLICO, _T.BALANCEADOR),
            (_T.IP_PUBLICO, _T.GATEWAY),
        }
    ),
    _R.MEMBRO_DE: frozenset(
        {
            (_T.BANCO, _T.OUTRO),
            (_T.CACHE, _T.OUTRO),
            (_T.INSTANCIA, _T.CLUSTER_KUBERNETES),
            (_T.INSTANCIA, _T.OUTRO),
        }
    ),
    _R.ENCAMINHA_PARA: frozenset(
        {(_T.BALANCEADOR, _T.INSTANCIA), (_T.BALANCEADOR, _T.INTERFACE_REDE)}
    ),
    _R.SNAPSHOT_DE: frozenset({(_T.SNAPSHOT, _T.DISCO)}),
}


@dataclass(frozen=True, slots=True)
class Referencia:
    """Um alvo que só dá para resolver com todos os arquivos lidos.

    O significado de ``ident`` é do normalizador (id nativo, nome, self-link);
    quem o traduz para a chave canônica é o resolvedor que ele passa em
    ``finalizar``. ``tipo_nativo`` também serve para contar o que não resolveu.
    """

    tipo_nativo: str
    ident: str
    conta: str | None = None
    regiao: str | None = None
    por_nome: bool = False


Alvo = str | Referencia
Resolvedor = Callable[[Referencia], str | None]


@dataclass(frozen=True, slots=True)
class _RegraPendente:
    regra: RegraDeRede
    origem: Referencia | None
    destino: Referencia | None


class Construtor:
    def __init__(self, provedor: str) -> None:
        self.provedor = provedor
        self._recursos: dict[str, tuple[Recurso, tuple[datetime, int]]] = {}
        self._relacoes: set[tuple[Alvo, Alvo, TipoDeRelacao]] = set()
        self._anotacoes: list[tuple[Alvo, dict[str, Any]]] = []
        self._regras: list[_RegraPendente] = []
        self.contagens: Counter[str] = Counter()
        self._arquivo = 0

    # ── entrada ─────────────────────────────────────────────────────────

    def novo_arquivo(self) -> None:
        self._arquivo += 1

    def contar(self, motivo: str, n: int = 1) -> None:
        self.contagens[motivo] += n

    def adicionar(self, recurso: Recurso) -> None:
        ordem = (recurso.capturado_em or _ANTES_DE_TUDO, self._arquivo)
        atual = self._recursos.get(recurso.chave)
        if atual is None:
            self._recursos[recurso.chave] = (recurso, ordem)
            return
        self.contar("duplicado")
        existente, ordem_existente = atual
        apelidos = existente.apelidos | recurso.apelidos
        if ordem >= ordem_existente:
            self._recursos[recurso.chave] = (replace(recurso, apelidos=apelidos), ordem)
        else:
            self._recursos[recurso.chave] = (
                replace(existente, apelidos=apelidos),
                ordem_existente,
            )

    def relacionar(self, de: Alvo, para: Alvo, tipo: TipoDeRelacao) -> None:
        self._relacoes.add((de, para, tipo))

    def anotar(self, alvo: Alvo, atributos: dict[str, Any]) -> None:
        """Atributo que um recurso diz sobre outro (a VM diz qual disco é o de
        boot). Aplicado no fim, quando o outro já foi lido."""
        self._anotacoes.append((alvo, atributos))

    def regra(
        self,
        regra: RegraDeRede,
        *,
        origem: Referencia | None = None,
        destino: Referencia | None = None,
    ) -> None:
        """Regra cujo seletor ainda cita um grupo por referência."""
        self._regras.append(_RegraPendente(regra, origem, destino))

    def existe(self, chave: str) -> bool:
        return chave in self._recursos

    def recursos(self) -> list[Recurso]:
        return [r for r, _ in self._recursos.values()]

    # ── saída ───────────────────────────────────────────────────────────

    def finalizar(self, resolvedor: Resolvedor | None = None) -> Inventario:
        recursos = {chave: r for chave, (r, _) in self._recursos.items()}

        def resolver(alvo: Alvo) -> str | None:
            if isinstance(alvo, str):
                return alvo if alvo in recursos else None
            chave = resolvedor(alvo) if resolvedor else None
            return chave if chave in recursos else None

        for alvo, atributos in self._anotacoes:
            chave = resolver(alvo)
            if chave is None:
                continue
            r = recursos[chave]
            recursos[chave] = replace(r, atributos={**r.atributos, **atributos})

        relacoes: set[Relacao] = set()
        for de, para, tipo in self._relacoes:
            d, p = resolver(de), resolver(para)
            if d is None or p is None:
                faltante = para if p is None else de
                self.contar(f"referencia_externa:{_rotulo(faltante)}")
                continue
            if d == p:
                continue
            par = (recursos[d].tipo, recursos[p].tipo)
            if par not in PARES_VALIDOS[tipo]:
                self.contar(f"relacao_invalida:{tipo.value}:{par[0].value}->{par[1].value}")
                continue
            relacoes.add(Relacao(de=d, para=p, tipo=tipo))

        regras: list[RegraDeRede] = []
        for pendente in self._regras:
            regra = pendente.regra
            if regra.grupo not in recursos:
                self.contar("regra_sem_grupo")
                continue
            if pendente.origem is not None:
                regra = replace(regra, origem=self._seletor_de_grupo(pendente.origem, resolver))
            if pendente.destino is not None:
                regra = replace(regra, destino=self._seletor_de_grupo(pendente.destino, resolver))
            regras.append(regra)

        capturas = [r.capturado_em for r in recursos.values() if r.capturado_em]
        return Inventario(
            provedor=self.provedor,
            recursos=tuple(recursos[c] for c in sorted(recursos)),
            relacoes=tuple(sorted(relacoes, key=lambda x: (x.de, x.para, x.tipo.value))),
            regras=tuple(sorted(set(regras), key=ordem_da_regra)),
            capturado_em=max(capturas) if capturas else None,
            contagens=dict(sorted(self.contagens.items())),
        )

    def _seletor_de_grupo(self, ref: Referencia, resolver: Callable[[Alvo], str | None]) -> Seletor:
        chave = resolver(ref)
        if chave is not None and self._recursos[chave][0].tipo is TipoDeRecurso.GRUPO_SEGURANCA:
            return Seletor(TipoDeSeletor.GRUPO, chave)
        # Grupo de outra conta ou fora do export: a origem existe, mas não
        # sabemos quem é membro. Não gera dependência (RI5) e é dito.
        self.contar("grupo_externo")
        return Seletor(TipoDeSeletor.SERVICO, f"grupo_externo:{ref.ident}")


def _rotulo(alvo: Alvo) -> str:
    return alvo.tipo_nativo if isinstance(alvo, Referencia) else "recurso"


def ordem_da_regra(r: RegraDeRede) -> tuple[Any, ...]:
    return (
        r.grupo,
        r.direcao.value,
        r.prioridade if r.prioridade is not None else -1,
        r.acao.value,
        r.protocolo,
        r.porta_inicio if r.porta_inicio is not None else -1,
        r.porta_fim if r.porta_fim is not None else -1,
        r.origem.tipo.value,
        r.origem.valor or "",
        r.destino.tipo.value if r.destino else "",
        (r.destino.valor or "") if r.destino else "",
        r.descricao or "",
    )
