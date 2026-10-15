"""Custo sem ID de recurso, ligado pelo tipo (PLANO_CENARIOS_DE_ENTRADA §3.3).

As linhas são as da fatura real da conta kitconta (formato detalhado legado da
AWS, 09/2026), com os valores dela.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.domain.services.atribuicao_por_tipo import atribuir_por_tipo, servicos_do_recurso
from app.domain.services.correlacao_inventario import (
    CustoDeRecurso,
    CustosDoBilling,
    LinhaSemRecurso,
    correlacionar,
)
from app.domain.services.correspondencia_inventario import (
    ESTIMADO,
    OK,
    SEM_CORRESPONDENCIA,
    SEM_ID_DE_RECURSO,
    avaliar_correspondencia,
)
from app.tests.unit.domain.topologia.construtores import T, banco, inventario, lb, recurso, vm

_CONTA = "538358956561"
_REGIAO = "us-east-1"


def _r(chave: str, tipo: T, **kw):
    return recurso(chave, tipo, conta=_CONTA, regiao=_REGIAO, **kw)


def _conta():
    nat = replace(_r("nat-1", T.GATEWAY), tipo_nativo="AWS::EC2::NatGateway")
    return inventario(
        [
            _r("eks-kitconta-prd", T.CLUSTER_KUBERNETES),
            vm("i-09650b3692d0461f2", conta=_CONTA, regiao=_REGIAO, tipo_maquina="t4g.xlarge"),
            vm("i-019711e3c383ca17a", conta=_CONTA, regiao=_REGIAO, tipo_maquina="r7g.large"),
            vm("srv-openvpn-prd", conta=_CONTA, regiao=_REGIAO, tipo_maquina="t3.micro"),
            banco(
                "rds-kitconta-prd",
                conta=_CONTA,
                regiao=_REGIAO,
                classe="db.t3.medium",
                armazenamento_gb=50,
            ),
            lb("k8s-kong", conta=_CONTA, regiao=_REGIAO),
            _r("vol-a", T.DISCO, tipo_disco="gp3", tamanho_gb=20),
            _r("vol-b", T.DISCO, tipo_disco="gp3", tamanho_gb=60),
            _r("bucket-front", T.BUCKET),
            _r("bucket-tfstate", T.BUCKET),
            nat,
        ]
    )


def _sem_id(servico: str, uso: str, custo: str, qtd: str | None = None) -> LinhaSemRecurso:
    return LinhaSemRecurso(
        conta=_CONTA,
        regiao=None,
        servico=servico,
        uso=uso,
        custo=Decimal(custo),
        quantidade=Decimal(qtd) if qtd else None,
    )


def _fatura(*linhas: LinhaSemRecurso, com_id: tuple[CustoDeRecurso, ...] = ()) -> CustosDoBilling:
    return CustosDoBilling(
        periodo="2026-09",
        moeda="USD",
        custo_da_base=sum((x.custo for x in linhas), Decimal(0))
        + sum((x.custo for x in com_id), Decimal(0)),
        por_recurso=com_id,
        sem_recurso=linhas,
        provedor="aws",
    )


_FATURA_REAL = (
    _sem_id("AmazonEKS", "USE1-AmazonEKS-Hours:perCluster", "72", "720"),
    _sem_id("AmazonEC2", "BoxUsage:t4g.xlarge", "71.9819518656", "535.579999"),
    _sem_id("AmazonEC2", "BoxUsage:r7g.large", "58.6957701897", "548.046407"),
    _sem_id("AmazonRDS", "InstanceUsage:db.t3.medium", "38.573797896", "535.747193"),
    _sem_id("AmazonRDS", "RDS:GP3-Storage", "5.55"),
    _sem_id("AmazonEC2", "NatGateway-Hours", "32.4", "720"),
    _sem_id("AWSELB", "LoadBalancerUsage", "16.29"),
    _sem_id("AmazonEC2", "EBS:VolumeUsage.gp3", "8"),
    _sem_id("AmazonS3", "TimedStorage-ByteHrs", "3.82"),
    _sem_id("AWSSecretsManager", "AWSSecretsManager-Secrets", "7.32"),
)


def _por_recurso(custos: CustosDoBilling) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for x in custos.por_recurso:
        total[x.resource_id] = total.get(x.resource_id, Decimal(0)) + x.custo
    return total


def test_fatura_sem_id_liga_cada_linha_ao_recurso_do_mesmo_tipo() -> None:
    custos = atribuir_por_tipo(_conta(), _fatura(*_FATURA_REAL))
    custo = _por_recurso(custos)
    assert custo["eks-kitconta-prd"] == Decimal("72")
    assert custo["i-09650b3692d0461f2"] == Decimal("71.9819518656")
    assert custo["i-019711e3c383ca17a"] == Decimal("58.6957701897")
    assert custo["rds-kitconta-prd"] == Decimal("38.573797896") + Decimal("5.55")
    assert custo["nat-1"] == Decimal("32.4")
    assert custo["k8s-kong"] == Decimal("16.29")
    # Disco pelo tamanho: 20 GB e 60 GB dividem 8 em 2 e 6
    assert (custo["vol-a"], custo["vol-b"]) == (Decimal("2"), Decimal("6"))
    assert all(x.estimado and x.origem is None and x.base for x in custos.por_recurso)
    # Dois buckets e uma linha de S3: nada no inventário diz quanto cada um consome.
    # Secrets Manager não tem recurso no inventário.
    assert {x.servico for x in custos.sem_recurso} == {"AmazonS3", "AWSSecretsManager"}


def test_a_maquina_que_a_fatura_nao_cita_nao_recebe_custo() -> None:
    custos = atribuir_por_tipo(_conta(), _fatura(*_FATURA_REAL))
    assert "srv-openvpn-prd" not in _por_recurso(custos)


def test_correspondencia_diz_que_o_custo_e_estimado() -> None:
    inv = _conta()
    custos = atribuir_por_tipo(inv, _fatura(*_FATURA_REAL))
    corr = correlacionar(inv, custos)
    r = avaliar_correspondencia(inv, custos, corr)
    assert (r.status, r.liga_custo) == (ESTIMADO, True)
    assert r.fracao_encontrada is None  # não há custo com ID para medir
    assert r.fracao_estimada is not None and r.fracao_estimada > Decimal("0.9")
    assert "estimado pelo tipo" in r.mensagem
    assert "CUR 2.0" in r.mensagem
    assert "Cost Explorer" not in r.mensagem


def test_sem_nada_ligavel_continua_sem_id_e_sem_cost_explorer_na_mensagem() -> None:
    inv = _conta()
    custos = atribuir_por_tipo(inv, _fatura(_sem_id("AWSSecretsManager", "x", "7")))
    r = avaliar_correspondencia(inv, custos, correlacionar(inv, custos))
    assert (r.status, r.liga_custo) == (SEM_ID_DE_RECURSO, False)
    assert "Cost Explorer" not in r.mensagem


def test_maquinas_do_mesmo_tipo_dividem_igual_e_o_total_fecha() -> None:
    inv = inventario(
        [
            vm("a", conta=_CONTA, regiao=_REGIAO, tipo_maquina="m5.large"),
            vm("b", conta=_CONTA, regiao=_REGIAO, tipo_maquina="m5.large"),
            vm("c", conta=_CONTA, regiao=_REGIAO, tipo_maquina="m5.large"),
        ]
    )
    custos = atribuir_por_tipo(inv, _fatura(_sem_id("AmazonEC2", "BoxUsage:m5.large", "100")))
    partes = [x.custo for x in custos.por_recurso]
    assert len(partes) == 3
    assert sum(partes) == Decimal("100")
    assert "dividido entre 3" in (custos.por_recurso[0].base or "")


def test_no_trocado_pelo_autoscaling_cobra_pelo_tipo_que_esta_no_inventario() -> None:
    """CUR com ID: a máquina de ontem não está no inventário, a de hoje está."""
    inv = _conta()
    antigo = CustoDeRecurso(
        resource_id="i-ontem",
        conta=_CONTA,
        regiao=_REGIAO,
        servico="AmazonEC2",
        custo=Decimal("10"),
        uso="BoxUsage:t4g.xlarge",
    )
    bucket_que_sumiu = CustoDeRecurso(
        resource_id="bucket-apagado",
        conta=_CONTA,
        regiao=_REGIAO,
        servico="AmazonS3",
        custo=Decimal("3"),
        uso="TimedStorage-ByteHrs",
    )
    medido = CustoDeRecurso(
        resource_id="rds-kitconta-prd",
        conta=_CONTA,
        regiao=_REGIAO,
        servico="AmazonRDS",
        custo=Decimal("30"),
        uso="InstanceUsage:db.t3.medium",
    )
    custos = atribuir_por_tipo(inv, _fatura(com_id=(antigo, bucket_que_sumiu, medido)))
    [no] = [x for x in custos.por_recurso if x.origem == "i-ontem"]
    assert (no.resource_id, no.estimado) == ("i-09650b3692d0461f2", True)
    # Só máquina e disco trocam de ID; o bucket apagado continua lacuna
    assert any(x.resource_id == "bucket-apagado" and not x.estimado for x in custos.por_recurso)
    corr = correlacionar(inv, custos)
    assert corr.custo_estimado_com_id == Decimal("10")
    assert corr.custo_com_recurso == Decimal("43")
    assert corr.custo_encontrado == Decimal("40")
    r = avaliar_correspondencia(inv, custos, corr)
    assert r.status in (OK, "parcial")
    assert "autoscaling" in r.mensagem


def test_inventario_de_outra_conta_nao_recebe_nada() -> None:
    inv = inventario([vm("i-9", conta="999", regiao=_REGIAO, tipo_maquina="t4g.xlarge")])
    linha = CustoDeRecurso(
        resource_id="i-1",
        conta=_CONTA,
        regiao=_REGIAO,
        servico="AmazonEC2",
        custo=Decimal("10"),
        uso="BoxUsage:t4g.xlarge",
    )
    custos = atribuir_por_tipo(inv, _fatura(com_id=(linha,)))
    assert not any(x.estimado for x in custos.por_recurso)
    r = avaliar_correspondencia(inv, custos, correlacionar(inv, custos))
    assert r.status == SEM_CORRESPONDENCIA


def test_regiao_vem_do_prefixo_do_tipo_de_uso() -> None:
    inv = inventario([vm("a", conta=_CONTA, regiao="us-east-1", tipo_maquina="m5.large")])
    custos = atribuir_por_tipo(inv, _fatura(_sem_id("AmazonEC2", "USE2-BoxUsage:m5.large", "9")))
    assert not custos.por_recurso  # a linha é de us-east-2; a máquina, de us-east-1


def test_servico_que_cada_tipo_gera() -> None:
    inv = _conta()
    assert servicos_do_recurso("aws", inv.recursos) >= {
        "AmazonEC2",
        "AmazonRDS",
        "AmazonEKS",
        "AWSELB",
        "AmazonS3",
    }
    assert servicos_do_recurso("gcp", inv.recursos) == set()
