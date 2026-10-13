"""Dependências por serviço da fatura (PLANO_LOCKIN_INVENTARIO B1 e B2)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.services.correlacao_inventario import CustoDeRecurso, CustosDoBilling
from app.domain.services.topologia import analisar_topologia
from app.domain.services.topologia.servicos_do_billing import dependencias_por_servico

from .construtores import camadas_app_banco, inventario, regra


def _custos(*linhas: tuple[str, str, str]) -> CustosDoBilling:
    """(resource_id, serviço, custo)."""
    return CustosDoBilling(
        periodo="2026-03",
        moeda="USD",
        custo_da_base=sum((Decimal(c) for _, _, c in linhas), Decimal(0)),
        por_recurso=tuple(
            CustoDeRecurso(resource_id=r, conta="c1", regiao="r1", servico=s, custo=Decimal(c))
            for r, s, c in linhas
        ),
    )


def test_maquina_e_banco_da_mesma_aplicacao_migram_juntos() -> None:
    rec, rels = camadas_app_banco("loja")
    inv = inventario(rec, rels, [regra("sg-loja-dados", 5432, de_grupo="sg-loja-app")])
    r = dependencias_por_servico(
        inv,
        analisar_topologia(inv),
        _custos(("vm-loja", "EC2", "100"), ("db-loja", "RDS", "300"), ("i-fora", "EC2", "50")),
    )
    ec2, rds = r["EC2"], r["RDS"]
    assert (rds.nivel, [v.servico for v in rds.migra_junto_com]) == ("medium", ["EC2"])
    assert rds.migra_junto_com[0].natureza == "mesma_unidade"
    assert rds.custo_do_grupo == Decimal("400")
    assert ec2.migra_junto_com[0].servico == "RDS"
    # Recurso sem inventário: entra no custo, não na cobertura.
    assert (ec2.custo, ec2.custo_no_inventario, ec2.cobertura) == (
        Decimal("150"),
        Decimal("100"),
        Decimal("0.6667"),
    )
    assert ec2.regioes == ("r1",)
    # Grupo sem dependência fica entre os independentes da topologia, fora das ondas.
    assert (rds.grupos, rds.ondas) == (("loja",), ())


def test_banco_acessado_por_outra_aplicacao_e_alto() -> None:
    rec_a, rels_a = camadas_app_banco("auth")
    rec_c, rels_c = camadas_app_banco("checkout")
    inv = inventario(
        [*rec_a, *rec_c],
        [*rels_a, *rels_c],
        [
            regra("sg-auth-dados", 5432, de_grupo="sg-auth-app"),
            regra("sg-auth-dados", 5432, de_grupo="sg-checkout-app"),
        ],
    )
    r = dependencias_por_servico(
        inv,
        analisar_topologia(inv),
        _custos(
            ("vm-auth", "AuthVM", "10"),
            ("db-auth", "AuthDB", "40"),
            ("vm-checkout", "CheckoutVM", "20"),
        ),
    )
    assert (r["AuthDB"].nivel, r["AuthDB"].banco_compartilhado) == ("high", True)
    assert "outras aplicações" in r["AuthDB"].motivo
    assert r["AuthDB"].dependentes >= 1


def test_dependencia_so_de_ordem_fica_baixa() -> None:
    rec_a, rels_a = camadas_app_banco("front", tags=False)
    rec_b, rels_b = camadas_app_banco("api", tags=False)
    inv = inventario(
        [*rec_a, *rec_b],
        [*rels_a, *rels_b],
        [regra("sg-api-app", 8080, de_grupo="sg-front-app")],
    )
    r = dependencias_por_servico(
        inv,
        analisar_topologia(inv),
        _custos(("vm-front", "Front", "10"), ("vm-api", "Api", "30")),
    )
    front, api = r["Front"], r["Api"]
    assert (front.nivel, front.migra_junto_com) == ("low", ())
    assert [(v.servico, v.natureza) for v in front.depende_de] == [("Api", "servico")]
    assert "depois de Api" in front.motivo
    assert (api.nivel, api.dependentes) == ("low", 1)


def test_servico_sem_recurso_no_inventario_fica_de_fora() -> None:
    rec, rels = camadas_app_banco("loja")
    inv = inventario(rec, rels)
    r = dependencias_por_servico(inv, analisar_topologia(inv), _custos(("x-1", "S3", "9")))
    assert r == {}
