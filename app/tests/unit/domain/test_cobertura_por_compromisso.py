"""Pools com capacidade e custo zero.

O caso real: um bill AWS com Savings Plan traz `BoxUsage:c5a.large` com 5.701
horas e $0,00, e `ComputeSP:3yrNoUpfront` com $8.184. As instâncias rodaram — o
custo existe, mas é faturado na linha do compromisso.

O agregador segrega o compromisso de propósito (custo sem capacidade explodiria
o $/vCPU). O efeito colateral é o inverso, e é o que estes testes cobrem: o pool
fica com capacidade e custo zero, e `cost_delta = destino - 0` faz a migração
parecer que **adiciona** o custo inteiro.

No CSV versionado isso atinge 3 dos 20 pools.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.services.cobertura_por_compromisso import avaliar_cobertura

D = Decimal


def test_pool_com_capacidade_e_sem_custo_e_detectado() -> None:
    c = avaliar_cobertura(
        {"C5A/global": (D("15.6"), D("0")), "M7G/global": (D("224.3"), D("5475.09"))},
        compromisso=D("8184"),
    )
    assert c.existe
    assert c.pools == ("C5A/global",)


def test_pool_pago_nao_entra() -> None:
    c = avaliar_cobertura({"M7G/global": (D("224.3"), D("5475.09"))}, D("0"))
    assert not c.existe
    assert c.pools == ()


def test_pool_vazio_nao_vira_ruido() -> None:
    """Capacidade zero e custo zero é pool vazio — a agregação já o descarta.
    Incluí-lo aqui encheria o alerta de ruído e faria o sinal ser ignorado."""
    c = avaliar_cobertura({"X/global": (D("0"), D("0"))}, D("100"))
    assert not c.existe


def test_a_fracao_da_capacidade_e_o_que_dimensiona_o_problema() -> None:
    """3 pools de 20 pode ser irrelevante ou metade da frota — o que decide é
    quanta capacidade eles representam."""
    c = avaliar_cobertura(
        {"A": (D("25"), D("0")), "B": (D("25"), D("0")), "C": (D("50"), D("1000"))},
        compromisso=D("900"),
    )
    assert c.vcpus_sem_custo == D("50")
    assert c.vcpus_total == D("100")
    assert c.fracao_da_capacidade == D("0.5")


def test_sem_pool_nenhum_nao_divide_por_zero() -> None:
    c = avaliar_cobertura({}, D("0"))
    assert c.fracao_da_capacidade == D("0")
    assert not c.existe


def test_a_ordem_dos_pools_e_estavel() -> None:
    """O ledger vai para o payload, e payload que muda de ordem entre execuções
    quebra a reprodutibilidade da request."""
    entrada = {"Z": (D("1"), D("0")), "A": (D("1"), D("0")), "M": (D("1"), D("0"))}
    assert avaliar_cobertura(entrada, D("0")).pools == ("A", "M", "Z")


# ── o ledger ────────────────────────────────────────────────────────────


def test_o_ledger_diz_que_a_economia_esta_superestimada() -> None:
    """O número sozinho não diz o que fazer: quem lê precisa saber que a
    economia daqueles pools está errada, e para que lado."""
    ledger = avaliar_cobertura({"C5A": (D("15.6"), D("0"))}, D("8184")).as_ledger()
    assert ledger["pools"] == ["C5A"]
    assert ledger["compromisso_no_bill"] == 8184.0
    assert "SUPERESTIMADA" in str(ledger["explicacao"])
    assert "Savings Plan" in str(ledger["explicacao"])


def test_o_ledger_admite_que_nao_rateia() -> None:
    """`ComputeSP` é fungível entre famílias e `EC2SP:r7g` é específico de uma.
    Decidir quanto de cada cobre qual pool é modelo, não fato — e inventar esse
    número trocaria um erro visível por um plausível."""
    ledger = avaliar_cobertura({"C5A": (D("1"), D("0"))}, D("100")).as_ledger()
    assert "Não rateamos" in str(ledger["explicacao"])
