"""Confiança da análise: conta, não impressão.

Antes, o nível de confiança vinha do LLM — um modelo opinando sobre a qualidade
dos dados que ele mesmo recebeu. Aqui vira razão de custo com preço oficial
sobre custo total cotado.

O número que motivou isto: no bill AWS real, 42% do dinheiro cotado sai de
catálogo seed. A análise saía com aparência de precisão sobre isso.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.confidence import avaliar_confianca, rebaixar


def _c(**kw: str) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in kw.items()}


# ── níveis ──────────────────────────────────────────────────────────────


def test_tudo_oficial_e_alta() -> None:
    r = avaliar_confianca(_c(official="1000"))
    assert r.nivel == "high"
    assert r.razao_confiavel == 1.0


def test_o_caso_real_do_bill_aws_e_baixa() -> None:
    """Os três rótulos que aparecem em produção, com os valores medidos.

    $47k oficial (azure+oci compute), $34k seed (alvos GCP sem preço oficial),
    $147k reference (categorias não-compute) → razão 0,21.

    Se este teste passar a dar 'medium' ou 'high', ou o sync de preços avançou
    (ótimo, atualize os números) ou alguém afrouxou o limiar (não ótimo).
    """
    r = avaliar_confianca(_c(official="47259", low="34143", reference="147655"))
    assert r.nivel == "low"
    assert r.razao_confiavel == pytest.approx(0.21, abs=0.01)


def test_reference_nao_conta_como_oficial() -> None:
    """`reference` é taxa publicada pelo provedor — melhor que catálogo seed,
    mas não é preço coletado da API.

    O próprio seed a rotula assim de propósito, e o `category_rate_client` faz
    upgrade para `official` quando consegue buscar. Tratá-la como confiável
    inflaria a métrica exatamente onde ela precisa ser conservadora.
    """
    r = avaliar_confianca(_c(reference="1000"))
    assert r.nivel == "low"
    assert r.custo_confiavel == 0.0


@pytest.mark.parametrize(
    ("oficial", "seed", "esperado"),
    [
        ("90", "10", "high"),    # exatamente no limiar
        ("89", "11", "medium"),
        ("60", "40", "medium"),  # exatamente no limiar
        ("59", "41", "low"),
    ],
)
def test_limiares_sao_inclusivos_na_borda(oficial: str, seed: str, esperado: str) -> None:
    assert avaliar_confianca(_c(official=oficial, low=seed)).nivel == esperado


def test_sem_custo_cotado_e_baixa() -> None:
    """Não saber nada nunca é alta confiança.

    Devolver 'high' para conjunto vazio seria bug silencioso: o caminho feliz
    nunca exercita este caso.
    """
    assert avaliar_confianca({}).nivel == "low"
    assert avaliar_confianca(_c(official="0")).nivel == "low"


def test_high_mock_nao_conta_como_oficial() -> None:
    """`high (mock)` existe no matcher e engana pelo nome.

    Tem 'high' no rótulo, mas descreve confiança do matching técnico com preço
    de mock. Se a comparação fosse por prefixo, preço inventado contaria como
    confiável — exatamente o que este módulo existe para evitar.
    """
    r = avaliar_confianca({"high (mock)": Decimal("1000")})
    assert r.nivel == "low"
    assert r.custo_confiavel == 0.0


def test_ledger_carrega_os_numeros_da_conta() -> None:
    """Nível sozinho é tão opaco quanto a opinião que substitui — com a razão,
    quem lê pode discordar do limiar em vez do resultado."""
    d = avaliar_confianca(_c(official="47259", low="34143")).as_ledger()
    assert set(d) == {
        "nivel",
        "razao_custo_confiavel",
        "custo_com_preco_oficial",
        "custo_total_cotado",
        "custo_por_rotulo",
    }
    assert d["custo_com_preco_oficial"] == 47259.0
    assert d["custo_total_cotado"] == 81402.0
    # A quebra por rótulo evita que a razão engane para o lado pessimista:
    # `reference` e `low` são coisas muito diferentes.
    assert d["custo_por_rotulo"] == {"low": 34143.0, "official": 47259.0}


# ── rebaixamento (base da contestação do agente) ────────────────────────


@pytest.mark.parametrize(
    ("de", "para"), [("high", "medium"), ("medium", "low"), ("low", "low")]
)
def test_rebaixar_um_degrau(de: str, para: str) -> None:
    assert rebaixar(de) == para


def test_rebaixar_nao_passa_do_piso() -> None:
    assert rebaixar("high", degraus=9) == "low"


def test_rebaixar_nivel_desconhecido_vira_baixo() -> None:
    """Entrada inesperada degrada para o lado seguro, não estoura."""
    assert rebaixar("altíssima") == "low"


def test_rebaixar_zero_degraus_nao_muda() -> None:
    assert rebaixar("high", degraus=0) == "high"
