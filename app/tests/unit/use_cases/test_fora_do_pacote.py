"""O que fica fora do pacote "só o serviço": cada caso com a frase verdadeira
(PROXIMAS_EXECUCOES, item 3)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.entities.topologia import TipoDeRecurso
from app.domain.services.atribuicao_por_tipo import SERVICO_DO_NATIVO
from app.use_cases.analysis.escopo_de_migracao import (
    RecursoDoEscopo,
    ServicoNoInventario,
    _fora_do_pacote,
)


def _recurso(chave: str, tipo: TipoDeRecurso) -> RecursoDoEscopo:
    return RecursoDoEscopo(
        chave=chave, nome=chave, tipo=str(tipo), tipo_nativo="x", regiao=None, custo=Decimal(0)
    )


_NO_INVENTARIO = {
    "AmazonVPC": ServicoNoInventario(
        # A tabela de rotas é "outro", não fundação: basta ter fundação (o caso da f67fcd01)
        (("AWS::EC2::Subnet", 15), ("AWS::EC2::RouteTable", 9), ("AWS::EC2::VPC", 3)),
        fundacao=True,
    ),
    "AmazonRoute53": ServicoNoInventario((("AWS::Route53::HostedZone", 1),), fundacao=False),
    "AWSELB": ServicoNoInventario(
        (("AWS::ElasticLoadBalancingV2::LoadBalancer", 1),), fundacao=False
    ),
}


def _avisos() -> dict[str, str]:
    recursos = [_recurso("alb", TipoDeRecurso.BALANCEADOR), _recurso("vm", TipoDeRecurso.INSTANCIA)]
    servico_de = {"alb": "AWSELB", "vm": "AmazonEC2"}
    escolhidos = frozenset(
        {"AmazonVPC", "AmazonRoute53", "AWS Cost Explorer", "AWSELB", "AmazonEC2"}
    )
    avisos = _fora_do_pacote(escolhidos, recursos, servico_de, _NO_INVENTARIO)
    return {a.split(":")[0].split(" não vira")[0]: a for a in avisos}


def test_fundacao_de_rede_vira_a_rede_do_pacote() -> None:
    aviso = _avisos()["AmazonVPC"]
    assert "vira a rede do pacote pelo espelho" in aviso
    assert "15 AWS::EC2::Subnet" in aviso
    assert "gateways" in aviso
    assert "nenhum recurso" not in aviso


def test_recurso_fora_das_aplicacoes_e_dito_como_esta() -> None:
    aviso = _avisos()["AmazonRoute53"]
    assert "1 AWS::Route53::HostedZone no inventário, fora das aplicações do escopo" in aviso


def test_servico_sem_infraestrutura() -> None:
    aviso = _avisos()["AWS Cost Explorer"]
    assert "nenhum recurso dele no inventário" in aviso
    assert "sem infraestrutura própria" in aviso


def test_tipo_que_o_pacote_nao_cria_e_o_que_ele_cria() -> None:
    avisos = _avisos()
    assert "crie o equivalente dele no destino à parte" in avisos["AWSELB"]
    assert "AmazonEC2" not in avisos  # a máquina o pacote cria


def test_zona_de_dns_e_rede_da_aws_tem_servico() -> None:
    assert SERVICO_DO_NATIVO["AWS::Route53::HostedZone"] == "AmazonRoute53"
    assert SERVICO_DO_NATIVO["AWS::EC2::VPC"] == "AmazonVPC"
    assert SERVICO_DO_NATIVO["AWS::EC2::Subnet"] == "AmazonVPC"
