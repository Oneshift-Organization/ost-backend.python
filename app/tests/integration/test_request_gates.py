"""Gates do request enviado ao agente — pipeline inteira sobre CSVs reais.

Cada teste aqui trava um invariante que uma onda do Precision Engine
estabeleceu. Antes disto, toda validação vivia em scripts de scratchpad: uma
refatoração podia fazer o request voltar a dizer "migre seu S3 para Persistent
Disk" e ninguém perceberia até a IA gerar o relatório errado.

Rodam contra Postgres real com seeds; pulam graciosamente sem banco (conftest).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.analysis_v2 import (
    AnalysisV2Response,
    ProviderComparison,
    SavingsSummaryV2,
)
from app.use_cases.analysis.backend_authority import apply_backend_authority
from app.use_cases.analysis.run_analysis_pipeline import (
    build_pipeline_llm_input_payload,
)

# ── Onda 1 — conservação e segregação de compromisso ────────────────────


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_conservacao_de_custo(ctx_name: str, request: pytest.FixtureRequest) -> None:
    """Nada some: soma dos baldes == gasto total (o invariante mais duro)."""
    ctx = request.getfixturevalue(ctx_name)
    rec = ctx.premissas["reconciliacao"]
    assert rec["conservacao_custo"]["bate"] is True, rec["conservacao_custo"]


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_reconciliacao_sem_violacoes(
    ctx_name: str, request: pytest.FixtureRequest
) -> None:
    ctx = request.getfixturevalue(ctx_name)
    rec = ctx.premissas["reconciliacao"]
    violacoes = [a for a in rec["achados"] if a["severidade"] == "violacao"]
    assert violacoes == [], violacoes


def test_compromisso_nao_infla_pool(aws_context) -> None:
    """Savings Plan tem custo sem capacidade: vai para `commitment`, não para
    o pool — senão o preço/vCPU explode (Onda 1)."""
    assert aws_context.auxiliary.commitment > 0
    for fp in aws_context.family_pools:
        if fp.capacity.vcpus <= 0:
            continue
        preco_vcpu = fp.monthly_cost / fp.capacity.vcpus
        assert preco_vcpu < Decimal("120"), f"{fp.pool_key.as_str()}: {preco_vcpu}"


# ── Onda 2 — instance-hours expandidos ──────────────────────────────────


def test_instance_hours_viram_vcpu_e_ram(aws_context) -> None:
    """BoxUsage é hora-de-instância, não hora-de-vCPU. Sem a expansão o pool
    M7G aparecia com ~27 vCPU em vez de ~224 (8x subdimensionado)."""
    m7g = next(
        (fp for fp in aws_context.family_pools if fp.pool_key.machine_family == "M7G"),
        None,
    )
    assert m7g is not None, "família M7G deveria existir (extração estrutural)"
    assert m7g.capacity.vcpus > Decimal("200")
    assert m7g.capacity.ram_gib > Decimal("800"), "RAM não pode ficar zerada"


def test_familias_reais_nao_colapsam_em_generic(aws_context) -> None:
    """Antes da extração estrutural, toda a AWS virava um pool `general`."""
    familias = {fp.pool_key.machine_family for fp in aws_context.family_pools}
    assert "general" not in familias
    assert len(familias) > 10, familias


# ── Onda 3 — preços oficiais ────────────────────────────────────────────


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_confianca_de_preco_por_provedor(
    ctx_name: str, request: pytest.FixtureRequest
) -> None:
    """A confiança é declarada POR PROVEDOR, não um 'low' cego (Onda 3).

    Não exigimos `official` aqui de propósito: isso dependeria de ter rodado o
    fetch de preços (rede), e um gate que depende de API externa é frágil. O
    invariante que importa e vale em qualquer ambiente é que cada provedor
    declara sua própria confiança, com valor do vocabulário conhecido.
    """
    ctx = request.getfixturevalue(ctx_name)
    conf = ctx.premissas["confianca_precos"]
    assert isinstance(conf, dict), f"confiança deveria ser por provedor: {conf}"
    assert conf, "nenhum provedor avaliado"
    assert set(conf.values()) <= {"official", "reference", "low"}, conf


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_equivalente_com_preco_oficial_e_rotulado(
    ctx_name: str, request: pytest.FixtureRequest
) -> None:
    """Quando há price point oficial, o equivalente carrega `official` — e o
    custo estimado é positivo. Se o fetch não rodou, não há nada a checar."""
    ctx = request.getfixturevalue(ctx_name)
    oficiais = [
        e
        for equivs in ctx.family_equivalents.values()
        for e in equivs
        if e.financials and e.financials.pricing_confidence == "official"
    ]
    if not oficiais:
        pytest.skip("nenhum preço oficial carregado neste ambiente")
    for e in oficiais:
        assert e.financials.estimated_monthly_cost > 0
        assert e.technical_match is not None


# ── Onda 4 — cobertura de análise ───────────────────────────────────────


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_cobertura_de_analise(ctx_name: str, request: pytest.FixtureRequest) -> None:
    """Storage/egress/database deixaram de ser baldes opacos."""
    ctx = request.getfixturevalue(ctx_name)
    assert ctx.premissas["cobertura_analise"] >= 0.70


# ── Onda 5 — recomendação determinística ────────────────────────────────


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_resumo_executivo_calculado_no_backend(
    ctx_name: str, request: pytest.FixtureRequest
) -> None:
    ctx = request.getfixturevalue(ctx_name)
    r = ctx.resumo_executivo
    assert r["por_provedor"], "roll-up por provedor vazio"
    for p in r["por_provedor"]:
        # economia = -(delta): consistência aritmética do roll-up
        assert p["economia_mensal"] == pytest.approx(-p["delta_vs_atual"], abs=0.01)
        assert 0.0 <= p["cobertura"] <= 1.0


# ── Onda 6 — DE-PARA correto por serviço nomeado ────────────────────────


def test_servicos_nomeados_no_payload(aws_context) -> None:
    """A IA precisa saber QUAIS serviços o cliente usa, não só baldes."""
    payload, _ = build_pipeline_llm_input_payload(aws_context)
    nomes = {s["nome"] for s in payload["servicos"]}
    assert {"AmazonRDS", "AmazonS3", "AmazonEC2"} <= nomes, sorted(nomes)[:10]


def test_object_storage_nao_mapeia_para_block_storage(aws_context) -> None:
    """Regressão do bug factual: S3 apontava para Persistent Disk / Managed
    Disks porque o casamento por nome falhava (`AmazonS3` vs `Amazon S3`)."""
    s3 = _find_service(aws_context, "AmazonS3")
    assert s3.service_type == "object_storage", s3.service_type
    alvos = {e.service_name.lower() for e in s3.equivalents}
    assert not any("persistent disk" in a or "managed disk" in a for a in alvos), alvos
    assert any("object storage" in a or "blob" in a or "cloud storage" in a
               for a in alvos), alvos


def test_banco_gerenciado_tem_equivalente(aws_context) -> None:
    """RDS é o 2º maior custo do bill; sem DE-PARA a IA o ignorava."""
    rds = _find_service(aws_context, "AmazonRDS")
    assert rds.service_type == "managed_relational_database"
    assert rds.equivalents, "RDS sem equivalente cross-cloud"


def test_instrumento_financeiro_nao_recebe_depara(aws_context) -> None:
    """Savings Plan não é recurso migrável: 'migre seu Savings Plan para
    Compute Engine' seria conselho sem sentido."""
    sp = _find_service(aws_context, "Savings Plans for AWS Compute usage")
    assert sp.equivalents == []
    assert sp.migration_expectation is None


def test_service_type_bate_com_catalogo_curado(aws_context) -> None:
    """O tipo usado não pode divergir da verdade curada — divergência significa
    que o casamento falhou e o tipo veio do palpite do classificador."""
    esperado = {
        "AmazonEC2": "virtual_server",
        "AmazonRDS": "managed_relational_database",
        "AmazonS3": "object_storage",
        "AmazonElastiCache": "in_memory_database",
    }
    for nome, tipo in esperado.items():
        assert _find_service(aws_context, nome).service_type == tipo, nome


# ── E3 — expectativa de migração ────────────────────────────────────────


def test_expectativa_de_migracao_no_payload(aws_context) -> None:
    """Classe 6R calculada no backend; sem isso a IA inventaria a dificuldade."""
    payload, _ = build_pipeline_llm_input_payload(aws_context)
    com_migracao = [s for s in payload["servicos"] if "migracao" in s]
    assert com_migracao, "nenhum serviço com expectativa de migração"
    for s in com_migracao:
        assert s["migracao"]["classe_migracao"] in {"rehost", "replatform", "refactor"}
        assert s["migracao"]["esforco"] in {"baixo", "medio", "alto"}


def test_classe_de_migracao_reflete_dificuldade_real(aws_context) -> None:
    """Object storage é o caso fácil; banco gerenciado exige reengenharia."""
    assert _find_service(aws_context, "AmazonS3").migration_expectation[
        "classe_migracao"
    ] == "rehost"
    assert _find_service(aws_context, "AmazonRDS").migration_expectation[
        "classe_migracao"
    ] == "refactor"


# ── Onda 7 — contrato com o LLM ─────────────────────────────────────────


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_payload_completo_chega_ao_modelo(
    ctx_name: str, request: pytest.FixtureRequest
) -> None:
    """O orchestrator já descartou `servicos`/`premissas`/`resumo_executivo`
    remontando o request a partir do `pools_json`."""
    ctx = request.getfixturevalue(ctx_name)
    payload, _ = build_pipeline_llm_input_payload(ctx)
    assert {"servicos", "premissas", "resumo_executivo", "pools_computacao"} <= set(
        payload
    )


@pytest.mark.parametrize("ctx_name", ["aws_context", "gcp_context"])
def test_payload_em_moeda_unica(ctx_name: str, request: pytest.FixtureRequest) -> None:
    """O bill GCP já foi enviado com gasto_total em BRL e pools em USD."""
    ctx = request.getfixturevalue(ctx_name)
    payload, _ = build_pipeline_llm_input_payload(ctx)
    assert payload["moeda"] == ctx.premissas["moeda_analise"] == "USD"
    total_pools = sum(p["custo_computacao"] for p in payload["pools_computacao"])
    assert total_pools <= payload["gasto_total"] * 1.01


def test_backend_sobrescreve_numeros_do_modelo(aws_context) -> None:
    """A IA narra; o número é do backend. Sem isto a resposta da API poderia
    contradizer a própria reconciliação."""
    alucinado = AnalysisV2Response(
        recommended_provider="AWS",
        provider_comparison=[
            ProviderComparison(
                provider="oci",
                estimated_monthly_cost=1.0,
                estimated_monthly_savings=999999.0,
            )
        ],
        savings_summary=SavingsSummaryV2(total_monthly_savings_usd=999999.0),
    )
    out = apply_backend_authority(alucinado, aws_context.resumo_executivo)

    verdade = {
        p["provedor"].lower(): p for p in aws_context.resumo_executivo["por_provedor"]
    }
    for pc in out.provider_comparison:
        t = verdade[pc.provider.lower()]
        assert pc.estimated_monthly_cost == pytest.approx(t["custo_estimado_total"])
        assert pc.estimated_monthly_savings == pytest.approx(t["economia_mensal"])
    assert out.savings_summary.total_monthly_savings_usd != 999999.0


# ── helper ──────────────────────────────────────────────────────────────


def _find_service(ctx, nome: str):
    for cat in ctx.categories:
        for sv in cat.services:
            if sv.service_name == nome:
                return sv
    raise AssertionError(f"serviço {nome!r} não encontrado no contexto")
