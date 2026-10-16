"""Ciclo de vida do preço (PLANO_CICLO_DE_VIDA_DO_PRECO, P3.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.services.ciclo_de_vida_do_preco import (
    Estado,
    Fonte,
    Observacao,
    PesoDaFonte,
    RegraDoCiclo,
    avaliar,
    duracao_dos_estados,
    estado_do_score,
    informacao_do_agente,
    regra_para,
)

AGORA = datetime(2026, 10, 16, tzinfo=UTC)
COMPUTE = RegraDoCiclo(meia_vida_dias=Decimal(90))
PESOS = {
    Fonte.OFICIAL: PesoDaFonte(peso=Decimal("1"), confirma=True),
    Fonte.BILLING: PesoDaFonte(peso=Decimal("0.9"), confirma=True),
    Fonte.REFERENCIA: PesoDaFonte(peso=Decimal("0.7"), confirma=True),
    Fonte.AGENTE: PesoDaFonte(peso=Decimal("0"), confirma=False),
}


def _obs(fonte: Fonte, dias: int, preco: str = "0.10", *, sob_demanda: bool = True) -> Observacao:
    return Observacao(
        fonte=fonte,
        preco=Decimal(preco),
        observado_em=AGORA - timedelta(days=dias),
        sob_demanda=sob_demanda,
    )


@pytest.mark.parametrize(
    ("score", "estado"),
    [
        ("1", Estado.VIVO),
        ("0.80", Estado.VIVO),
        ("0.7999", Estado.MADURO),
        ("0.60", Estado.MADURO),
        ("0.5999", Estado.EM_DECOMPOSICAO),
        ("0.30", Estado.EM_DECOMPOSICAO),
        ("0.2999", Estado.ADUBO),
        ("0", Estado.ADUBO),
    ],
)
def test_estado_pelo_score(score: str, estado: Estado) -> None:
    assert estado_do_score(Decimal(score), COMPUTE) is estado


def test_preco_oficial_novo_e_vivo() -> None:
    avaliacao = avaliar([_obs(Fonte.OFICIAL, 0)], COMPUTE, PESOS, AGORA)
    assert avaliacao.estado is Estado.VIVO
    assert avaliacao.score == Decimal("1.0000")
    assert avaliacao.ultima_fonte == Fonte.OFICIAL
    assert avaliacao.idade_dias == 0


def test_meia_vida_divide_o_score_por_dois() -> None:
    avaliacao = avaliar([_obs(Fonte.OFICIAL, 90)], COMPUTE, PESOS, AGORA)
    assert avaliacao.score == Decimal("0.5000")
    assert avaliacao.estado is Estado.EM_DECOMPOSICAO


def test_preco_antigo_vira_adubo_e_nao_some() -> None:
    avaliacao = avaliar([_obs(Fonte.OFICIAL, 200)], COMPUTE, PESOS, AGORA)
    assert avaliacao.estado is Estado.ADUBO
    # Nenhum estado é terminal: o preço segue lá, com a confirmação que tinha
    assert avaliacao.confirmado_em == AGORA - timedelta(days=200)


def test_confirmacao_oficial_nova_revive() -> None:
    avaliacao = avaliar([_obs(Fonte.OFICIAL, 200), _obs(Fonte.OFICIAL, 1)], COMPUTE, PESOS, AGORA)
    assert avaliacao.estado is Estado.VIVO
    assert avaliacao.idade_dias == 1


def test_agente_nunca_revive_nem_sobe_o_score() -> None:
    # A resposta do agente pode ser alucinação: observa, não confirma
    antes = avaliar([_obs(Fonte.OFICIAL, 200)], COMPUTE, PESOS, AGORA)
    depois = avaliar(
        [_obs(Fonte.OFICIAL, 200), _obs(Fonte.AGENTE, 0, "0.12")], COMPUTE, PESOS, AGORA
    )
    assert depois == antes


def test_agente_sozinho_nao_da_estado_vivo() -> None:
    avaliacao = avaliar([_obs(Fonte.AGENTE, 0)], COMPUTE, PESOS, AGORA)
    assert avaliacao.estado is Estado.ADUBO
    assert avaliacao.confirmado_em is None
    assert avaliacao.score == Decimal("0")


def test_billing_com_desconto_nao_confirma_preco_de_tabela() -> None:
    avaliacao = avaliar(
        [_obs(Fonte.OFICIAL, 200), _obs(Fonte.BILLING, 0, sob_demanda=False)], COMPUTE, PESOS, AGORA
    )
    assert avaliacao.estado is Estado.ADUBO


def test_referencia_recente_vence_oficial_velho_com_peso_menor() -> None:
    avaliacao = avaliar(
        [_obs(Fonte.OFICIAL, 200), _obs(Fonte.REFERENCIA, 5)], COMPUTE, PESOS, AGORA
    )
    assert avaliacao.ultima_fonte == Fonte.REFERENCIA
    # 0,7 de peso: a referência nunca é VIVO
    assert avaliacao.estado is Estado.MADURO


def test_sem_observacao_e_adubo_sem_confirmacao() -> None:
    avaliacao = avaliar([], COMPUTE, PESOS, AGORA)
    assert (avaliacao.estado, avaliacao.confirmado_em, avaliacao.idade_dias) == (
        Estado.ADUBO,
        None,
        None,
    )


def test_informacao_do_agente_so_ao_lado_de_preco_adubo() -> None:
    agente = _obs(Fonte.AGENTE, 2, "0.12")
    adubo = avaliar([_obs(Fonte.OFICIAL, 200)], COMPUTE, PESOS, AGORA)
    assert informacao_do_agente([_obs(Fonte.OFICIAL, 200), agente], adubo) == agente
    vivo = avaliar([_obs(Fonte.OFICIAL, 1)], COMPUTE, PESOS, AGORA)
    assert informacao_do_agente([_obs(Fonte.OFICIAL, 1), agente], vivo) is None


def test_agente_mais_velho_que_a_confirmacao_nao_aparece() -> None:
    adubo = avaliar([_obs(Fonte.OFICIAL, 200)], COMPUTE, PESOS, AGORA)
    assert informacao_do_agente([_obs(Fonte.OFICIAL, 200), _obs(Fonte.AGENTE, 300)], adubo) is None


def test_duracao_de_cada_estado_do_preco_oficial_de_compute() -> None:
    assert duracao_dos_estados(COMPUTE, Decimal(1)) == {
        Estado.VIVO: (0, 29),
        Estado.MADURO: (29, 66),
        Estado.EM_DECOMPOSICAO: (66, 156),
        Estado.ADUBO: (156, None),
    }


def test_referencia_comeca_madura() -> None:
    duracao = duracao_dos_estados(COMPUTE, Decimal("0.7"))
    assert duracao[Estado.VIVO] is None
    assert duracao[Estado.MADURO] == (0, 20)


def test_regra_mais_especifica_vence() -> None:
    regras = [
        ("*", None, RegraDoCiclo(meia_vida_dias=Decimal(90))),
        ("storage_*", None, RegraDoCiclo(meia_vida_dias=Decimal(180))),
        ("*", "*preview*", RegraDoCiclo(meia_vida_dias=Decimal(30))),
    ]
    assert regra_para(regras, "storage_ssd", "gp3").meia_vida_dias == Decimal(180)
    assert regra_para(regras, "compute", "m7g.large").meia_vida_dias == Decimal(90)
    assert regra_para(regras, "compute", "Standard_D4s_v6 Preview").meia_vida_dias == Decimal(30)
