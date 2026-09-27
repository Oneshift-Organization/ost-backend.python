"""A topologia no payload da análise: compactação e ordem imposta (I7)."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from app.domain.services.correlacao_inventario import IndiceDeApelidos
from app.domain.services.topologia.indice import Indice
from app.schemas.analysis_v2 import AgentViews, AnalysisV2Response, RiskMigrationView
from app.tests.unit.domain.topologia.construtores import (
    R,
    inventario,
    protege,
    rel,
    sg,
    subrede,
    vm,
)
from app.use_cases.analysis.decision_support import impose_decision
from app.use_cases.analysis.topologia_no_payload import (
    VERSAO,
    _posicionamento_dos_pools,
    compactar_topologia,
    impor_topologia,
    sequencia_de_migracao,
)


def _topologia() -> dict:
    return {
        "versao": VERSAO,
        "inventario_id": "i-1",
        "fingerprint": "f" * 64,
        "declaracoes": {"ferramentas": ["aplicacao:monitoramento"]},
        "resumo": {
            "contagens": {
                "fundacao": {"rede": 2, "subrede": 6, "grupo_seguranca": 9, "gateway": 1}
            },
            "ondas": [
                {"numero": 1, "grupos": ["auth", "catalogo"], "total": 2},
                {"numero": 2, "grupos": ["checkout"], "total": 14},
            ],
            "independentes": {"total": 3, "exemplos": ["relatorios", "busca", "bucket"]},
            "ferramentas": ["bastion"],
            "riscos": [
                {
                    "regra": "banco_exposto",
                    "severidade": "alta",
                    "titulo": "Porta de banco aberta para a internet",
                    "total": 3,
                },
                {
                    "regra": "ponto_unico_de_falha",
                    "severidade": "media",
                    "titulo": "Banco sem alta disponibilidade",
                    "total": 1,
                },
                {
                    "regra": "grupo_interno",
                    "severidade": "informativa",
                    "titulo": "Grupos que liberam os próprios membros",
                    "total": 5,
                },
            ],
        },
        "detalhe": {"rede": {"subredes": [{"cidr": "10.0.1.0/24"}]}, "posicionamento": []},
    }


def test_compactar_tira_o_detalhe_e_as_declaracoes() -> None:
    payload = {"moeda": "USD", "topologia": _topologia()}
    compacto = compactar_topologia(payload)
    assert "detalhe" not in compacto["topologia"]
    assert "declaracoes" not in compacto["topologia"]
    assert compacto["topologia"]["resumo"] == payload["topologia"]["resumo"]
    # O original não é tocado: é dele que o Terraform lê.
    assert "detalhe" in payload["topologia"]
    assert compactar_topologia({"moeda": "USD"}) == {"moeda": "USD"}


def test_sequencia_de_migracao() -> None:
    seq = sequencia_de_migracao(_topologia())
    assert seq.passos[0].startswith("Onda 0 — fundação: 2 rede(s), 6 subrede(s)")
    assert seq.passos[1] == "Onda 1: auth, catalogo."
    assert seq.passos[2] == "Onda 2: checkout e mais 13."
    assert seq.passos[3].startswith("Sem dependência (3)")
    assert seq.passos[4].startswith("Ferramentas de administração")
    # Só alta e média viram flag; informativo fica na topologia.
    assert seq.riscos == [
        "[alta] Porta de banco aberta para a internet (3)",
        "[media] Banco sem alta disponibilidade (1)",
    ]


def test_impor_substitui_a_sequencia_do_modelo_e_soma_os_riscos() -> None:
    resposta = AnalysisV2Response(
        agent_views=AgentViews(
            risk_migration=RiskMigrationView(
                recommended_migration_sequence=["Migre tudo no sábado"],
                risk_flags=["[alta] Porta de banco aberta para a internet (3)", "do modelo"],
            )
        )
    )
    imposta = impor_topologia(resposta, {"topologia": _topologia()})
    risco = imposta.agent_views.risk_migration
    assert risco.recommended_migration_sequence[0].startswith("Onda 0")
    assert "Migre tudo no sábado" not in risco.recommended_migration_sequence
    assert risco.risk_flags == [
        "[alta] Porta de banco aberta para a internet (3)",
        "do modelo",
        "[media] Banco sem alta disponibilidade (1)",
    ]


def test_sem_topologia_ou_de_outra_versao_nada_muda() -> None:
    resposta = AnalysisV2Response()
    assert impor_topologia(resposta, {}) == resposta
    outra = _topologia()
    outra["versao"] = "outra"
    assert impor_topologia(resposta, {"topologia": outra}) == resposta


def test_impose_decision_aplica_a_ordem_tambem() -> None:
    """O funil comum ao LLM e ao agente: a ordem vem junto com o score."""
    payload = {
        "provedor_origem": "aws",
        "moeda": "USD",
        "resumo_executivo": {"custo_mensal_atual": 100, "por_provedor": []},
        "premissas": {},
        "decision_workloads": [],
        "topologia": _topologia(),
    }
    resposta = impose_decision(AnalysisV2Response(), deepcopy(payload))
    assert resposta.agent_views.risk_migration.recommended_migration_sequence[0].startswith(
        "Onda 0"
    )


def test_posicionamento_agrupa_por_subrede_e_soma_os_grupos() -> None:
    """A máquina consolidada tem de aceitar o que qualquer instância da subrede
    aceitava; ``conjuntos_de_grupos`` avisa que posturas diferentes se juntaram."""
    maquinas = [vm(f"vm{i}", vcpus=2, tipo_maquina="m5.large") for i in range(1, 5)]
    conteudo = inventario(
        [*maquinas, subrede("s1", "10.0.1.0/24"), subrede("s2", "10.0.2.0/24"), sg("a"), sg("b")],
        [
            *(rel(f"vm{i}", "s1", R.CONTIDO_EM) for i in (1, 2, 3)),
            rel("vm4", "s2", R.CONTIDO_EM),
            *protege("a", "vm1", "vm2", "vm3", "vm4"),
            *protege("b", "vm3"),
        ],
    )
    pool = SimpleNamespace(
        pool_key=SimpleNamespace(machine_family="M5", region="us-east-1"),
        inventario=SimpleNamespace(recursos=frozenset({"vm1", "vm2", "vm3", "vm4", "i-fora"})),
    )
    [saida] = _posicionamento_dos_pools(
        SimpleNamespace(family_pools=[pool]), Indice(conteudo), IndiceDeApelidos(conteudo)
    )
    assert saida["sem_posicionamento"] == 1  # i-fora não está no inventário
    assert [
        (p["subrede"], p["grupos"], p["conjuntos_de_grupos"], p["instancias"], p["vcpus"])
        for p in saida["posicionamentos"]
    ] == [("s1", ["a", "b"], 2, 3, 6.0), ("s2", ["a"], 1, 1, 2.0)]
    assert saida["posicionamentos"][0]["tipos"] == {"m5.large": 3}
