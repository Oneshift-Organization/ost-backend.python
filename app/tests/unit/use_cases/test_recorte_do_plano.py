"""Do plano de migração ao recorte do Terraform do escopo (T5)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.entities.topologia import TipoDeRecurso
from app.domain.services.correspondencia_inventario import Correspondencia
from app.domain.services.cotacao_do_escopo import LinhaDoEscopo, cotar_escopo
from app.domain.services.topologia.escopo import Escolha, fechar_plano
from app.tests.unit.domain.topologia.test_escopo_de_migracao import _CUSTOS, _cenario
from app.use_cases.analysis.escopo_de_migracao import (
    ContextoDoEscopo,
    PlanoCotado,
    RecursoDoEscopo,
    recorte_do_plano,
)

D = Decimal


def _cotado(aplicacoes: set[str], incluir: set[str] | None = None) -> PlanoCotado:
    catalogo = _cenario()
    analise = catalogo.analise
    plano = fechar_plano(
        catalogo,
        Escolha(aplicacoes=frozenset(aplicacoes), incluir_grupos=frozenset(incluir or ())),
    )
    linhas = tuple(
        LinhaDoEscopo(
            servico=servico,
            custo=D(custo),
            tipo=TipoDeRecurso.INSTANCIA if chave.startswith("vm") else TipoDeRecurso.BANCO,
            familia="M7G" if chave.startswith("vm") else None,
            regiao="us-east-1",
            unidade=analise.unidade_de[chave],
            recurso=chave,
        )
        for chave, (servico, custo) in _CUSTOS.items()
    )
    recursos = {
        chave: RecursoDoEscopo(
            chave=chave,
            nome=chave,
            tipo=TipoDeRecurso.INSTANCIA if chave.startswith("vm") else TipoDeRecurso.BANCO,
            tipo_nativo="AWS::EC2::Instance" if chave.startswith("vm") else "AWS::RDS::DBInstance",
            regiao="us-east-1",
            custo=D(_CUSTOS[chave][1]),
            subredes=(f"subnet-{chave}",),
            familia="m7g" if chave.startswith("vm") else None,
            classe=None if chave.startswith("vm") else "db.t4g.small",
        )
        for chave in _CUSTOS
    }
    contexto = ContextoDoEscopo(
        catalogo=catalogo,
        linhas=linhas,
        pools=(),
        taxas_de_categoria={},
        provedores=("oci",),
        provedor_origem="aws",
        provedor_recomendado="oci",
        moeda="USD",
        inventario_id="inv-1",
        correspondencia=Correspondencia(
            status="ok",
            fracao_encontrada=D("1"),
            custo_com_recurso=D("815"),
            custo_encontrado=D("815"),
            contas_da_fatura=("111",),
            contas_do_inventario=("111",),
            contas_sem_inventario=(),
            mensagem="ok",
        ),
        premissas=(),
        recursos=recursos,
        periodo="2026-03",
        gasto_total=D("815"),
    )
    no_plano = [x for x in linhas if x.unidade in set(plano.unidades)]
    return PlanoCotado(
        plano=plano, cotacao=cotar_escopo(no_plano, (), {}, ("oci",)), contexto=contexto
    )


def test_maquinas_bancos_e_custos_so_do_escopo() -> None:
    # checkout leva o auth junto (usa o banco dele); catalogo vai antes, monitor fica
    recorte = recorte_do_plano(_cotado({"checkout"}))
    assert recorte.maquinas == {("subnet-vm-auth", "M7G"): 1, ("subnet-vm-checkout", "M7G"): 1}
    assert recorte.bancos == frozenset({"db-auth", "db-checkout"})
    assert recorte.bancos_por_classe == {"db.t4g.small": 2}
    # Para o pool sem posicionamento: 2 das 4 máquinas M7G da conta
    assert recorte.por_familia == {("us-east-1", "M7G"): (2, 4)}
    # Só o banco gerenciado das linhas do escopo: 400 + 150
    assert recorte.custos == {"banco_dados": D("550")}
    assert recorte.aplicacoes == ("auth", "checkout")
    assert [g.grupo for g in recorte.antes] and [g.grupo for g in recorte.para_tras]


def test_grupo_incluido_entra_no_recorte() -> None:
    base = _cotado({"checkout"})
    [antes] = base.plano.antes
    recorte = recorte_do_plano(_cotado({"checkout"}, {antes.grupo}))
    assert len(recorte.maquinas) == 3
    assert recorte.bancos_por_classe == {"db.t4g.small": 3}
    assert not recorte.antes
