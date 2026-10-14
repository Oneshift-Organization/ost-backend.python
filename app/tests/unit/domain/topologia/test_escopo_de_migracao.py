"""Plano de migração de um escopo e dependências entre serviços (PLANO_ESCOPO_DE_MIGRACAO)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from app.domain.services.topologia import analisar_topologia
from app.domain.services.topologia.dependencias_de_servico import dependencias_de_servico
from app.domain.services.topologia.escopo import (
    CatalogoDeAplicacoes,
    Escolha,
    EscolhaInvalidaError,
    fechar_plano,
    montar_catalogo,
)
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import Natureza

from .construtores import camadas_app_banco, inventario, regra

# Recurso → (serviço, custo)
_CUSTOS = {
    "vm-auth": ("EC2", "100"),
    "db-auth": ("RDS", "400"),
    "vm-checkout": ("EC2", "50"),
    "db-checkout": ("RDS", "150"),
    "vm-catalogo": ("EC2", "30"),
    "db-catalogo": ("RDS", "70"),
    "vm-monitor": ("EC2", "10"),
    "db-monitor": ("RDS", "5"),
}


def _cenario(*, catalogo_chama_checkout: bool = False) -> CatalogoDeAplicacoes:
    recursos, relacoes = [], []
    for app in ("auth", "checkout", "catalogo", "monitor"):
        r, rel = camadas_app_banco(app)
        recursos += r
        relacoes += rel
    regras = [
        # Cada VM acessa o banco da própria aplicação: a ligação EC2 → RDS
        *(regra(f"sg-{a}-dados", 5432, de_grupo=f"sg-{a}-app") for a in _apps()),
        # checkout usa o banco do auth: dependência forte, migram juntos
        regra("sg-auth-dados", 5432, de_grupo="sg-checkout-app"),
        # checkout chama o catalogo: fraca, o catalogo vai antes
        regra("sg-catalogo-app", 8080, de_grupo="sg-checkout-app"),
        # monitor chama o checkout: fraca, o monitor fica para trás
        regra("sg-checkout-app", 8080, de_grupo="sg-monitor-app"),
    ]
    if catalogo_chama_checkout:
        regras.append(regra("sg-checkout-app", 9090, de_grupo="sg-catalogo-app"))
    inv = inventario(recursos, relacoes, regras)
    analise = analisar_topologia(inv)
    por_unidade: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for chave, (servico, custo) in _CUSTOS.items():
        por_unidade[analise.unidade_de[chave]][servico] += Decimal(custo)
    deps = dependencias_de_servico(
        Indice(inv), analise.unidade_de, {k: s for k, (s, _) in _CUSTOS.items()}
    )
    return montar_catalogo(analise, por_unidade, {"RDS": Decimal("9")}, deps, "app")


def _apps() -> tuple[str, ...]:
    return ("auth", "checkout", "catalogo", "monitor")


def test_vm_que_acessa_o_banco_da_propria_aplicacao_vira_ligacao_entre_servicos() -> None:
    catalogo = _cenario()
    ec2 = next(s for s in catalogo.servicos if s.nome == "EC2")
    # A topologia entre unidades descarta essa aresta; aqui ela aparece
    assert [(x.servico, x.natureza, x.portas) for x in ec2.depende_de] == [
        ("RDS", Natureza.DADOS, ("tcp/5432",))
    ]
    rds = next(s for s in catalogo.servicos if s.nome == "RDS")
    assert [x.servico for x in rds.usado_por] == ["EC2"]
    assert rds.sem_aplicacao == Decimal("9")


def test_catalogo_por_aplicacao_e_por_servico() -> None:
    catalogo = _cenario()
    apps = {a.nome: a for a in catalogo.aplicacoes}
    assert set(apps) == set(_apps())
    assert apps["auth"].migra_junto_com == ("checkout",)
    assert apps["checkout"].migra_junto_com == ("auth",)
    assert apps["catalogo"].migra_junto_com == ()
    assert dict(apps["checkout"].servicos) == {"RDS": Decimal("150"), "EC2": Decimal("50")}
    # A maior aplicação primeiro
    assert catalogo.aplicacoes[0].nome == "auth"
    ec2 = next(s for s in catalogo.servicos if s.nome == "EC2")
    assert {n for n, _ in ec2.aplicacoes} == set(_apps())


def test_escolher_uma_aplicacao_traz_o_que_vai_junto_antes_e_para_tras() -> None:
    plano = fechar_plano(_cenario(), Escolha(aplicacoes=frozenset({"checkout"})))
    assert plano.aplicacoes == ("auth", "checkout")
    assert [v.aplicacao for v in plano.vao_junto] == ["auth"]
    assert plano.vao_junto[0].motivo.startswith("Compartilha banco com")
    assert not plano.vao_junto[0].incluida_por_voce
    assert [g.nome for g in plano.antes] == ["catalogo"]
    assert plano.antes[0].naturezas == (Natureza.SERVICO,)
    assert [g.nome for g in plano.para_tras] == ["monitor"]
    assert plano.custo == Decimal("700")
    assert ("EC2", "RDS") in {(x.de, x.para) for x in plano.ligacoes}
    # Escolha de aplicação não lista "serviços que vêm junto": são dela
    assert plano.servicos_que_vem_junto == ()


def test_escolher_um_servico_diz_quais_servicos_vieram_junto_e_por_que() -> None:
    plano = fechar_plano(_cenario(), Escolha(servicos=frozenset({"EC2"})))
    assert set(plano.aplicacoes) == set(_apps())
    vem = {v.servico: v.motivo for v in plano.servicos_que_vem_junto}
    assert vem == {"RDS": "EC2 acessa dados em RDS (tcp/5432)"}
    # O custo do serviço escolhido que não casa com aplicação fica fora, declarado
    assert plano.fora_do_plano == ()
    plano_rds = fechar_plano(_cenario(), Escolha(servicos=frozenset({"RDS"})))
    assert plano_rds.fora_do_plano == (("RDS", Decimal("9")),)


def test_conversa_nos_dois_sentidos_aparece_uma_vez() -> None:
    plano = fechar_plano(
        _cenario(catalogo_chama_checkout=True), Escolha(aplicacoes=frozenset({"checkout"}))
    )
    assert [(g.nome, g.nos_dois_sentidos) for g in plano.antes] == [("catalogo", True)]
    assert plano.antes[0].portas == ("tcp/8080", "tcp/9090")
    assert "catalogo" not in {g.nome for g in plano.para_tras}


def test_incluir_um_grupo_a_mao_traz_o_grupo_e_marca_o_motivo() -> None:
    catalogo = _cenario()
    grupo_catalogo = fechar_plano(catalogo, Escolha(aplicacoes=frozenset({"checkout"}))).antes[0]
    plano = fechar_plano(
        catalogo,
        Escolha(
            aplicacoes=frozenset({"checkout"}), incluir_grupos=frozenset({grupo_catalogo.grupo})
        ),
    )
    assert "catalogo" in plano.aplicacoes
    incluida = next(v for v in plano.vao_junto if v.aplicacao == "catalogo")
    assert incluida.incluida_por_voce
    assert plano.antes == ()


@pytest.mark.parametrize(
    "escolha",
    [
        Escolha(),
        Escolha(aplicacoes=frozenset({"nao-existe"})),
        Escolha(servicos=frozenset({"Lambda"})),
        Escolha(aplicacoes=frozenset({"auth"}), incluir_grupos=frozenset({"g-x"})),
    ],
)
def test_escolha_vazia_ou_com_nome_desconhecido_e_recusada(escolha: Escolha) -> None:
    with pytest.raises(EscolhaInvalidaError):
        fechar_plano(_cenario(), escolha)


def test_ambiente_sem_nada_escolhido_da_escopo_vazio() -> None:
    plano = fechar_plano(_cenario(), Escolha(aplicacoes=frozenset({"auth"}), ambiente="prod"))
    # Os recursos do cenário não têm tag de ambiente
    assert (plano.unidades, plano.custo) == ((), Decimal(0))
