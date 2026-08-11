"""Autoridade por tipo de afirmação.

O que estes testes protegem: a assimetria entre *ter* um número e estar
*autorizado* a impô-lo. O backend tem preço, mas o dele pode estar mais velho que
o conhecimento do agente — 79% do dinheiro cotado no bill AWS vem de preço
não-oficial. Sobrescrever ali seria trocar ruído por viés.
"""

from __future__ import annotations

import pytest

from app.domain.services.authority import (
    Categoria,
    aplicar_autoridade,
    classificar,
)

# ── classificação ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("caminho", "esperada"),
    [
        # aritmética sobre o que já temos
        ("recommended_provider", Categoria.DERIVACAO),
        ("provider_comparison.0.estimated_monthly_cost", Categoria.DERIVACAO),
        ("savings_summary.percentage_reduction", Categoria.DERIVACAO),
        ("viability_score", Categoria.DERIVACAO),
        # preço envelhece
        ("cross_cloud_equivalents.2.estimated_monthly_cost", Categoria.ESTIMATIVA),
        # não há fato da questão
        ("recommendation", Categoria.JULGAMENTO),
        ("provider_comparison.0.strengths", Categoria.JULGAMENTO),
        ("cross_cloud_equivalents.0.migration_risk", Categoria.JULGAMENTO),
        # ninguém consegue calcular
        ("savings_summary.payback_period_months", Categoria.SEM_BASE),
        ("oneshift_scores.technical_score", Categoria.SEM_BASE),
    ],
)
def test_classificacao(caminho: str, esperada: Categoria) -> None:
    assert classificar(caminho) is esperada


def test_indice_de_lista_e_indiferente() -> None:
    """A regra vale para o campo, não para a posição."""
    for i in (0, 3, 17):
        assert classificar(f"provider_comparison.{i}.estimated_monthly_cost") is (
            Categoria.DERIVACAO
        )


def test_mesmo_nome_muda_de_dono_conforme_o_contexto() -> None:
    """`confidence_level` em provider_comparison é derivado da cobertura;
    em agent_views é o quanto o modelo confia na própria leitura.

    É por isso que a classificação é por CAMINHO e não por nome simples.
    """
    assert classificar("provider_comparison.0.confidence_level") is Categoria.DERIVACAO
    assert classificar("agent_views.finops.confidence_level") is Categoria.JULGAMENTO


def test_campo_desconhecido_vira_julgamento() -> None:
    """Default seguro: campo novo que ninguém classificou é do agente.

    Assumir DERIVACAO faria o backend sobrescrever com None algo que ele não
    sabe calcular — apagando conteúdo em silêncio.
    """
    assert classificar("campo_que_alguem_adicionou_amanha") is Categoria.JULGAMENTO


# ── aplicação ───────────────────────────────────────────────────────────


def _resposta() -> dict:
    return {
        "recommended_provider": "AWS",
        "recommendation": "migrar para AWS o quanto antes",
        "provider_comparison": [
            {
                "provider": "OCI",
                "estimated_monthly_cost": 9999.0,
                "strengths": ["preço agressivo"],
            }
        ],
        "cross_cloud_equivalents": [
            {"target_provider": "OCI", "estimated_monthly_cost": 111.0}
        ],
        "savings_summary": {"total_monthly_savings_usd": 1.0, "payback_period_months": 4},
    }


def test_derivacao_e_sobrescrita() -> None:
    r = aplicar_autoridade(
        _resposta(),
        {
            "recommended_provider": "OCI",
            "provider_comparison.0.estimated_monthly_cost": 2394.08,
            "savings_summary.total_monthly_savings_usd": 2487.53,
        },
    )
    assert r.resposta["recommended_provider"] == "OCI"
    assert r.resposta["provider_comparison"][0]["estimated_monthly_cost"] == 2394.08
    assert r.resposta["savings_summary"]["total_monthly_savings_usd"] == 2487.53
    assert len(r.substituicoes) == 3


def test_julgamento_passa_intocado() -> None:
    """Mesmo que o backend mande um valor, julgamento não é dele."""
    r = aplicar_autoridade(
        _resposta(),
        {
            "recommendation": "TEXTO DO BACKEND",
            "provider_comparison.0.strengths": ["inventado"],
        },
    )
    assert r.resposta["recommendation"] == "migrar para AWS o quanto antes"
    assert r.resposta["provider_comparison"][0]["strengths"] == ["preço agressivo"]
    assert r.substituicoes == ()


def test_estimativa_nao_e_sobrescrita() -> None:
    """O ponto central: TER o número não autoriza a impor.

    Para preço, o nosso pode estar mais velho que o conhecimento do agente.
    A discordância vira contestação e rebaixa a confiança — não troca o valor.
    """
    r = aplicar_autoridade(
        _resposta(), {"cross_cloud_equivalents.0.estimated_monthly_cost": 236.52}
    )
    assert r.resposta["cross_cloud_equivalents"][0]["estimated_monthly_cost"] == 111.0
    assert r.substituicoes == ()


def test_sem_base_passa_intocado() -> None:
    """`payback` continua o do agente aqui.

    Não é descuido: o tratamento dele é exigir a premissa declarada no schema de
    entrada (plano v2 §5), não sobrescrever com um número que não temos.
    """
    r = aplicar_autoridade(_resposta(), {"savings_summary.payback_period_months": 7})
    assert r.resposta["savings_summary"]["payback_period_months"] == 4
    assert r.substituicoes == ()


# ── segurança da escrita ────────────────────────────────────────────────


def test_nao_cria_estrutura_ausente() -> None:
    """Inventar `provider_comparison[3]` produziria item órfão, sem a narrativa
    que o acompanha. Campo que o agente não produziu é assunto de emissão, não
    de autoridade."""
    r = aplicar_autoridade(
        _resposta(),
        {
            "provider_comparison.3.estimated_monthly_cost": 1.0,
            "campo.que.nao.existe": 2.0,
        },
    )
    assert len(r.resposta["provider_comparison"]) == 1
    assert "campo" not in r.resposta
    assert r.substituicoes == ()


def test_valor_igual_nao_conta_como_substituicao() -> None:
    """Concordância não é correção — inflaria a métrica de quanto o agente erra."""
    r = aplicar_autoridade(_resposta(), {"recommended_provider": "AWS"})
    assert r.substituicoes == ()


def test_entrada_nao_e_mutada() -> None:
    """O bruto do agente é persistido lado a lado com o corrigido; mutá-lo
    apagaria a evidência de quanto ele errou."""
    original = _resposta()
    aplicar_autoridade(original, {"recommended_provider": "OCI"})
    assert original["recommended_provider"] == "AWS"


def test_ledger_registra_os_dois_valores() -> None:
    """Sem o valor do agente não há como justificar depois por que o número
    mudou na tela."""
    r = aplicar_autoridade(_resposta(), {"recommended_provider": "OCI"})
    d = r.as_ledger()
    assert d["campos_sobrescritos"] == 1
    assert d["detalhe"] == [
        {"campo": "recommended_provider", "agente": "AWS", "backend": "OCI"}
    ]
