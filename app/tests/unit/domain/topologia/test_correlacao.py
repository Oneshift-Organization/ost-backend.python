"""Correlação entre billing e inventário (RI13), casos pontuais."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.entities.topologia import EstadoDoRecurso
from app.domain.services.correlacao_inventario import (
    CustoDeRecurso,
    CustosDoBilling,
    correlacionar,
)
from app.tests.unit.domain.topologia.construtores import R, T, inventario, recurso, rel, vm


def _custos(
    *linhas: tuple[str, str, str | None, str, str], periodo: str = "2026-03"
) -> CustosDoBilling:
    por_recurso = tuple(
        CustoDeRecurso(
            resource_id=rid, conta=conta, regiao=regiao, servico=servico, custo=Decimal(custo)
        )
        for rid, conta, regiao, servico, custo in linhas
    )
    total = sum((x.custo for x in por_recurso), Decimal("0")) + Decimal("100")  # sem recurso
    return CustosDoBilling(
        periodo=periodo, moeda="USD", custo_da_base=total, por_recurso=por_recurso
    )


def _vm_com_apelidos(chave: str, *apelidos: str):
    return replace(vm(chave), apelidos=frozenset({chave.lower(), *apelidos}))


def test_casa_por_apelido_sem_diferenciar_caixa() -> None:
    inv = inventario([_vm_com_apelidos("arn:aws:ec2:us-east-1:1:instance/i-abc", "i-abc")])
    c = correlacionar(inv, _custos(("I-ABC", "1", "us-east-1", "AmazonEC2", "10")))
    assert c.custo_encontrado == Decimal("10")
    assert c.fracao_encontrada == Decimal("1")
    assert c.custo_por_chave == {"arn:aws:ec2:us-east-1:1:instance/i-abc": Decimal("10")}
    assert c.custo_da_base == Decimal("110")


def test_varias_linhas_do_mesmo_recurso_somam() -> None:
    inv = inventario([_vm_com_apelidos("vm-1")])
    c = correlacionar(
        inv,
        _custos(("vm-1", "1", "r", "EC2", "10"), ("vm-1", "1", "r", "EBS", "5")),
    )
    assert c.custo_por_chave["vm-1"] == Decimal("15")
    assert c.recursos_do_billing == 1


def test_sub_recurso_da_azure_casa_com_o_pai() -> None:
    vm_azure = "/subscriptions/s/resourcegroups/g/providers/microsoft.compute/virtualmachines/vm1"
    inv = inventario([_vm_com_apelidos(vm_azure)])
    c = correlacionar(inv, _custos((vm_azure + "/extensions/monitor", "s", "eastus", "VM", "3")))
    assert c.custo_por_chave == {vm_azure: Decimal("3")}


def test_ambiguo_prefere_o_recurso_de_unidade_e_conta() -> None:
    """O mock do CUR usa o mesmo id em S3 e ECR: um id, dois recursos."""
    bucket = replace(recurso("aws:1:r:AWS::S3::Bucket:b", T.BUCKET), apelidos=frozenset({"b"}))
    repo = replace(recurso("aws:1:r:AWS::ECR::Repository:b", T.OUTRO), apelidos=frozenset({"b"}))
    c = correlacionar(inventario([repo, bucket]), _custos(("b", "1", "r", "AmazonS3", "7")))
    assert c.custo_por_chave == {"aws:1:r:AWS::S3::Bucket:b": Decimal("7")}
    assert c.ambiguos == 1


def test_lacunas_por_conta_e_por_servico_maior_custo_primeiro() -> None:
    inv = inventario([_vm_com_apelidos("i-1")])
    c = correlacionar(
        inv,
        _custos(
            ("i-1", "111", "us-east-1", "AmazonEC2", "50"),
            ("i-9", "333", "us-east-1", "AmazonEC2", "30"),
            ("snap-1", "111", "us-east-1", "AmazonEC2", "5"),
            ("db-x", "333", "us-east-1", "AmazonRDS", "40"),
        ),
    )
    assert [(x.conta, x.custo) for x in c.lacunas_por_escopo] == [
        ("333", Decimal("70")),
        ("111", Decimal("5")),
    ]
    assert c.lacunas_por_escopo[0].exemplos == ("db-x", "i-9")
    assert [(x.servico, x.custo) for x in c.lacunas_por_servico] == [
        ("AmazonRDS", Decimal("40")),
        ("AmazonEC2", Decimal("35")),
    ]
    assert c.fracao_encontrada == Decimal("50") / Decimal("125")
    assert {r.regra for r in c.riscos} == {"cobertura_baixa"}


def test_sem_cobranca_por_tipo() -> None:
    inv = inventario(
        [
            _vm_com_apelidos("i-1"),
            vm("i-2"),
            recurso("vol-1", T.DISCO),
            recurso("sg-1", T.GRUPO_SEGURANCA),
        ]
    )
    c = correlacionar(inv, _custos(("i-1", "1", "r", "EC2", "1")))
    # Grupo de segurança não é cobrado: não entra.
    assert c.sem_cobranca == {"disco": 1, "instancia": 1}


def test_custo_dos_ociosos_inclui_os_discos_da_maquina_parada() -> None:
    inv = inventario(
        [
            replace(vm("parada", estado=EstadoDoRecurso.PARADO), apelidos=frozenset({"parada"})),
            replace(recurso("vol-a", T.DISCO), apelidos=frozenset({"vol-a"})),
            replace(
                recurso("vol-solto", T.DISCO, estado=EstadoDoRecurso.DISPONIVEL),
                apelidos=frozenset({"vol-solto"}),
            ),
        ],
        [rel("vol-a", "parada", R.ANEXADO_A)],
    )
    c = correlacionar(
        inv, _custos(("vol-a", "1", "r", "EBS", "8"), ("vol-solto", "1", "r", "EBS", "4"))
    )
    assert {(o.recurso, o.motivo, o.custo) for o in c.ociosos} == {
        ("parada", "instancia_parada", Decimal("8")),
        ("vol-solto", "disco_sem_anexo", Decimal("4")),
    }


def test_alinhamento_no_tempo() -> None:
    base = inventario([_vm_com_apelidos("i-1")])
    custos = _custos(("i-1", "1", "r", "EC2", "1"), periodo="2026-03")

    def com_captura(dia: datetime):
        return correlacionar(replace(base, capturado_em=dia), custos).alinhamento

    assert com_captura(datetime(2026, 3, 31, tzinfo=UTC)).desalinhado is False
    assert com_captura(datetime(2026, 5, 10, tzinfo=UTC)).desalinhado is False  # até 45 dias
    depois = com_captura(datetime(2026, 7, 1, tzinfo=UTC))
    assert (depois.desalinhado, depois.dias_fora) == (True, 91)
    antes = com_captura(datetime(2026, 1, 1, tzinfo=UTC))
    assert antes.desalinhado and antes.dias_fora == 59
    c = correlacionar(replace(base, capturado_em=datetime(2026, 7, 1, tzinfo=UTC)), custos)
    assert "inventario_desalinhado" in {r.regra for r in c.riscos}
    # Sem data de captura (Resource Graph) ou sem período: não há como julgar.
    assert correlacionar(base, custos).alinhamento.desalinhado is False
    sem_periodo = _custos(("i-1", "1", "r", "EC2", "1"), periodo="")
    assert correlacionar(base, sem_periodo).alinhamento.periodo_inicio is None


def test_dezembro_vira_o_ano() -> None:
    c = correlacionar(
        replace(
            inventario([_vm_com_apelidos("i-1")]), capturado_em=datetime(2025, 12, 31, tzinfo=UTC)
        ),
        _custos(("i-1", "1", "r", "EC2", "1"), periodo="2025-12"),
    )
    assert c.alinhamento.periodo_fim == datetime(2026, 1, 1, tzinfo=UTC)


def test_billing_sem_recurso_nenhum() -> None:
    c = correlacionar(inventario([vm("i-1")]), _custos())
    assert c.fracao_encontrada is None
    assert c.riscos == ()


def test_captura_estimada_e_dita_no_risco() -> None:
    inv = replace(
        inventario([_vm_com_apelidos("i-1")]), capturado_em=datetime(2026, 9, 26, tzinfo=UTC)
    )
    c = correlacionar(inv, _custos(("i-1", "1", "r", "EC2", "1")), captura_estimada=True)
    assert c.alinhamento.estimado is True
    [risco] = [r for r in c.riscos if r.regra == "inventario_desalinhado"]
    assert "estimada pela data do envio" in risco.detalhe
