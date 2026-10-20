"""Unidades (RI6) e dependências (RI5), uma regra de cada vez."""

from __future__ import annotations

from app.domain.entities.topologia import Seletor, TipoDeSeletor
from app.domain.services.topologia import (
    Declaracoes,
    DependenciaDeclarada,
    analisar_topologia,
    dependencias,
)
from app.domain.services.topologia.modelo import Acoplamento, Natureza
from app.tests.unit.domain.topologia.construtores import (
    R,
    T,
    banco,
    camadas_app_banco,
    inventario,
    lb,
    protege,
    recurso,
    regra,
    rel,
    sg,
    subrede,
    vm,
)

# ── unidades ────────────────────────────────────────────────────────────


def _unidade_de(analise, chave):
    return analise.unidade_de[chave]


def test_anexos_vao_com_o_dono_e_disco_solto_nao_e_unidade() -> None:
    inv = inventario(
        [
            vm("vm-1"),
            recurso("disco-1", T.DISCO),
            recurso("nic-1", T.INTERFACE_REDE),
            recurso("ip-1", T.IP_PUBLICO),
            recurso("disco-solto", T.DISCO),
        ],
        [
            rel("disco-1", "vm-1", R.ANEXADO_A),
            rel("nic-1", "vm-1", R.ANEXADO_A),
            rel("ip-1", "nic-1", R.ANEXADO_A),
        ],
    )
    a = analisar_topologia(inv)
    assert [u.recursos for u in a.unidades] == [("disco-1", "ip-1", "nic-1", "vm-1")]
    assert "disco-solto" not in a.unidade_de


def test_cluster_de_banco_junta_as_instancias() -> None:
    inv = inventario(
        [banco("db-a"), banco("db-b"), recurso("cluster", T.OUTRO)],
        [rel("db-a", "cluster", R.MEMBRO_DE), rel("db-b", "cluster", R.MEMBRO_DE)],
    )
    a = analisar_topologia(inv)
    assert len(a.unidades) == 1
    assert set(a.unidades[0].recursos) == {"db-a", "db-b", "cluster"}


def test_tag_de_aplicacao_detectada_agrupa_por_conta_regiao_e_ambiente() -> None:
    inv = inventario(
        [
            vm("a1", tags={"App": "loja", "env": "prod"}),
            vm("a2", tags={"app": "loja", "env": "prod"}),
            vm("a3", tags={"app": "loja", "env": "dev"}),
            vm("a4", tags={"app": "loja", "env": "prod"}, regiao="r2"),
            vm("b1", tags={"app": "busca", "env": "prod"}),
        ]
    )
    a = analisar_topologia(inv)
    grupos = sorted(sorted(u.recursos) for u in a.unidades)
    assert grupos == [["a1", "a2"], ["a3"], ["a4"], ["b1"]]
    premissas = {p.campo: p for p in a.premissas}
    assert premissas["chave_aplicacao"].valor == "app"
    assert premissas["chave_ambiente"].valor == "env"
    # Nome distinguível: a mesma aplicação em duas regiões.
    nomes = {u.nome for u in a.unidades}
    assert len(nomes) == len(a.unidades)
    assert "loja (prod) · r1" in nomes and "loja (prod) · r2" in nomes


def test_tag_presente_em_menos_da_metade_nao_agrupa() -> None:
    inv = inventario(
        [vm("a1", tags={"app": "x"}), vm("a2"), vm("a3", tags={"app": "x"}), vm("a4"), vm("a5")]
    )
    a = analisar_topologia(inv)
    assert len(a.unidades) == 5
    assert {p.campo: p.valor for p in a.premissas}["chave_aplicacao"] is None


def test_chave_declarada_e_desligada() -> None:
    recursos = [
        vm("a1", tags={"sistema": "x", "app": "p"}),
        vm("a2", tags={"sistema": "x", "app": "q"}),
    ]
    declarada = analisar_topologia(inventario(recursos), Declaracoes(chave_aplicacao="sistema"))
    assert len(declarada.unidades) == 1
    desligada = analisar_topologia(inventario(recursos), Declaracoes(chave_aplicacao=""))
    assert len(desligada.unidades) == 2


# ── dependências por regra ──────────────────────────────────────────────


def _deps(analise):
    nomes = {u.id: u.nome for u in analise.unidades}
    return {(nomes[d.de], nomes[d.para]): d for d in analise.dependencias}


def test_regra_entre_grupos_vira_dependencia_de_dados() -> None:
    rec, rels = camadas_app_banco("loja", tags=False)
    inv = inventario(rec, rels, [regra("sg-loja-dados", 5432, de_grupo="sg-loja-app")])
    a = analisar_topologia(inv)
    [d] = a.dependencias
    assert (d.de, d.para) == (a.unidade_de["vm-loja"], a.unidade_de["db-loja"])
    assert d.natureza is Natureza.DADOS
    assert d.acoplamento is Acoplamento.FORTE
    assert d.portas == ("tcp/5432",)
    assert d.evidencias[0].grupo == "sg-loja-dados"


def test_porta_de_banco_numa_vm_tambem_e_dados() -> None:
    inv = inventario(
        [vm("app"), vm("pg-na-vm"), sg("sg-app"), sg("sg-pg")],
        [*protege("sg-app", "app"), *protege("sg-pg", "pg-na-vm")],
        [regra("sg-pg", 5432, de_grupo="sg-app")],
    )
    [d] = analisar_topologia(inv).dependencias
    assert d.natureza is Natureza.DADOS


def test_servico_administrativo_e_encaminhamento() -> None:
    inv = inventario(
        [
            vm("web"),
            vm("api"),
            vm("bastion"),
            lb("alb"),
            sg("sg-web"),
            sg("sg-api"),
            sg("sg-bastion"),
            sg("sg-alb"),
        ],
        [
            *protege("sg-web", "web"),
            *protege("sg-api", "api"),
            *protege("sg-bastion", "bastion"),
            *protege("sg-alb", "alb"),
        ],
        [
            regra("sg-api", 8080, de_grupo="sg-web"),
            regra("sg-api", 22, de_grupo="sg-bastion"),
            regra("sg-web", 443, de_grupo="sg-alb"),
        ],
    )
    deps = _deps(analisar_topologia(inv))
    assert deps[("web", "api")].natureza is Natureza.SERVICO
    assert deps[("web", "api")].acoplamento is Acoplamento.FRACO
    assert deps[("bastion", "api")].natureza is Natureza.ADMINISTRATIVO
    assert deps[("bastion", "api")].acoplamento is Acoplamento.NENHUM
    assert deps[("alb", "web")].natureza is Natureza.ENCAMINHAMENTO


def test_auto_referencia_e_grupo_amplo_nao_viram_dependencia() -> None:
    inv = inventario(
        [vm("a"), vm("b"), sg("default"), sg("toda-rede", amplo=True)],
        [*protege("default", "a", "b"), *protege("toda-rede", "a", "b")],
        [
            regra("default", None, de_grupo="default", protocolo="todos"),
            regra("toda-rede", 22, de_cidr="10.0.0.0/8"),
        ],
    )
    a = analisar_topologia(inv)
    assert a.dependencias == ()
    regras = {r.regra for r in a.riscos}
    assert {"grupo_interno", "regra_ampla"} <= regras


def _com_subredes(regra_de_teste, ip_da_origem: str | None = "10.0.1.5"):
    atributos = {"ip_privado": ip_da_origem} if ip_da_origem else {}
    return inventario(
        [
            subrede("s-app", "10.0.1.0/24"),
            subrede("s-outra", "10.0.2.0/24"),
            vm("origem", **atributos),
            vm("vizinha", ip_privado="10.0.1.99") if ip_da_origem else vm("vizinha"),
            vm("alvo"),
            sg("sg-alvo"),
        ],
        [
            rel("origem", "s-app", R.CONTIDO_EM),
            rel("vizinha", "s-app", R.CONTIDO_EM),
            *protege("sg-alvo", "alvo"),
        ],
        [regra_de_teste],
    )


def test_cidr_da_subrede_inteira() -> None:
    a = analisar_topologia(_com_subredes(regra("sg-alvo", 8080, de_cidr="10.0.1.0/24")))
    assert {n[0] for n in _deps(a)} == {"origem", "vizinha"}


def test_cidr_maior_que_a_unica_subrede_tocada() -> None:
    # 10.0.0.0/23 cobre 10.0.0.0-10.0.1.255: toca só a subrede `s-app`, inteira.
    a = analisar_topologia(_com_subredes(regra("sg-alvo", 8080, de_cidr="10.0.1.0/23")))
    assert {n[0] for n in _deps(a)} == {"origem", "vizinha"}


def test_cidr_que_toca_duas_subredes_e_amplo() -> None:
    # A VPC inteira acessa o alvo: configuração preguiçosa, não dependência.
    a = analisar_topologia(_com_subredes(regra("sg-alvo", 8080, de_cidr="10.0.0.0/22")))
    assert a.dependencias == ()
    assert "regra_ampla" in {r.regra for r in a.riscos}


def test_cidr_de_um_host_pelo_ip() -> None:
    a = analisar_topologia(_com_subredes(regra("sg-alvo", 8080, de_cidr="10.0.1.5/32")))
    assert set(_deps(a)) == {("origem", "alvo")}


def test_cidr_de_host_sem_ip_conhecido_nao_infere() -> None:
    a = analisar_topologia(
        _com_subredes(regra("sg-alvo", 8080, de_cidr="10.0.1.5/32"), ip_da_origem=None)
    )
    assert a.dependencias == ()
    assert "origem_nao_identificada" in {r.regra for r in a.riscos}


def test_cidr_fora_do_inventario_e_origem_externa() -> None:
    a = analisar_topologia(_com_subredes(regra("sg-alvo", 8080, de_cidr="203.0.113.0/24")))
    assert a.dependencias == ()
    assert "origem_externa" in {r.regra for r in a.riscos}


def test_destino_estreita_o_alvo() -> None:
    inv = inventario(
        [vm("web"), vm("api"), vm("outra"), sg("nsg"), sg("asg-web"), sg("asg-api")],
        [
            *protege("nsg", "web", "api", "outra"),
            *protege("asg-web", "web"),
            *protege("asg-api", "api"),
        ],
        [regra("nsg", 8080, de_grupo="asg-web", destino=Seletor(TipoDeSeletor.GRUPO, "asg-api"))],
    )
    assert set(_deps(analisar_topologia(inv))) == {("web", "api")}


def test_teto_de_pares(monkeypatch) -> None:
    monkeypatch.setattr(dependencias, "TETO_DE_PARES", 3)
    inv = inventario(
        [*(vm(f"o{i}") for i in range(2)), *(vm(f"a{i}") for i in range(2)), sg("sa"), sg("sb")],
        [*protege("sa", "o0", "o1"), *protege("sb", "a0", "a1")],
        [regra("sb", 8080, de_grupo="sa")],
    )
    a = analisar_topologia(inv)
    assert a.dependencias == ()
    assert "regra_ampla" in {r.regra for r in a.riscos}


def test_encaminha_para_pela_interface() -> None:
    inv = inventario(
        [lb("lb"), vm("vm"), recurso("nic", T.INTERFACE_REDE)],
        [rel("nic", "vm", R.ANEXADO_A), rel("lb", "nic", R.ENCAMINHA_PARA)],
    )
    [d] = analisar_topologia(inv).dependencias
    assert d.natureza is Natureza.ENCAMINHAMENTO


# ── dependências declaradas ─────────────────────────────────────────────


def test_declarada_entre_aplicacoes_so_no_mesmo_escopo() -> None:
    inv = inventario(
        [
            vm("checkout-prod", tags={"app": "checkout", "env": "prod"}),
            vm("pag-prod", tags={"app": "pagamentos", "env": "prod"}),
            vm("pag-dev", tags={"app": "pagamentos", "env": "dev"}),
        ]
    )
    decl = Declaracoes(
        dependencias=(
            DependenciaDeclarada("aplicacao:checkout", "aplicacao:pagamentos", motivo="HTTP"),
            DependenciaDeclarada("aplicacao:nao-existe", "aplicacao:pagamentos"),
        )
    )
    a = analisar_topologia(inv, decl)
    [d] = a.dependencias
    nomes = {u.id: u.nome for u in a.unidades}
    assert (nomes[d.de], nomes[d.para]) == ("checkout (prod)", "pagamentos (prod)")
    assert d.natureza is Natureza.DECLARADA
    assert d.evidencias[0].descricao == "HTTP"
    ignoradas = {p.campo: p for p in a.premissas}["dependencias_declaradas_ignoradas"]
    assert ignoradas.valor == "1"


def test_declarada_por_recurso_com_acoplamento_forte_junta() -> None:
    inv = inventario([vm("a"), vm("b")])
    decl = Declaracoes(dependencias=(DependenciaDeclarada("a", "b", Acoplamento.FORTE),))
    a = analisar_topologia(inv, decl)
    assert len(a.grupos) == 1
