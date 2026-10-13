"""Acoplamento por serviço da fatura (PLANO_LOCKIN_INVENTARIO, bloco A)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.services.acoplamento_por_servico import (
    AcoplamentoPorServico,
    PayloadSemServicosError,
    montar_acoplamento_por_servico,
)
from app.domain.services.decision_support import build_decision_support


def _workload(
    nome: str,
    custo: float,
    *,
    categoria: str = "database",
    tipo: str = "managed_relational_database",
    lock: str | None = None,
    alvos: list[dict[str, Any]] | None = None,
    inferido: bool = False,
    classe: str | None = None,
) -> dict[str, Any]:
    return {
        "name": nome,
        "category": categoria,
        "service_type": tipo,
        "cost": custo,
        "migration": {"lock_in": classe, "fonte": "classe"} if classe else {},
        "source_metadata": {"vendor_lock_in_level": lock} if lock else {},
        "type_source": "inferido" if inferido else "curado",
        "evidence_source": "catalog_snapshot",
        "targets": alvos or [],
    }


def _alvo(provedor: str, lock: str | None = None) -> dict[str, Any]:
    meta = {"vendor_lock_in_level": lock} if lock else {}
    return {"provider": provedor, "service_name": f"{provedor}-svc", "metadata": meta}


def _payload() -> dict[str, Any]:
    workloads = [
        _workload(
            "AmazonRDS",
            500,
            lock="medium",
            alvos=[_alvo("gcp", "high"), _alvo("azure"), _alvo("azure")],
        ),
        _workload(
            "AmazonS3",
            100,
            categoria="storage",
            tipo="object_storage",
            lock="low",
            alvos=[_alvo("gcp"), _alvo("oci")],
        ),
        _workload(
            "AmazonEKS",
            50,
            categoria="containers",
            tipo="managed_kubernetes",
            inferido=True,
            classe="low",
        ),
        _workload(
            "AmazonEC2",
            300,
            categoria="compute",
            tipo="virtual_server",
            lock="medium",
            alvos=[_alvo("gcp"), _alvo("azure"), _alvo("oci")],
        ),
        _workload("Tax", 40, categoria="tax", tipo="unknown"),
    ]
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "gasto_total": 990.0,
        "decision_workloads": workloads,
        "resumo_executivo": {
            "custo_mensal_atual": 990.0,
            "por_provedor": [
                {"provedor": p, "economia_mensal": 10, "cobertura": 0.9}
                for p in ("gcp", "azure", "oci")
            ],
        },
        "pools_computacao": [
            {
                "familia": "M7A",
                "regiao": "us-east-1",
                "custo_computacao": 80,
                "equivalentes": [{"provedor": "gcp"}],
            },
            {
                "familia": "M7G",
                "regiao": "global",
                "custo_computacao": 200,
                "custo_coberto_por_compromisso": True,
                "equivalentes": [
                    {"provedor": "gcp", "arquitetura_diferente": True},
                    {"provedor": "oci"},
                ],
            },
        ],
    }


def _por_nome(r: AcoplamentoPorServico) -> dict[str, Any]:
    return {s.nome: s for s in r.servicos}


def test_uma_linha_por_servico_e_imposto_fora() -> None:
    r = montar_acoplamento_por_servico(_payload())
    assert [s.nome for s in r.servicos] == ["AmazonRDS", "AmazonEC2", "AmazonS3", "AmazonEKS"]
    assert r.resumo.servicos == 4
    rec = r.reconciliacao
    assert (rec.soma_linhas, rec.fora_do_escopo, rec.diferenca, rec.status) == (
        Decimal("950"),
        Decimal("40"),
        Decimal("0"),
        "fecha",
    )
    assert sum(s.fracao for s in r.servicos if s.fracao) <= 1


def test_nivel_e_o_lado_da_origem_e_por_destino_e_o_do_score() -> None:
    rds = _por_nome(montar_acoplamento_por_servico(_payload()))["AmazonRDS"]
    assert (rds.nivel, rds.fonte) == ("medium", "catalogo")
    # gcp endurece; azure tem dois mapeamentos (ambíguo); oci não tem mapeamento.
    assert rds.por_destino == {"azure": None, "gcp": "high", "oci": None}


def test_por_destino_bate_com_a_evidencia_do_score() -> None:
    payload = _payload()
    visao = _por_nome(montar_acoplamento_por_servico(payload))
    decisao = build_decision_support(payload)
    comparados = 0
    for cenario in decisao.provider_scores:
        tecnico = next(a for a in cenario.axes if a.id == "technical")
        lock = next(f for f in tecnico.factors if f.id == "lock_in")
        for amostra in lock.evidence:
            assert visao[amostra["workload"]].por_destino[cenario.provider] == amostra["lock_in"]
            comparados += 1
    assert comparados == 3 * 4


def test_inferido_fica_a_confirmar_com_a_sugestao_da_classe() -> None:
    eks = _por_nome(montar_acoplamento_por_servico(_payload()))["AmazonEKS"]
    assert (eks.nivel, eks.fonte, eks.nivel_sugerido) == (None, None, "low")
    assert [s.fonte for s in eks.sugestoes] == ["classe"]
    assert eks.tipo_inferido and eks.alertas
    r = montar_acoplamento_por_servico(_payload())
    assert (r.resumo.a_confirmar, r.resumo.a_confirmar_com_sugestao) == (1, 1)
    assert (r.resumo.low, r.resumo.medium, r.resumo.high) == (1, 2, 0)


def test_pools_ficam_dentro_das_maquinas_virtuais() -> None:
    ec2 = _por_nome(montar_acoplamento_por_servico(_payload()))["AmazonEC2"]
    assert [p.familia for p in ec2.pools] == ["M7G", "M7A"]
    m7g, m7a = ec2.pools
    assert (m7g.regiao, m7g.arquitetura, m7g.coberto_por_compromisso) == (None, "arm", True)
    assert m7g.destinos_sem_mesma_arquitetura == ("gcp",)
    assert [s.fonte for s in m7g.sugestoes] == ["arquitetura"]
    assert (m7a.regiao, m7a.arquitetura, m7a.sugestoes) == ("us-east-1", "x86", ())
    # A sugestão sobe para o serviço, mas não muda o nível: médio já é médio.
    assert "arquitetura" in {s.fonte for s in ec2.sugestoes}
    assert (ec2.nivel, ec2.nivel_sugerido) == ("medium", None)


def test_sugestao_de_arquitetura_sobe_nivel_baixo() -> None:
    payload = _payload()
    payload["decision_workloads"][3]["source_metadata"] = {"vendor_lock_in_level": "low"}
    ec2 = _por_nome(montar_acoplamento_por_servico(payload))["AmazonEC2"]
    assert (ec2.nivel, ec2.nivel_sugerido) == ("low", "medium")


def test_sem_maquina_virtual_curada_os_pools_ficam_a_parte() -> None:
    payload = _payload()
    payload["decision_workloads"][3]["type_source"] = "inferido"
    r = montar_acoplamento_por_servico(payload)
    assert [p.familia for p in r.pools_sem_servico] == ["M7A", "M7G"]
    assert all(not s.pools for s in r.servicos)


def test_diferenca_de_escopo_aparece_sem_forcar_fechamento() -> None:
    payload = _payload()
    payload["gasto_total"] = 1200.0
    rec = montar_acoplamento_por_servico(payload).reconciliacao
    assert (rec.status, rec.diferenca) == ("diferenca_de_escopo", Decimal("210"))


def test_contrato_direto_usa_os_workloads_informados() -> None:
    payload = {
        "total_spend": 100,
        "workloads": [
            {
                "service_name": "Blob",
                "category": "storage",
                "source_usage": {"billed_cost": 100},
                "equivalents": [{"provider": "aws", "metadata": {}}],
            }
        ],
    }
    r = montar_acoplamento_por_servico(payload)
    assert [s.nome for s in r.servicos] == ["Blob"]
    assert r.servicos[0].por_destino == {"aws": None}
    assert r.reconciliacao.status == "fecha"


def test_sem_lista_de_servicos_recusa() -> None:
    with pytest.raises(PayloadSemServicosError):
        montar_acoplamento_por_servico({"pools_computacao": []})


def test_gasto_sem_categoria_aparece_fora_do_score() -> None:
    payload = _payload()
    payload["decision_workloads"].append(
        _workload(
            "DATABASE", 60, categoria="unknown", tipo="unknown", inferido=True, classe="medium"
        )
    )
    payload["gasto_total"] = 1050.0
    r = montar_acoplamento_por_servico(payload)
    db = _por_nome(r)["DATABASE"]
    assert (db.entra_no_score, db.nivel, dict(db.por_destino)) == (False, None, {})
    assert db.nivel_sugerido == "medium" and "fora do score" in db.alertas[0]
    assert r.resumo.fora_do_score == 1
    # Fora do escopo continua só o financeiro (o imposto de 40).
    assert (r.reconciliacao.fora_do_escopo, r.reconciliacao.status) == (Decimal("40"), "fecha")


def test_inventario_preenche_nivel_vazio_e_dependencia_sobe() -> None:
    payload = _payload()
    payload["topologia"] = {
        "detalhe": {
            "servicos": {
                "AmazonEKS": {
                    "nivel": "low",
                    "motivo": "sozinho",
                    "migra_junto_com": [],
                    "exclusividade": {
                        "nivel": "high",
                        "motivo": "API própria",
                        "fracao": 1.0,
                        "recursos": ["c1"],
                    },
                },
                "AmazonRDS": {
                    "nivel": "high",
                    "motivo": "Banco acessado por outras aplicações.",
                    "migra_junto_com": [{"servico": "AmazonEC2", "custo": 1, "natureza": "dados"}],
                    "exclusividade": {
                        "nivel": "low",
                        "motivo": "PostgreSQL",
                        "fracao": 1.0,
                        "recursos": ["db"],
                    },
                },
            }
        }
    }
    r = montar_acoplamento_por_servico(payload)
    servicos = _por_nome(r)
    eks, rds = servicos["AmazonEKS"], servicos["AmazonRDS"]
    assert (r.com_inventario, r.eixo_de_dependencia) == (True, True)
    # EKS: nível vazio; exclusividade alta do inventário vence a classe (baixa).
    assert eks.nivel_sugerido == "high"
    assert {s.fonte for s in eks.sugestoes} == {"classe", "inventario"}
    # RDS: catálogo médio; a dependência alta sobe, o motor aberto só informa.
    assert (rds.nivel, rds.nivel_sugerido) == ("medium", "high")
    assert [s.fonte for s in rds.sugestoes] == ["inventario", "dependencias"]
    assert r.resumo.migram_em_grupo == 1
    assert servicos["AmazonS3"].dependencias is None
