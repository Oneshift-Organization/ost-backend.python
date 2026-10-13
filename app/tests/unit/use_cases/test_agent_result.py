"""Recepção da análise do agente.

O que estes testes protegem: a inteligência do modelo entra como **sinal**, não
como fonte. Ele contesta um preço, a confiança cai — mas o número não muda,
porque trocá-lo por um palpite custaria a reprodutibilidade.
"""

from __future__ import annotations

from typing import Any

from app.use_cases.analysis.agent_result import (
    mapear_verdade,
    processar_sugestoes,
    receber_analise_do_agente,
)

# ── fixtures ────────────────────────────────────────────────────────────


def _payload() -> dict:
    return {
        "resumo_executivo": {
            "recomendado": "oci",
            "custo_mensal_atual": 4881.60,
            "por_provedor": [
                {
                    "provedor": "oci",
                    "custo_estimado_total": 2394.08,
                    "economia_mensal": 2487.53,
                    "confianca": "official",
                },
                {
                    "provedor": "azure",
                    "custo_estimado_total": 4305.0,
                    "economia_mensal": 576.6,
                    "confianca": "reference",
                },
            ],
        },
        "premissas": {"confianca_analise": {"nivel": "medium"}},
    }


def _agente() -> dict:
    # Ordem DIFERENTE da do backend, de propósito.
    return {
        "recommendation": "migrar para Azure",
        "recommended_provider": "AZURE",
        "provider_comparison": [
            {"provider": "azure", "estimated_monthly_cost": 100.0, "strengths": ["rede"]},
            {"provider": "oci", "estimated_monthly_cost": 200.0, "strengths": ["preço"]},
        ],
        "savings_summary": {"total_monthly_savings_usd": 42.0, "payback_period_months": 4},
        "cross_cloud_equivalents": [{"estimated_monthly_cost": 111.0}],
    }


# ── mapeamento por nome, não por posição ────────────────────────────────


def test_casa_provedor_por_nome_e_nao_por_indice() -> None:
    """O agente mandou azure em [0] e oci em [1]; o backend tem a ordem oposta.

    Casar por posição escreveria o custo da OCI no item do Azure — número certo
    no lugar errado, que é pior que número errado, porque parece plausível.
    """
    v = mapear_verdade(_payload(), _agente())
    assert v["provider_comparison.0.estimated_monthly_cost"] == 4305.0   # azure
    assert v["provider_comparison.1.estimated_monthly_cost"] == 2394.08  # oci


def test_provedor_inventado_pelo_agente_e_ignorado() -> None:
    agente = {**_agente(), "provider_comparison": [{"provider": "ibm_cloud"}]}
    v = mapear_verdade(_payload(), agente)
    assert not any(k.startswith("provider_comparison.") for k in v)


def test_payload_sem_resumo_nao_produz_verdade() -> None:
    """Sem base de comparação, sobrescrever às cegas é pior que não sobrescrever."""
    assert mapear_verdade({}, _agente()) == {}


# ── autoridade aplicada ─────────────────────────────────────────────────


def test_derivacao_corrigida_e_julgamento_preservado() -> None:
    r = receber_analise_do_agente(_payload(), _agente())

    assert r.resposta["recommended_provider"] == "OCI", "o agente dizia AZURE"
    assert r.resposta["savings_summary"]["total_monthly_savings_usd"] == 2487.53
    assert r.resposta["recommendation"] == "migrar para Azure", "julgamento é dele"
    assert r.resposta["provider_comparison"][0]["strengths"] == ["rede"]


def test_preco_de_equivalente_nao_e_sobrescrito() -> None:
    """Estimativa: o nosso preço pode estar mais velho que o conhecimento do
    agente. A discordância vira contestação, não substituição."""
    r = receber_analise_do_agente(_payload(), _agente())
    assert r.resposta["cross_cloud_equivalents"][0]["estimated_monthly_cost"] == 111.0


def test_payback_do_agente_sobrevive() -> None:
    """O backend não calcula payback — não temos custo de migração. O controle
    é exigir a premissa no schema de entrada, não sobrescrever."""
    r = receber_analise_do_agente(_payload(), _agente())
    assert r.resposta["savings_summary"]["payback_period_months"] == 4


# ── payback derivado da premissa (PLANO_PRECO_REGIONAL D11) ─────────────


def _com_payback(meses: int, premissa: float) -> dict[str, Any]:
    return {
        **_agente(),
        "payback": {"meses": meses, "premissa_custo_migracao_usd": premissa, "base": "3 squads"},
    }


def test_payback_e_a_premissa_dividida_pela_economia_do_backend() -> None:
    r = receber_analise_do_agente(_payload(), _com_payback(meses=2, premissa=10_000))

    payback = r.resposta["payback"]
    assert payback["meses"] == 5, "⌈10000 ÷ 2487,53⌉"
    assert payback["premissa_custo_migracao_usd"] == 10_000, "a premissa é do agente"
    assert payback["base"] == "3 squads"
    assert payback["economia_mensal_usd"] == 2487.53
    assert payback["calculado_pelo_backend"] is True
    assert r.resposta["savings_summary"]["payback_period_months"] == 5
    assert ("payback.meses", 2, 5) in {
        (s.caminho, s.valor_agente, s.valor_backend) for s in r.autoridade.substituicoes
    }


def test_sem_economia_nao_ha_payback() -> None:
    payload = _payload()
    payload["resumo_executivo"]["recomendado"] = None
    r = receber_analise_do_agente(payload, _com_payback(meses=6, premissa=10_000))
    assert r.resposta["payback"]["meses"] is None
    assert "não se paga" in r.resposta["payback"]["motivo_backend"]


def test_premissa_zero_paga_na_hora() -> None:
    r = receber_analise_do_agente(_payload(), _com_payback(meses=1, premissa=0))
    assert r.resposta["payback"]["meses"] == 0


def test_meses_iguais_nao_contam_como_sobrescrita() -> None:
    r = receber_analise_do_agente(_payload(), _com_payback(meses=5, premissa=10_000))
    assert "payback.meses" not in {s.caminho for s in r.autoridade.substituicoes}


# ── sugestões ───────────────────────────────────────────────────────────


def test_contestacao_alta_rebaixa_a_confianca() -> None:
    r = receber_analise_do_agente(
        _payload(),
        _agente(),
        sugestoes=[
            {
                "tipo": "contestacao_preco",
                "severidade": "alta",
                "titulo": "preço GCP defasado",
                "detalhe": "~40% abaixo da tabela",
            }
        ],
    )
    assert r.confianca_final == "low", "era medium"
    assert r.sugestoes[0]["efeito_backend"] == "confianca_rebaixada"


def test_contestacao_nao_troca_o_numero() -> None:
    """O ponto central de todo o desenho: a IA vira sinal, não fonte."""
    r = receber_analise_do_agente(
        _payload(),
        _agente(),
        sugestoes=[
            {
                "tipo": "contestacao_preco",
                "severidade": "alta",
                "titulo": "x",
                "detalhe": "y",
            }
        ],
    )
    assert r.resposta["cross_cloud_equivalents"][0]["estimated_monthly_cost"] == 111.0
    assert r.resposta["provider_comparison"][1]["estimated_monthly_cost"] == 2394.08


def test_oportunidade_e_informativa() -> None:
    s, conf = processar_sugestoes(
        [{"tipo": "oportunidade", "severidade": "alta", "titulo": "CUD", "detalhe": "z"}],
        "high",
    )
    assert s[0]["efeito_backend"] == "informativo"
    assert conf == "high", "oportunidade não põe em dúvida insumo nosso"


def test_severidade_baixa_nao_rebaixa() -> None:
    _, conf = processar_sugestoes(
        [{"tipo": "contestacao_preco", "severidade": "baixa", "titulo": "x", "detalhe": "y"}],
        "high",
    )
    assert conf == "high"


def test_muitas_contestacoes_rebaixam_um_degrau_so() -> None:
    """Dez contestações não tornam a análise dez vezes menos confiável — e
    permitir isso daria ao agente um jeito de zerar a confiança repetindo a
    mesma queixa."""
    _, conf = processar_sugestoes(
        [
            {"tipo": "contestacao_preco", "severidade": "alta", "titulo": f"{i}", "detalhe": "y"}
            for i in range(10)
        ],
        "high",
    )
    assert conf == "medium"


def test_efeito_backend_enviado_pelo_agente_e_descartado() -> None:
    """Se ele pudesse preenchê-lo, poderia afirmar na tela que a confiança foi
    rebaixada quando não foi."""
    s, _ = processar_sugestoes(
        [
            {
                "tipo": "oportunidade",
                "severidade": "baixa",
                "titulo": "x",
                "detalhe": "y",
                "efeito_backend": "confianca_rebaixada",
            }
        ],
        "high",
    )
    assert s[0]["efeito_backend"] == "informativo"


def test_sem_sugestoes_a_confianca_e_a_do_backend() -> None:
    r = receber_analise_do_agente(_payload(), _agente())
    assert r.confianca_final == "medium"
    assert r.sugestoes == []


# ── sugestões que o próprio backend levantou ────────────────────────────


def _payload_com_alerta() -> dict:
    """Payload como sai de `build_migration_context` quando falta câmbio.

    A confiança em `premissas` já vem rebaixada de lá — este payload é o
    resultado, não a entrada.
    """
    p = _payload()
    p["premissas"]["confianca_analise"] = {
        "nivel": "low",
        "rebaixada_por": ["Sem taxa de câmbio para EUR/USD"],
    }
    p["premissas"]["sugestoes_backend"] = [
        {
            "tipo": "contestacao_preco",
            "severidade": "alta",
            "titulo": "Sem taxa de câmbio para EUR/USD",
            "detalhe": "valores somados sem conversão",
            "referencia": "EUR/USD",
            "origem": "backend",
            "efeito_backend": "confianca_rebaixada",
        }
    ]
    return p


def test_alerta_do_backend_chega_na_lista_de_tela() -> None:
    r = receber_analise_do_agente(_payload_com_alerta(), _agente())
    assert len(r.sugestoes) == 1
    assert r.sugestoes[0]["origem"] == "backend"
    assert r.resposta["sugestoes"] == r.sugestoes


def test_alerta_do_backend_nao_rebaixa_de_novo() -> None:
    """O rebaixamento dele já foi aplicado em `build_migration_context`. Passá-lo
    por `processar_sugestoes` contaria o mesmo problema duas vezes — e com dois
    alertas a análise cairia ao piso por um problema só."""
    r = receber_analise_do_agente(_payload_com_alerta(), _agente())
    assert r.confianca_final == "low", "já era low; não há degrau extra a cobrar"


def test_backend_e_agente_convivem_na_mesma_lista() -> None:
    r = receber_analise_do_agente(
        _payload_com_alerta(),
        _agente(),
        sugestoes=[
            {
                "tipo": "oportunidade",
                "severidade": "baixa",
                "titulo": "CUD de 1 ano",
                "detalhe": "z",
            }
        ],
    )
    assert [s["origem"] for s in r.sugestoes] == ["backend", "agente"]


def test_sugestao_do_agente_sai_marcada_como_do_agente() -> None:
    """Sem `origem`, a tela não distingue 'o modelo desconfia do nosso preço'
    de 'nós desconfiamos do nosso preço'."""
    r = receber_analise_do_agente(
        _payload(),
        _agente(),
        sugestoes=[
            {"tipo": "risco", "severidade": "media", "titulo": "x", "detalhe": "y"}
        ],
    )
    assert r.sugestoes[0]["origem"] == "agente"


def test_agente_nao_consegue_assinar_como_backend() -> None:
    """Mesma razão de `efeito_backend`: uma dúvida do modelo não pode aparecer
    na tela com o peso de uma verificação nossa."""
    s, _ = processar_sugestoes(
        [
            {
                "tipo": "risco",
                "severidade": "baixa",
                "titulo": "x",
                "detalhe": "y",
                "origem": "backend",
            }
        ],
        "high",
    )
    assert s[0]["origem"] == "agente"
