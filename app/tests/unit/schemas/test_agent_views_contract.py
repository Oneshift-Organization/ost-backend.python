"""Contrato de `agent_views` que a tela lê: o texto vem do modelo, o formato não.

A normalização nunca levanta erro (um campo estranho derrubaria o
`resilient_parse` da análise inteira) e roda também na leitura de análises antigas.
"""

from __future__ import annotations

import pytest

from app.schemas.analysis_v2 import AgentFlag, AnalysisV2Response, MigrationStep, RiskMigrationView


def test_alerta_em_texto_vira_estruturado_com_titulo_curto() -> None:
    flag = AgentFlag.model_validate(
        "Savings Plan ativo de $10.082/mês cobre pools C5A e C7A. "
        "Migração antecipada gera custo duplo."
    )
    assert flag.title == "Savings Plan ativo de $10.082/mês cobre pools C5A e C7A"
    assert flag.message.endswith("custo duplo.")
    assert flag.theme == "custo"


@pytest.mark.parametrize(
    ("code", "area", "theme"),
    [
        ("rds_engine_unknown", "database_migration", "banco"),
        ("waf_rules_migration", "security_migration", "seguranca"),
        ("arm_architecture_validation", "compute_compatibility", "computacao"),
        ("unclassified_spend_risk", "cost_coverage", "precisao"),
        ("savings_plan_exit_risk", "commitment_management", "custo"),
        ("x", "decisão de migração, 60% dos critérios sem informação", "outro"),
    ],
)
def test_tema_vem_de_lista_fechada(code: str, area: str, theme: str) -> None:
    assert (
        AgentFlag.model_validate({"code": code, "message": "m", "impact_area": area}).theme == theme
    )


def test_tema_invalido_do_modelo_cai_na_inferencia() -> None:
    flag = AgentFlag.model_validate({"message": "Custo de egresso", "theme": "dinheiro"})
    assert flag.theme == "custo"


def test_titulo_longo_e_cortado_na_palavra() -> None:
    flag = AgentFlag.model_validate({"message": "palavra " * 40})
    assert len(flag.title) <= 91
    assert flag.title.endswith("…")


def test_travessao_sai_de_todo_texto() -> None:
    view = RiskMigrationView.model_validate(
        {
            "summary": "Risco médio — banco",
            "risk_flags": [{"message": "RDS — sem engine"}],
            "blocked_or_uncertain_items": ["Translate \u2013 sem equivalente"],
        }
    )
    assert "—" not in view.summary
    assert view.risk_flags[0].title == "RDS, sem engine"
    assert view.blocked_or_uncertain_items == ["Translate, sem equivalente"]


@pytest.mark.parametrize(
    ("raw", "name", "duration", "detail"),
    [
        (
            "Wave 1 — Networking e Identidade (2-3 semanas): Provisionar VCN; configurar DNS",
            "Networking e Identidade",
            "2 a 3 semanas",
            "Provisionar VCN; configurar DNS",
        ),
        ("Onda 2: checkout, relatorios.", "Onda 2", None, "checkout, relatorios."),
        ("Texto livre sem formato", "Texto livre sem formato", None, ""),
        (
            {"name": "Banco", "duration": "4 semanas", "detail": "Replicar"},
            "Banco",
            "4 semanas",
            "Replicar",
        ),
    ],
)
def test_etapa_aceita_texto_ou_objeto(
    raw: object, name: str, duration: str | None, detail: str
) -> None:
    step = MigrationStep.model_validate(raw)
    assert (step.name, step.duration, step.detail) == (name, duration, detail)


def test_formato_estranho_nunca_derruba_a_analise() -> None:
    result = AnalysisV2Response.resilient_parse(
        {
            "recommendation_rationale": "mantida",
            "agent_views": {
                "finops": {"financial_flags": "texto solto", "key_findings": None, "summary": None},
                "cloud_engineering": {"technical_flags": [42, None, {"message": None}]},
                "risk_migration": {"recommended_migration_sequence": {"a": 1}, "risk_flags": [[]]},
            },
        }
    )
    assert result.recommendation_rationale == "mantida"
    assert result.agent_views.finops.financial_flags == []
    assert result.agent_views.risk_migration.recommended_migration_sequence == []
    assert all(f.theme for f in result.agent_views.cloud_engineering.technical_flags)
