"""O inventário é da mesma conta da fatura? (PLANO_ESCOPO_DE_MIGRACAO, E4)"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.domain.entities.topologia import Inventario
from app.domain.services.correlacao_inventario import (
    CustoDeRecurso,
    CustosDoBilling,
    correlacionar,
)
from app.domain.services.correspondencia_inventario import (
    OK,
    PARCIAL,
    SEM_CORRESPONDENCIA,
    SEM_ID_DE_RECURSO,
    Correspondencia,
    avaliar_correspondencia,
)

from .construtores import banco, inventario, vm


def _custos(*linhas: tuple[str, str, str]) -> CustosDoBilling:
    """(resource_id, conta, custo)."""
    return CustosDoBilling(
        periodo="2026-03",
        moeda="USD",
        custo_da_base=sum((Decimal(c) for _, _, c in linhas), Decimal("50")),
        por_recurso=tuple(
            CustoDeRecurso(resource_id=r, conta=conta, regiao="r1", servico="EC2", custo=Decimal(c))
            for r, conta, c in linhas
        ),
    )


def _avaliar(inv: Inventario, custos: CustosDoBilling) -> Correspondencia:
    return avaliar_correspondencia(inv, custos, correlacionar(inv, custos))


def test_mesma_conta_e_os_recursos_casam() -> None:
    inv = inventario([vm("i-1", conta="111"), banco("db-1", conta="111")])
    r = _avaliar(inv, _custos(("i-1", "111", "100"), ("db-1", "111", "300")))
    assert (r.status, r.liga_custo, r.fracao_encontrada) == (OK, True, Decimal(1))


def test_fatura_resumida_nao_tem_como_ligar_recurso_a_servico() -> None:
    inv = inventario([vm("i-1", conta="111")])
    r = _avaliar(inv, _custos())
    assert (r.status, r.liga_custo) == (SEM_ID_DE_RECURSO, False)
    assert "fatura detalhada" in r.mensagem


def test_inventario_de_outra_conta_nao_corresponde_e_diz_as_contas() -> None:
    inv = inventario([vm("i-9", conta="999")])
    r = _avaliar(inv, _custos(("i-1", "111", "100")))
    assert r.status == SEM_CORRESPONDENCIA
    assert (r.contas_da_fatura, r.contas_do_inventario) == (("111",), ("999",))
    assert "111" in r.mensagem and "999" in r.mensagem


def test_nome_coincidente_em_outra_conta_nao_vale_como_o_mesmo_recurso() -> None:
    # Um bucket com o mesmo nome em outra conta casa pelo nome, mas as contas não têm nada em
    # comum: é coincidência, não o mesmo recurso
    inv = inventario([vm("dados-app", conta="999")])
    r = _avaliar(inv, _custos(("dados-app", "111", "10"), ("i-1", "111", "90")))
    assert r.status == SEM_CORRESPONDENCIA


def test_no_gcp_conta_nao_e_comparavel_e_vale_o_que_casou() -> None:
    # A fatura do GCP traz a conta de faturamento; o inventário, o projeto
    inv = replace(inventario([vm("vm-1", conta="projeto-a")]), provedor="gcp")
    r = _avaliar(inv, _custos(("vm-1", "01ABCD-EFGH", "100")))
    assert r.status == OK


def test_inventario_que_cobre_so_parte_diz_a_conta_que_falta() -> None:
    inv = inventario([vm("i-1", conta="111")])
    r = _avaliar(inv, _custos(("i-1", "111", "30"), ("i-2", "222", "70")))
    assert (r.status, r.liga_custo) == (PARCIAL, True)
    assert r.contas_sem_inventario == ("222",)
    assert "222" in r.mensagem
