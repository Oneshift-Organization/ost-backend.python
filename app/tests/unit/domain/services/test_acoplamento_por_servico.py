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


def _alvo(provedor: str, lock: str | None = None, refatora: bool | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"vendor_lock_in_level": lock} if lock else {}
    if refatora is not None:
        meta["requires_refactoring"] = refatora
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


def test_v4_alternatives_match_score_without_choosing_service_or_changing_history() -> None:
    payload = _payload()
    workload = payload["decision_workloads"][0]
    for target in workload["targets"]:
        target["service_type"] = workload["service_type"]
    before = _por_nome(montar_acoplamento_por_servico(payload))["AmazonRDS"]
    assert before.por_destino["azure"] is None
    payload["decision_support"] = build_decision_support(payload).model_dump(mode="json")
    after = _por_nome(montar_acoplamento_por_servico(payload))["AmazonRDS"]
    assert after.por_destino["azure"] == "medium"
    # Ambíguo continua sem escolher serviço no score; na tela, os nomes (aqui, o mesmo)
    assert after.equivalente_por_destino["azure"] == "azure-svc"
    assert after.refatoracao_por_destino["azure"] is None


def test_adaptacao_e_equivalente_por_destino_sao_os_do_score() -> None:
    payload = _payload()
    ec2 = payload["decision_workloads"][3]
    ec2["source_metadata"]["requires_refactoring"] = False
    ec2["migration"] = {"classe_migracao": "replatform", "esforco": "medio"}
    ec2["targets"] = [_alvo("gcp", refatora=False), _alvo("azure", refatora=True), _alvo("oci")]
    visao = _por_nome(montar_acoplamento_por_servico(payload))
    vm = visao["AmazonEC2"]
    # "Não exige" só com os dois lados; um lado que exige basta; sem dado do destino, None.
    assert vm.refatoracao_por_destino == {"azure": True, "gcp": False, "oci": None}
    assert vm.equivalente_por_destino == {"azure": "azure-svc", "gcp": "gcp-svc", "oci": "oci-svc"}
    assert (vm.classe_migracao, vm.esforco) == ("replatform", "medio")
    # Ambíguo (dois no azure, com o mesmo nome) mostra o nome; ausente (oci) fica None.
    assert visao["AmazonRDS"].equivalente_por_destino == {
        "azure": "azure-svc",
        "gcp": "gcp-svc",
        "oci": None,
    }
    decisao = build_decision_support(payload)
    comparados = 0
    for cenario in decisao.provider_scores:
        tecnico = next(a for a in cenario.axes if a.id == "technical")
        fator = next(f for f in tecnico.factors if f.id == "refactoring")
        for amostra in fator.evidence:
            servico = visao[amostra["workload"]]
            assert (
                servico.refatoracao_por_destino[cenario.provider]
                == (amostra["requires_refactoring"])
            )
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


def test_inventario_preenche_nivel_vazio_e_migra_junto_nao_sobe() -> None:
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
    # RDS: catálogo médio. "Migra junto" é dependência entre serviços, não da nuvem:
    # não sobe o nível (lote v4, decisão de 14/10). O motor aberto não desce sem
    # cobertura. As duas evidências ficam listadas.
    assert (rds.nivel, rds.nivel_sugerido) == ("medium", None)
    assert [s.fonte for s in rds.sugestoes] == ["inventario", "dependencias"]
    assert r.resumo.migram_em_grupo == 1
    assert servicos["AmazonS3"].dependencias is None


def _com_rds(exclusividade: dict[str, Any], cobertura: float, **resumo: Any) -> dict[str, Any]:
    payload = _payload()
    payload["topologia"] = {
        "resumo": {"cobertura": resumo},
        "detalhe": {
            "servicos": {
                "AmazonRDS": {
                    "nivel": "low",
                    "motivo": "sozinho",
                    "cobertura": cobertura,
                    "exclusividade": exclusividade,
                }
            }
        },
    }
    return payload


_POSTGRES = {"nivel": "low", "motivo": "PostgreSQL: motor aberto.", "fracao": 1.0}


def test_motor_aberto_com_cobertura_alta_sugere_descer() -> None:
    rds = _por_nome(montar_acoplamento_por_servico(_com_rds(_POSTGRES, 0.95)))["AmazonRDS"]
    assert (rds.nivel, rds.nivel_sugerido) == ("medium", "low")


def test_inventario_estimado_nao_desce() -> None:
    payload = _com_rds(_POSTGRES, 0.95, custo_estimado=120.0)
    rds = _por_nome(montar_acoplamento_por_servico(payload))["AmazonRDS"]
    assert (rds.nivel, rds.nivel_sugerido) == ("medium", None)


def test_recurso_proprietario_sobe_mesmo_com_pouca_cobertura() -> None:
    dynamo = {"nivel": "high", "motivo": "DynamoDB: API própria.", "fracao": 0.1}
    rds = _por_nome(montar_acoplamento_por_servico(_com_rds(dynamo, 0.1)))["AmazonRDS"]
    assert (rds.nivel, rds.nivel_sugerido) == ("medium", "high")


def test_classe_mista_nao_sugere_e_pede_curadoria() -> None:
    payload = _payload()
    payload["decision_workloads"].append(
        {
            **_workload("Azure Database for PostgreSQL", 80),
            "migration": {
                "classe_migracao": "refactor",
                "fonte": "classe",
                "lock_in_faixa": ["medium", "high"],
            },
        }
    )
    pg = _por_nome(montar_acoplamento_por_servico(payload))["Azure Database for PostgreSQL"]
    assert (pg.nivel, pg.nivel_sugerido, pg.sugestoes) == (None, None, ())
    assert "de médio a alto" in pg.alertas[-1]


def test_varios_equivalentes_no_destino_mostram_as_opcoes_e_nao_nao_mapeado() -> None:
    """18/10: o RDS sem motor conhecido tinha três equivalentes na Azure e na OCI, e a tela
    dizia "Equivalente não mapeado" com o preço ao lado."""
    payload = _payload()
    rds = payload["decision_workloads"][0]
    rds["targets"] = [
        {"provider": "azure", "service_name": nome}
        for nome in (
            "Azure SQL Database",
            "Azure Database for MySQL",
            "Azure Database for PostgreSQL",
        )
    ] + [{"provider": "gcp", "service_name": "Cloud SQL"}]
    visao = _por_nome(montar_acoplamento_por_servico(payload))["AmazonRDS"]
    assert visao.equivalente_por_destino["azure"] == (
        "Azure SQL Database, Azure Database for MySQL ou Azure Database for PostgreSQL"
    )
    assert visao.equivalente_por_destino["gcp"] == "Cloud SQL"
    assert visao.equivalente_por_destino["oci"] is None


# ── opções de cada destino com preço (PLANO_EQUIVALENTES_E_TARIFAS §6) ──────


def _rds_com_opcoes(*, por_motor: bool = True, parte: float | None = None) -> dict[str, Any]:
    """O RDS da c31d09cc: US$ 44,12, motor fora da fatura, a OCI cotada em US$ 55,98."""
    payload = _payload()
    rds = payload["decision_workloads"][0]
    rds["cost"] = 44.12
    rds["targets"] = [
        {"provider": "oci", "service_name": nome}
        for nome in (
            "Oracle Database Cloud Service",
            "MySQL Database Service",
            "OCI Database with PostgreSQL",
        )
    ] + [{"provider": "gcp", "service_name": "Cloud SQL"}]
    cotacoes: list[dict[str, Any]] = [
        {"provedor": "OCI", "custo_mensal_estimado": 55.98},
        {"provedor": "GCP", "custo_mensal_estimado": 82.45},
    ]
    if por_motor:
        cotacoes[0]["por_motor"] = {"mysql": 41.81, "postgresql": 55.98}
        cotacoes[1]["por_motor"] = {"mysql": 82.45, "postgresql": 82.45}
    payload["custos_auxiliares"] = {"banco_dados": 44.12, "equivalentes_banco_dados": cotacoes}
    if parte is not None:
        payload["custos_auxiliares"]["por_servico"] = {"banco_dados": {"AmazonRDS": parte}}
    return payload


_PRECOS = {("AmazonRDS", "oci"): Decimal("55.98"), ("AmazonRDS", "gcp"): Decimal("82.45")}


def test_cada_opcao_do_banco_tem_o_preco_do_motor_dela() -> None:
    visao = _por_nome(montar_acoplamento_por_servico(_rds_com_opcoes(), _PRECOS))["AmazonRDS"]
    oci = {o.servico: o for o in visao.opcoes_por_destino["oci"]}
    pg = oci["OCI Database with PostgreSQL"]
    assert (pg.motor, pg.custo_estimado, pg.cotacao, pg.no_total) == (
        "postgresql",
        Decimal("55.98"),
        "motor",
        True,
    )
    mysql = oci["MySQL Database Service"]
    assert (mysql.custo_estimado, mysql.no_total) == (Decimal("41.81"), False)
    oracle = oci["Oracle Database Cloud Service"]
    assert (oracle.motor, oracle.custo_estimado, oracle.cotacao) == ("oracle", None, "sem_cotacao")
    assert oracle.motivo and "Oracle" in oracle.motivo


def test_servico_dos_dois_motores_vira_uma_opcao_por_motor() -> None:
    visao = _por_nome(montar_acoplamento_por_servico(_rds_com_opcoes(), _PRECOS))["AmazonRDS"]
    gcp = visao.opcoes_por_destino["gcp"]
    assert [(o.servico, o.motor, o.custo_estimado) for o in gcp] == [
        ("Cloud SQL", "postgresql", Decimal("82.45")),
        ("Cloud SQL", "mysql", Decimal("82.45")),
    ]


def test_so_a_parte_do_servico_no_banco_muda_com_o_motor() -> None:
    """RDS de US$ 44,12 com US$ 22,06 no banco: na OCI, metade da diferença do motor."""
    payload = _rds_com_opcoes(parte=22.06)
    visao = _por_nome(montar_acoplamento_por_servico(payload, _PRECOS))["AmazonRDS"]
    mysql = next(o for o in visao.opcoes_por_destino["oci"] if o.motor == "mysql")
    assert mysql.custo_estimado == Decimal("48.90")  # 55,98 + 0,5 x (41,81 - 55,98)


def test_analise_antiga_sem_cotacao_por_motor_leva_o_preco_do_servico() -> None:
    payload = _rds_com_opcoes(por_motor=False)
    visao = _por_nome(montar_acoplamento_por_servico(payload, _PRECOS))["AmazonRDS"]
    oci = visao.opcoes_por_destino["oci"]
    assert {o.custo_estimado for o in oci} == {Decimal("55.98")}
    assert {o.cotacao for o in oci} == {"servico"}
    assert not any(o.no_total for o in oci)
    # Uma opção só no destino: o preço dela é o que entra no total
    assert [o.no_total for o in visao.opcoes_por_destino["gcp"]] == [True]


def test_fora_do_banco_as_opcoes_levam_o_preco_do_servico() -> None:
    payload = _payload()
    s3 = payload["decision_workloads"][1]
    s3["targets"] = [{"provider": "gcp", "service_name": n} for n in ("Cloud Storage", "Filestore")]
    precos = {("AmazonS3", "gcp"): Decimal("90")}
    visao = _por_nome(montar_acoplamento_por_servico(payload, precos))["AmazonS3"]
    assert [(o.motor, o.custo_estimado, o.cotacao) for o in visao.opcoes_por_destino["gcp"]] == [
        (None, Decimal("90"), "servico"),
        (None, Decimal("90"), "servico"),
    ]


def test_sem_preco_do_servico_as_opcoes_saem_sem_cotacao() -> None:
    visao = _por_nome(montar_acoplamento_por_servico(_rds_com_opcoes()))["AmazonRDS"]
    assert {o.cotacao for o in visao.opcoes_por_destino["oci"]} == {"sem_cotacao"}
    assert {o.custo_estimado for o in visao.opcoes_por_destino["oci"]} == {None}


def test_com_o_motor_conhecido_o_servico_dos_dois_motores_abre_so_nele() -> None:
    """O inventário disse PostgreSQL: a OCI chega só com o PostgreSQL, e o Cloud SQL não
    pode reabrir o MySQL."""
    payload = _rds_com_opcoes()
    rds = payload["decision_workloads"][0]
    rds["targets"] = [
        {"provider": "oci", "service_name": "OCI Database with PostgreSQL"},
        {"provider": "gcp", "service_name": "Cloud SQL"},
    ]
    visao = _por_nome(montar_acoplamento_por_servico(payload, _PRECOS))["AmazonRDS"]
    assert [(o.servico, o.motor) for o in visao.opcoes_por_destino["gcp"]] == [
        ("Cloud SQL", "postgresql")
    ]


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Azure Database for PostgreSQL", "postgresql"),
        ("SQL Database", "sqlserver"),
        ("AUTONOMOUS_DATABASE", "oracle"),
        ("AmazonRDS", None),
    ],
)
def test_motor_da_origem_pelo_nome_do_banco(nome: str, esperado: str | None) -> None:
    payload = _rds_com_opcoes()
    payload["decision_workloads"][0]["name"] = nome
    visao = _por_nome(montar_acoplamento_por_servico(payload))[nome]
    assert visao.motor_da_origem == esperado


def test_fora_do_banco_nao_tem_motor_da_origem() -> None:
    visao = _por_nome(montar_acoplamento_por_servico(_payload()))["AmazonS3"]
    assert visao.motor_da_origem is None
