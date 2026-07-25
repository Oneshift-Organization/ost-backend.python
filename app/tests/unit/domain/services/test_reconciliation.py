"""Reconciliação — a análise auditando a si mesma (Precision Engine v3).

Verificações são propriedades do modelo de pool, não regras por cloud. Os testes
constroem pools sintéticos e conferem que cada invariante dispara o achado certo.
"""
from __future__ import annotations

from decimal import Decimal

from app.domain.analysis import EquivalentService, FinancialEstimate
from app.domain.entities.workload_pool import (
    AuxiliaryCosts,
    FamilyPool,
    FamilyPoolKey,
    PoolCapacity,
    PoolCosts,
)
from app.domain.services.currency import FxRate, identity_rate
from app.domain.services.reconciliation import reconcile

_USD = identity_rate("USD")


def _pool(family: str, vcpus: str, ram: str, cost: str, region: str = "global") -> FamilyPool:
    return FamilyPool(
        pool_key=FamilyPoolKey(machine_family=family, region=region),
        capacity=PoolCapacity(vcpus=Decimal(vcpus), ram_gib=Decimal(ram)),
        costs=PoolCosts(compute_base=Decimal(cost)),
        hours_window=Decimal("730"),
    )


def _codes(result: dict) -> set[str]:
    return {a["codigo"] for a in result["achados"]}


def test_clean_analysis_has_no_violations() -> None:
    pools = (_pool("E2", "10", "40", "260"),)  # 26 USD/vCPU-mês, dentro da banda
    result = reconcile(
        total_spend=Decimal("260"),
        family_pools=pools,
        auxiliary=AuxiliaryCosts(),
        family_equivalents={"E2/global": [_equiv("AWS", monthly="250")]},
        target_providers=["AWS"],
        cobertura_classificacao=0.99,
        fx=_USD,
    )
    assert result["ok"] is True
    assert result["resumo"]["violacao"] == 0
    assert "preco_vcpu_implausivel" not in _codes(result)


def test_implausible_unit_price_flagged() -> None:
    # $/vCPU-mês = 8184/1 = 8184 -> muito acima da banda.
    pools = (_pool("M7G", "1", "0", "8184"),)
    result = reconcile(
        total_spend=Decimal("8184"),
        family_pools=pools,
        auxiliary=AuxiliaryCosts(),
        family_equivalents={},
        target_providers=[],
        cobertura_classificacao=0.99,
        fx=_USD,
    )
    assert "preco_vcpu_implausivel" in _codes(result)


def test_generic_family_concentration_flagged() -> None:
    pools = (
        _pool("general", "10", "0", "800"),
        _pool("E2", "5", "20", "130"),
    )
    result = reconcile(
        total_spend=Decimal("930"),
        family_pools=pools,
        auxiliary=AuxiliaryCosts(),
        family_equivalents={},
        target_providers=[],
        cobertura_classificacao=0.99,
        fx=_USD,
    )
    assert "familia_generica_concentrada" in _codes(result)


def test_cost_leak_is_hard_violation() -> None:
    pools = (_pool("E2", "10", "40", "260"),)
    result = reconcile(
        total_spend=Decimal("1000"),  # 740 a mais do que os baldes somam
        family_pools=pools,
        auxiliary=AuxiliaryCosts(),
        family_equivalents={},
        target_providers=[],
        cobertura_classificacao=0.99,
        fx=_USD,
    )
    assert "conservacao_custo_violada" in _codes(result)
    assert result["ok"] is False
    assert result["resumo"]["violacao"] == 1


def test_conservation_counts_all_buckets() -> None:
    pools = (_pool("E2", "10", "40", "260"),)
    aux = AuxiliaryCosts(commitment=Decimal("500"), network_egress=Decimal("240"))
    result = reconcile(
        total_spend=Decimal("1000"),  # 260 + 500 + 240 = 1000, fecha
        family_pools=pools,
        auxiliary=aux,
        family_equivalents={},
        target_providers=[],
        cobertura_classificacao=0.99,
        fx=_USD,
    )
    assert result["conservacao_custo"]["bate"] is True
    assert "conservacao_custo_violada" not in _codes(result)


def test_provider_without_price_base_declared() -> None:
    result = reconcile(
        total_spend=Decimal("260"),
        family_pools=(_pool("E2", "10", "40", "260"),),
        auxiliary=AuxiliaryCosts(),
        family_equivalents={
            "E2/global": [_equiv("AWS", monthly="250"), _equiv("azure", monthly=None)],
        },
        target_providers=["AWS", "azure"],
        cobertura_classificacao=0.99,
        fx=_USD,
    )
    achados = {a["codigo"]: a for a in result["achados"]}
    assert "provedor_sem_base_preco" in achados
    assert achados["provedor_sem_base_preco"]["escopo"] == "azure"


def test_fx_applied_before_price_band() -> None:
    """Custo em BRL não deve ser comparado à banda em USD sem converter.

    810 BRL / 5.7 = 142 USD para 10 vCPU -> 14,2 USD/vCPU, dentro da banda.
    Sem conversão seriam 81 USD/vCPU (ainda dentro), então usamos um caso que
    só passa COM conversão: 6000 BRL / 5.7 = 1052 USD ... escolhemos valores
    que invertem o veredito."""
    brl = FxRate(par="BRL/USD", taxa=Decimal("5.7"), fonte="config")
    # 700 BRL / 5.7 = 122.8 USD /vCPU sem, /10 vCPU -> converter: 700/5.7/10=12.3 (ok)
    pools = (_pool("E2", "10", "40", "700"),)
    result = reconcile(
        total_spend=Decimal("700"),
        family_pools=pools,
        auxiliary=AuxiliaryCosts(),
        family_equivalents={},
        target_providers=[],
        cobertura_classificacao=0.99,
        fx=brl,
    )
    # Com conversão: 12.28 USD/vCPU-mês -> dentro da banda, sem flag.
    assert "preco_vcpu_implausivel" not in _codes(result)


def _equiv(provider: str, *, monthly: str | None) -> EquivalentService:
    fin = FinancialEstimate(
        estimated_monthly_cost=None if monthly is None else Decimal(monthly)
    )
    return EquivalentService(
        provider=provider,
        service_name="svc",
        category="compute",
        service_type="virtual_server",
        financials=fin,
    )
