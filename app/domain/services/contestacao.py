"""Contestação de preço verificável (lote da autoridade v4, V4.1).

Antes, contestação de preço com severidade alta rebaixava a confiança um degrau sem
olhar o preço contestado. Agora ela é conferida contra o próprio snapshot, que é
reproduzível: o payload já diz, por equivalente, de onde veio o preço, quando foi
capturado, em que estado do ciclo de vida está e se é da região pedida.

- `official`, da região pedida e VIVO (ou com até 30 dias, em payload sem estado):
  não rebaixa; `verificado_mantido`, com a evidência;
- `official` velho, de outra região, `reference` ou seed: rebaixa como antes e vira
  pedido de sync;
- referência não achada no snapshot: rebaixa como antes; `nao_verificavel`.

A referência segue o contrato com o agente: `PROVEDOR/sku/região`
(ex.: `GCP/n2-standard-8/us-central1`), e vale também para taxa de categoria
(`OCI/network_egress/sa-saopaulo-1`).

Módulo puro, sem I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

VERIFICADO = "verificado_mantido"
A_RECONFIRMAR = "preco_a_reconfirmar"
NAO_VERIFICAVEL = "nao_verificavel"

# `PROVEDOR/sku/região`: a região pode faltar
_PARTES_DA_REFERENCIA = 3
# Sem estado do ciclo de vida (payload antigo), a idade decide
_IDADE_MAXIMA_DIAS = 30
_EVIDENCIA = (
    "confianca_preco",
    "preco_origem",
    "preco_capturado_em",
    "preco_idade_dias",
    "preco_estado",
    "preco_regiao",
    "preco_regiao_aproximada",
    "taxa_destino_regiao",
    "taxa_destino_aproximada",
)


@dataclass(frozen=True, slots=True)
class PedidoDeSync:
    provedor: str
    regiao: str | None
    sku: str
    tipo: str
    motivo: str


@dataclass(frozen=True, slots=True)
class Verificacao:
    resultado: str
    evidencia: dict[str, Any]
    pedido: PedidoDeSync | None = None
    # (provedor, sku, região) do ponto de preço, para a observação do agente
    chave_do_preco: tuple[str, str, str] | None = None


def _equivalentes_de_compute(
    payload: Mapping[str, Any],
) -> Iterator[tuple[str, str, str | None, dict[str, Any]]]:
    for pool in payload.get("pools_computacao") or []:
        for e in pool.get("equivalentes") or []:
            financeiro = e.get("financeiro") or {}
            yield (
                str(e.get("provedor") or "").lower(),
                str(e.get("sku") or "").lower(),
                str(financeiro.get("preco_regiao") or "").lower() or None,
                financeiro,
            )


def _taxas_de_categoria(
    payload: Mapping[str, Any],
) -> Iterator[tuple[str, str, str | None, dict[str, Any]]]:
    auxiliares = payload.get("custos_auxiliares") or {}
    for chave, cotacoes in auxiliares.items():
        if not chave.startswith("equivalentes_") or not isinstance(cotacoes, list):
            continue
        for q in cotacoes:
            if isinstance(q, dict):
                yield (
                    str(q.get("provedor") or "").lower(),
                    str(q.get("categoria") or "").lower(),
                    str(q.get("taxa_destino_regiao") or "").lower() or None,
                    q,
                )


def _oficial_e_recente(financeiro: Mapping[str, Any], *, categoria: bool) -> bool:
    if financeiro.get("confianca_preco") != "official":
        return False
    if financeiro.get("preco_regiao_aproximada") or financeiro.get("taxa_destino_aproximada"):
        return False
    if categoria:
        # A taxa de categoria não traz idade no payload: oficial da região basta
        return True
    estado = financeiro.get("preco_estado")
    if estado:
        return bool(estado == "vivo")
    idade = financeiro.get("preco_idade_dias")
    return isinstance(idade, int | float) and idade <= _IDADE_MAXIMA_DIAS


def verificar_contestacao(referencia: object, payload: Mapping[str, Any]) -> Verificacao:
    """Confere a contestação de preço contra o snapshot."""
    partes = [p.strip() for p in str(referencia or "").split("/")]
    partes += [""] * (_PARTES_DA_REFERENCIA - len(partes))
    if not partes[0] or not partes[1]:
        return Verificacao(NAO_VERIFICAVEL, {"motivo": "referência sem provedor e sku"})
    provedor, sku = partes[0].lower(), partes[1].lower()
    regiao = partes[2].lower() or None
    for fonte, categoria in ((_equivalentes_de_compute, False), (_taxas_de_categoria, True)):
        for prov, chave, regiao_do_preco, financeiro in fonte(payload):
            if prov != provedor or chave != sku:
                continue
            if regiao is not None and regiao_do_preco is not None and regiao_do_preco != regiao:
                continue
            evidencia = {k: financeiro[k] for k in _EVIDENCIA if financeiro.get(k) is not None}
            ponto = regiao_do_preco or regiao
            chave_do_preco = (provedor, partes[1], ponto) if ponto else None
            if _oficial_e_recente(financeiro, categoria=categoria):
                return Verificacao(VERIFICADO, evidencia, chave_do_preco=chave_do_preco)
            pedido = PedidoDeSync(
                provedor=provedor,
                regiao=ponto,
                sku=partes[1],
                tipo=sku if categoria else "virtual_server",
                motivo="contestação do agente sobre preço não oficial, velho ou de outra região",
            )
            return Verificacao(A_RECONFIRMAR, evidencia, pedido, chave_do_preco)
    return Verificacao(NAO_VERIFICAVEL, {"motivo": "referência não achada no snapshot"})


@dataclass(frozen=True, slots=True)
class ObservacaoDoAgente:
    """O preço que o agente disse para um ponto, para o ciclo de vida.

    Observa, não confirma: nunca revive o preço nem sobe o score
    (PLANO_CICLO_DE_VIDA_DO_PRECO, decisão do usuário de 16/10)."""

    chave_do_preco: tuple[str, str, str]
    preco: Decimal
    detalhe: dict[str, Any]


def _preco(valor: object) -> Decimal | None:
    try:
        preco = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return preco if preco.is_finite() and preco > 0 else None


def acoes_da_contestacao(
    sugestoes: Iterable[Mapping[str, Any]], payload: Mapping[str, Any]
) -> tuple[list[PedidoDeSync], list[ObservacaoDoAgente]]:
    """Pedidos de sync e observações do agente que as contestações de preço geram.

    A observação só existe quando o agente mandou `preco_sugerido` e a referência
    achou o ponto de preço."""
    pedidos: list[PedidoDeSync] = []
    observacoes: list[ObservacaoDoAgente] = []
    for s in sugestoes:
        if str(s.get("tipo", "")) != "contestacao_preco":
            continue
        v = verificar_contestacao(s.get("referencia"), payload)
        if v.pedido is not None and v.pedido not in pedidos:
            pedidos.append(v.pedido)
        preco = _preco(s.get("preco_sugerido"))
        if v.chave_do_preco is not None and preco is not None:
            observacoes.append(
                ObservacaoDoAgente(
                    chave_do_preco=v.chave_do_preco,
                    preco=preco,
                    detalhe={k: s[k] for k in ("titulo", "detalhe", "severidade") if s.get(k)},
                )
            )
    return pedidos, observacoes
