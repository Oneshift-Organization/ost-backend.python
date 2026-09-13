"""Reconciliação — a análise auditando a si mesma (Precision Engine v3).

Toda análise passa por aqui antes de sair. As verificações são **propriedades
do modelo de pool**, não regras por cloud: nenhuma cita AWS/GCP/Azure/OCI nem
nome de serviço. O objetivo não é corrigir dados — é fazer o sistema declarar
onde ele mesmo não é confiável, em vez de emitir um número fraco em silêncio.

Cada achado tem um `codigo` estável (para o front/telemetria), uma `severidade`
(`violacao` > `suspeita` > `info`) e os números que o sustentam. O resultado é
gravado em `premissas["reconciliacao"]`.

Função pura, sem I/O. Ver ``docs/precision-engine.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.services.currency import FxRate, convert

if TYPE_CHECKING:
    from app.domain.analysis import EquivalentService
    from app.domain.entities.workload_pool import AuxiliaryCosts, FamilyPool

# Famílias que sinalizam extração de família falha (não há máquina "general").
_GENERIC_FAMILIES = frozenset({"general", "unknown", "_unknown", ""})
# Banda plausível de preço on-demand por vCPU-mês, na moeda de análise (USD).
# Fora disso, ou o custo está inflado (vazamento de commitment) ou a capacidade
# está subdimensionada. Larga de propósito: é um detector de absurdo, não um SLA.
_MIN_PRECO_VCPU_MES = Decimal("2")
_MAX_PRECO_VCPU_MES = Decimal("120")
# Concentração de gasto de compute numa família genérica que dispara suspeita.
_CONCENTRACAO_GENERICA = Decimal("0.25")
# Tolerância da conservação de custo (soma dos baldes == gasto total).
_TOLERANCIA_CONSERVACAO = Decimal("0.005")  # 0,5%
_COBERTURA_MINIMA = Decimal("0.95")
# Fração do gasto cujo `service_type` veio do classificador de SKU (palpite) em
# vez do catálogo curado. Acima disto o DE-PARA por serviço repousa em inferência.
_MAX_TIPO_INFERIDO = Decimal("0.30")


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    codigo: str
    severidade: str  # "violacao" | "suspeita" | "info"
    escopo: str
    detalhe: str
    valores: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "codigo": self.codigo,
            "severidade": self.severidade,
            "escopo": self.escopo,
            "detalhe": self.detalhe,
            "valores": self.valores,
        }


def _q2(value: Decimal) -> float:
    return round(float(value), 2)


def reconcile(
    *,
    total_spend: Decimal,
    family_pools: tuple[FamilyPool, ...],
    auxiliary: AuxiliaryCosts | None,
    family_equivalents: dict[str, list[EquivalentService]],
    target_providers: list[str],
    cobertura_classificacao: float,
    fx: FxRate,
    fracao_tipo_inferido: float = 0.0,
) -> dict[str, object]:
    """Roda todas as verificações e devolve o bloco `reconciliacao`.

    Todos os custos são convertidos para a moeda de análise antes de comparar —
    o gasto de origem pode estar em BRL enquanto os baldes já saíram em USD.
    """
    findings: list[ReconciliationFinding] = []
    total = convert(total_spend, fx) or Decimal("0")

    findings += _check_precos_por_vcpu(family_pools, fx)
    findings += _check_familia_generica(family_pools, fx)
    conservacao = _check_conservacao(total, family_pools, auxiliary, fx, findings)
    findings += _check_providers_sem_preco(family_equivalents, target_providers)
    findings += _check_cobertura(cobertura_classificacao)
    findings += _check_tipo_inferido(fracao_tipo_inferido)

    contagem = {"violacao": 0, "suspeita": 0, "info": 0}
    for f in findings:
        contagem[f.severidade] = contagem.get(f.severidade, 0) + 1

    return {
        "ok": contagem["violacao"] == 0,
        "resumo": contagem,
        "conservacao_custo": conservacao,
        "achados": [f.as_dict() for f in findings],
    }


def _check_precos_por_vcpu(
    family_pools: tuple[FamilyPool, ...], fx: FxRate
) -> list[ReconciliationFinding]:
    """Preço implícito $/vCPU-mês fora da banda plausível.

    Pega o vazamento de commitment que a guarda do agregador não segurou e o
    catálogo defasado, sem saber nada sobre nenhuma cloud."""
    out: list[ReconciliationFinding] = []
    for fp in family_pools:
        if fp.capacity.vcpus <= 0:
            continue
        custo = convert(fp.monthly_cost, fx) or Decimal("0")
        # Capacidade real com custo direto zero não é preço implausível — é uma
        # carga coberta por commitment/reserva (o custo está no balde commitment).
        # Situação real, não erro de modelagem: reporta como info, sem ruído.
        if custo <= 0:
            out.append(
                ReconciliationFinding(
                    codigo="capacidade_sem_custo_direto",
                    severidade="info",
                    escopo=fp.pool_key.as_str(),
                    detalhe=(
                        f"{_q2(fp.capacity.vcpus)} vCPU sem custo direto — "
                        "provavelmente coberto por commitment/reserva"
                    ),
                    valores={"vcpus": _q2(fp.capacity.vcpus)},
                )
            )
            continue
        preco = custo / fp.capacity.vcpus
        if preco < _MIN_PRECO_VCPU_MES or preco > _MAX_PRECO_VCPU_MES:
            out.append(
                ReconciliationFinding(
                    codigo="preco_vcpu_implausivel",
                    severidade="suspeita",
                    escopo=fp.pool_key.as_str(),
                    detalhe=(
                        f"preço implícito {_q2(preco)}/vCPU-mês fora da banda "
                        f"[{_MIN_PRECO_VCPU_MES}, {_MAX_PRECO_VCPU_MES}]"
                    ),
                    valores={
                        "preco_vcpu_mes": _q2(preco),
                        "vcpus": _q2(fp.capacity.vcpus),
                        "custo_mensal": _q2(custo),
                    },
                )
            )
    return out


def _check_familia_generica(
    family_pools: tuple[FamilyPool, ...], fx: FxRate
) -> list[ReconciliationFinding]:
    """Família genérica concentrando gasto = extração de família falhou."""
    total_compute = sum(
        (convert(fp.monthly_cost, fx) or Decimal("0") for fp in family_pools),
        Decimal("0"),
    )
    if total_compute <= 0:
        return []
    out: list[ReconciliationFinding] = []
    for fp in family_pools:
        if fp.pool_key.machine_family.lower() not in _GENERIC_FAMILIES:
            continue
        custo = convert(fp.monthly_cost, fx) or Decimal("0")
        fracao = custo / total_compute
        if fracao >= _CONCENTRACAO_GENERICA:
            out.append(
                ReconciliationFinding(
                    codigo="familia_generica_concentrada",
                    severidade="suspeita",
                    escopo=fp.pool_key.as_str(),
                    detalhe=(
                        f"{_q2(fracao * 100)}% do gasto de compute numa família "
                        "genérica — família de máquina provavelmente não extraída"
                    ),
                    valores={"fracao": _q2(fracao), "custo_mensal": _q2(custo)},
                )
            )
    return out


def _check_conservacao(
    total: Decimal,
    family_pools: tuple[FamilyPool, ...],
    auxiliary: AuxiliaryCosts | None,
    fx: FxRate,
    findings: list[ReconciliationFinding],
) -> dict[str, object]:
    """Soma de todos os baldes deve bater com o gasto total. Nada some."""
    pools_total = sum(
        (convert(fp.monthly_cost, fx) or Decimal("0") for fp in family_pools),
        Decimal("0"),
    )
    aux_total = Decimal("0")
    if auxiliary is not None:
        for bucket in (
            auxiliary.gke_control_plane,
            auxiliary.storage_ssd,
            auxiliary.storage_hdd,
            auxiliary.object_storage,
            auxiliary.network_egress,
            auxiliary.database,
            auxiliary.commitment,
            auxiliary.orphan,
        ):
            aux_total += convert(bucket, fx) or Decimal("0")

    # Pools com capacidade zero são descartados na agregação; seu custo já está
    # nos baldes (commitment/orphan). O que sobra é vazamento real.
    somado = pools_total + aux_total
    diff = total - somado
    limite = total * _TOLERANCIA_CONSERVACAO
    if abs(diff) > limite and total > 0:
        findings.append(
            ReconciliationFinding(
                codigo="conservacao_custo_violada",
                severidade="violacao",
                escopo="total",
                detalhe=(
                    f"soma dos baldes {_q2(somado)} difere do gasto total "
                    f"{_q2(total)} em {_q2(diff)} (> {_q2(limite)})"
                ),
                valores={
                    "total": _q2(total),
                    "somado": _q2(somado),
                    "diferenca": _q2(diff),
                },
            )
        )
    return {
        "total": _q2(total),
        "pools": _q2(pools_total),
        "auxiliares": _q2(aux_total),
        "diferenca": _q2(diff),
        "bate": abs(diff) <= limite,
    }


def _check_providers_sem_preco(
    family_equivalents: dict[str, list[EquivalentService]],
    target_providers: list[str],
) -> list[ReconciliationFinding]:
    """Provedor-alvo que nunca aparece com financeiro = comparação sem base."""
    com_preco: set[str] = set()
    for equivs in family_equivalents.values():
        for e in equivs:
            if e.financials is not None and e.financials.estimated_monthly_cost is not None:
                com_preco.add(e.provider.lower())
    out: list[ReconciliationFinding] = []
    for prov in target_providers:
        if prov.lower() not in com_preco:
            out.append(
                ReconciliationFinding(
                    codigo="provedor_sem_base_preco",
                    severidade="info",
                    escopo=prov,
                    detalhe=(
                        f"{prov}: comparação de compute indisponível — nenhuma "
                        "base de preço no catálogo para dimensionar o pool"
                    ),
                    valores={"provedor": prov},
                )
            )
    return out


def _check_cobertura(cobertura: float) -> list[ReconciliationFinding]:
    if Decimal(str(cobertura)) >= _COBERTURA_MINIMA:
        return []
    return [
        ReconciliationFinding(
            codigo="cobertura_classificacao_baixa",
            severidade="suspeita",
            escopo="total",
            detalhe=(
                f"cobertura de classificação {_q2(Decimal(str(cobertura)) * 100)}% "
                f"abaixo do mínimo {_q2(_COBERTURA_MINIMA * 100)}%"
            ),
            valores={"cobertura": round(cobertura, 4)},
        )
    ]


def _check_tipo_inferido(fracao: float) -> list[ReconciliationFinding]:
    """Gasto cujo tipo de serviço é palpite, não verdade curada.

    O tipo determina quais equivalentes são oferecidos; um tipo inferido errado
    produz DE-PARA errado apresentado como fato (foi assim que object storage
    virou block storage). O sistema precisa declarar quando está inferindo.
    """
    valor = Decimal(str(fracao))
    if valor <= _MAX_TIPO_INFERIDO:
        return []
    return [
        ReconciliationFinding(
            codigo="tipo_servico_inferido_concentrado",
            severidade="suspeita",
            escopo="total",
            detalhe=(
                f"{_q2(valor * 100)}% do gasto tem service_type inferido pelo "
                "classificador (serviço ausente do catálogo curado) — o DE-PARA "
                "desses serviços repousa em palpite"
            ),
            valores={"fracao": round(fracao, 4)},
        )
    ]
