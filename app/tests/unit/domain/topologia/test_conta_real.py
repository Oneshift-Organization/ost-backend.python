"""A forma de uma conta real (AWS, 10/2026), anonimizada: o que os mocks não pegavam.

Uma aplicação só, marcada com ``project`` (nenhuma chave de aplicação), num
cluster EKS cujos nós não têm tag; o balanceador do Kong criado pelo cluster; um
banco que o cluster e a VPN acessam na 5432; a VPN aberta para a internet.
"""

from __future__ import annotations

from app.domain.services.topologia import analisar_topologia
from app.tests.unit.domain.topologia.construtores import (
    R,
    T,
    banco,
    inventario,
    lb,
    protege,
    recurso,
    regra,
    rel,
    sg,
    vm,
)

_MARCAS = {"project": "loja", "env": "prd"}


def _conta(*, marcada: bool = True):
    marcas = _MARCAS if marcada else {}
    recursos = [
        recurso("eks-loja", T.CLUSTER_KUBERNETES, tags=marcas),
        vm("i-019711e3c383ca17a", tipo_maquina="r7g.large"),
        vm("i-09650b3692d0461f2", tipo_maquina="t4g.xlarge"),
        lb("k8s-kong"),
        banco("rds-loja", tags=marcas, alta_disponibilidade=False),
        vm("srv-vpn", tipo_maquina="t3.micro"),
        recurso("loja-front", T.BUCKET, tags=marcas),
        recurso("loja-tfstate", T.BUCKET),
        recurso("loki-loja", T.BUCKET),
        sg("sg-cluster"),
        sg("sg-rds"),
        sg("sg-vpn"),
    ]
    relacoes = [
        rel("i-019711e3c383ca17a", "eks-loja", R.MEMBRO_DE),
        rel("i-09650b3692d0461f2", "eks-loja", R.MEMBRO_DE),
        rel("k8s-kong", "eks-loja", R.MEMBRO_DE),
        *protege("sg-cluster", "i-019711e3c383ca17a", "i-09650b3692d0461f2", "eks-loja"),
        *protege("sg-rds", "rds-loja"),
        *protege("sg-vpn", "srv-vpn"),
    ]
    regras = [
        regra("sg-rds", 5432, de_grupo="sg-cluster"),
        regra("sg-rds", 5432, de_grupo="sg-vpn"),
        regra("sg-vpn", 1194, internet=True, protocolo="udp"),
        regra("sg-vpn", 22, internet=True),
    ]
    return inventario(recursos, relacoes, regras)


def _unidade_de(analise, recurso: str):
    return next(u for u in analise.unidades if recurso in u.recursos)


def test_projeto_vira_aplicacao_quando_nao_ha_chave_de_aplicacao() -> None:
    analise = analisar_topologia(_conta())
    premissa = next(p for p in analise.premissas if p.campo == "chave_aplicacao")
    assert premissa.valor == "project"
    assert "projeto" in premissa.base
    loja = _unidade_de(analise, "rds-loja")
    assert loja.aplicacao == "loja"
    assert loja.nome == "loja (prd)"
    # Nós sem tag vão com o cluster; o balanceador do cluster também
    assert {"eks-loja", "i-019711e3c383ca17a", "k8s-kong", "loja-front"} <= set(loja.recursos)


def test_vpn_aberta_para_a_internet_e_ferramenta() -> None:
    analise = analisar_topologia(_conta())
    vpn = _unidade_de(analise, "srv-vpn")
    assert vpn.id in analise.ferramentas
    # Não junta ao grupo da aplicação nem torna o banco "compartilhado"
    loja = _unidade_de(analise, "rds-loja")
    assert analise.grupo_de.get(vpn.id) != analise.grupo_de.get(loja.id)
    assert not [r for r in analise.riscos if r.regra == "banco_compartilhado"]


def test_porta_de_administracao_aberta_sai_uma_vez_com_o_nome_do_grupo() -> None:
    analise = analisar_topologia(_conta())
    expostos = [r for r in analise.riscos if r.regra == "exposicao_administrativa"]
    assert len(expostos) == 1
    assert "sg-vpn: 22" in expostos[0].detalhe
    assert not [r for r in analise.riscos if r.regra == "porta_ampla_aberta"]


def test_sem_tag_a_unidade_leva_o_nome_do_cluster_e_nao_do_no() -> None:
    analise = analisar_topologia(_conta(marcada=False))
    cluster = _unidade_de(analise, "eks-loja")
    assert cluster.aplicacao is None
    assert cluster.nome == "eks-loja"


def test_grupos_com_a_mesma_exposicao_viram_um_risco() -> None:
    recursos = [vm("a"), vm("b"), sg("sg-a"), sg("sg-b")]
    relacoes = [*protege("sg-a", "a"), *protege("sg-b", "b")]
    regras = [regra("sg-a", 22, internet=True), regra("sg-b", 3389, internet=True)]
    analise = analisar_topologia(inventario(recursos, relacoes, regras))
    [risco] = [r for r in analise.riscos if r.regra == "exposicao_administrativa"]
    assert risco.total == 2
    assert "sg-a: 22" in risco.detalhe
    assert "sg-b: 3389" in risco.detalhe


def _com_componentes():
    inv = _conta()
    componentes = [
        recurso("cdn-front", T.OUTRO, tags=_MARCAS, regiao="global", componente="cdn"),
        recurso("lambda-start", T.OUTRO, tags=_MARCAS, componente="funcao"),
        recurso("ecr-sem-tag", T.OUTRO, componente="registro_de_imagens"),
        recurso("rtb-com-tag", T.OUTRO, tags=_MARCAS),  # rota: não é componente
    ]
    return inventario([*inv.recursos, *componentes], inv.relacoes, inv.regras)


def test_componente_com_a_tag_vai_com_a_aplicacao_e_o_global_entra_pela_conta() -> None:
    analise = analisar_topologia(_com_componentes())
    loja = _unidade_de(analise, "rds-loja")
    assert {"cdn-front", "lambda-start"} <= set(loja.recursos)
    # Sem tag não vira unidade; tabela de rota não é componente
    em_unidade = {r for u in analise.unidades for r in u.recursos}
    assert "ecr-sem-tag" not in em_unidade
    assert "rtb-com-tag" not in em_unidade
    # A cobertura da chave não muda com componentes sem tag
    premissa = next(p for p in analise.premissas if p.campo == "chave_aplicacao")
    assert premissa.valor == "project"
