"""Acoplamento (RI7), ondas (RI8, RI9), criticidade (RI10), riscos (RI11) e
ociosos (RI12)."""

from __future__ import annotations

from app.domain.entities.topologia import EstadoDoRecurso
from app.domain.services.topologia import Declaracoes, analisar_topologia
from app.domain.services.topologia.modelo import Acoplamento, Nivel, Severidade
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
    vm,
)


def _nome(analise, grupo_id):
    return next(g.nome for g in analise.grupos if g.id == grupo_id)


def _grupos_por_nome(analise):
    return {g.nome: g for g in analise.grupos}


def _regras(analise):
    return {r.regra: r for r in analise.riscos}


# ── acoplamento ─────────────────────────────────────────────────────────


def test_banco_exclusivo_junta_com_a_aplicacao() -> None:
    rec, rels = camadas_app_banco("loja", tags=False)
    a = analisar_topologia(
        inventario(rec, rels, [regra("sg-loja-dados", 5432, de_grupo="sg-loja-app")])
    )
    assert len(a.grupos) == 1
    assert "banco_compartilhado" not in _regras(a)


def test_bastion_vira_ferramenta_e_sai_das_ondas() -> None:
    rec, rels = camadas_app_banco("a", tags=False)
    rec2, rels2 = camadas_app_banco("b", tags=False)
    inv = inventario(
        [*rec, *rec2, vm("bastion"), sg("sg-bastion")],
        [*rels, *rels2, *protege("sg-bastion", "bastion")],
        [
            regra("sg-a-app", 22, de_grupo="sg-bastion"),
            regra("sg-b-app", 22, de_grupo="sg-bastion"),
        ],
    )
    a = analisar_topologia(inv)
    assert a.ferramentas == (a.unidade_de["bastion"],)
    assert "ferramenta_administrativa" in _regras(a)
    em_ondas = {g for o in a.ondas for g in o.grupos} | set(a.independentes)
    assert a.grupo_de[a.unidade_de["bastion"]] not in em_ondas


def test_ferramenta_declarada() -> None:
    inv = inventario([vm("monit", tags={"app": "monitoramento"}), vm("x", tags={"app": "x"})])
    a = analisar_topologia(inv, Declaracoes(ferramentas=("aplicacao:monitoramento",)))
    assert a.ferramentas == (a.unidade_de["monit"],)


def test_monitoramento_com_bancos_de_tres_aplicacoes_e_hub() -> None:
    recursos, relacoes, regras_ = [vm("monit"), sg("sg-monit")], protege("sg-monit", "monit"), []
    for app in ("a", "b", "c"):
        rec, rels = camadas_app_banco(app)
        recursos += rec
        relacoes += rels
        regras_ += [
            regra(f"sg-{app}-dados", 5432, de_grupo=f"sg-{app}-app"),
            regra(f"sg-{app}-dados", 5432, de_grupo="sg-monit"),
        ]
    a = analisar_topologia(inventario(recursos, relacoes, regras_))
    assert "hub_de_dados" in _regras(a)
    # As três aplicações continuam separadas; o monitoramento depende delas.
    nomes = {g.nome for g in a.grupos}
    assert {"a", "b", "c"} <= nomes
    [onda_final] = [o for o in a.ondas if a.grupo_de[a.unidade_de["monit"]] in o.grupos]
    assert onda_final.numero == 2
    rebaixadas = [d for d in a.dependencias if d.rebaixada == "hub_de_dados"]
    assert len(rebaixadas) == 3 and all(d.acoplamento is Acoplamento.FRACO for d in rebaixadas)


def test_aplicacao_com_tres_bancos_proprios_nao_e_hub() -> None:
    recursos = [vm("app"), sg("sg-app")]
    relacoes = protege("sg-app", "app")
    regras_ = []
    for i in range(3):
        recursos += [banco(f"db{i}"), sg(f"sg-db{i}")]
        relacoes += protege(f"sg-db{i}", f"db{i}")
        regras_.append(regra(f"sg-db{i}", 5432, de_grupo="sg-app"))
    a = analisar_topologia(inventario(recursos, relacoes, regras_))
    assert "hub_de_dados" not in _regras(a)
    assert len(a.grupos) == 1  # a aplicação e os três bancos migram juntos


def test_balanceador_compartilhado_conta_a_propria_unidade() -> None:
    inv = inventario(
        [
            lb("alb", tags={"app": "catalogo"}),
            vm("cat", tags={"app": "catalogo"}),
            vm("busca", tags={"app": "busca"}),
            sg("sg-alb"),
            sg("sg-cat"),
            sg("sg-busca"),
        ],
        [*protege("sg-alb", "alb"), *protege("sg-cat", "cat"), *protege("sg-busca", "busca")],
        [regra("sg-cat", 8080, de_grupo="sg-alb"), regra("sg-busca", 8080, de_grupo="sg-alb")],
    )
    a = analisar_topologia(inv)
    assert "balanceador_compartilhado" in _regras(a)
    assert {g.nome for g in a.grupos} == {"catalogo", "busca"}
    [d] = a.dependencias
    assert d.rebaixada == "balanceador_compartilhado"


def test_banco_de_uma_aplicacao_acessado_por_outra_e_compartilhado() -> None:
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
    a = analisar_topologia(inv)
    compartilhado = _regras(a)["banco_compartilhado"]
    assert compartilhado.recursos == ("db-auth",)
    assert "checkout" in compartilhado.detalhe
    assert len(a.grupos) == 1  # auth e checkout presos pelo banco


def test_grupo_acoplado_grande() -> None:
    recursos = [banco("central", tags={"app": "central"}), sg("sg-central")]
    relacoes = protege("sg-central", "central")
    regras_ = []
    for i in range(5):
        recursos += [vm(f"app{i}", tags={"app": f"app{i}"}), sg(f"sg-app{i}")]
        relacoes += protege(f"sg-app{i}", f"app{i}")
        regras_.append(regra("sg-central", 5432, de_grupo=f"sg-app{i}"))
    a = analisar_topologia(inventario(recursos, relacoes, regras_))
    assert "grupo_acoplado_grande" in _regras(a)


# ── ondas ───────────────────────────────────────────────────────────────


def _cadeia():
    """web chama api, api chama auth; relatorio fica sozinho."""
    nomes = ("web", "api", "auth", "relatorio")
    recursos = [vm(n, tags={"app": n}) for n in nomes] + [sg(f"sg-{n}") for n in nomes]
    relacoes = [r for n in nomes for r in protege(f"sg-{n}", n)]
    regras_ = [regra("sg-api", 8080, de_grupo="sg-web"), regra("sg-auth", 8080, de_grupo="sg-api")]
    return recursos, relacoes, regras_


def test_ondas_dependencia_primeiro_e_independente_a_parte() -> None:
    a = analisar_topologia(inventario(*_cadeia()))
    ondas = [(o.numero, [_nome(a, g) for g in o.grupos]) for o in a.ondas]
    assert ondas == [(1, ["auth"]), (2, ["api"]), (3, ["web"])]
    assert [_nome(a, g) for g in a.independentes] == ["relatorio"]
    entre = {(_nome(a, e.de_grupo), _nome(a, e.para_grupo)) for e in a.dependencias_entre_ondas}
    assert entre == {("web", "api"), ("api", "auth")}
    assert all(e.portas == ("tcp/8080",) for e in a.dependencias_entre_ondas)


def test_ciclo_migra_na_mesma_onda() -> None:
    recursos, relacoes, regras_ = _cadeia()
    regras_.append(regra("sg-web", 8080, de_grupo="sg-auth"))  # auth chama web: ciclo
    a = analisar_topologia(inventario(recursos, relacoes, regras_))
    assert len(a.ondas) == 1
    assert "ciclo" in _regras(a)
    assert a.dependencias_entre_ondas == ()


# ── criticidade ─────────────────────────────────────────────────────────


def test_criticidade_herdada_de_quem_depende() -> None:
    recursos, relacoes, regras_ = _cadeia()
    recursos[0] = vm("web", tags={"app": "web", "criticality": "Critical"})
    a = analisar_topologia(inventario(recursos, relacoes, regras_))
    g = _grupos_por_nome(a)
    assert g["web"].criticidade.efetiva is Nivel.ALTA
    assert g["web"].criticidade.origem == "tag:criticality=Critical"
    assert g["auth"].criticidade.efetiva is Nivel.ALTA
    assert g["auth"].criticidade.declarada is None
    assert g["auth"].criticidade.origem == "herdada de web"
    assert g["relatorio"].criticidade.origem == "nao_declarada"
    assert g["auth"].raio_de_impacto == 2
    assert g["web"].raio_de_impacto == 0


def test_precedencia_recurso_tag_aplicacao() -> None:
    inv = inventario(
        [
            vm("a", tags={"app": "x", "criticality": "low"}),
            vm("b", tags={"app": "y", "criticidade": "média"}),
            vm("c", tags={"app": "z", "criticality": "urgente"}),
        ]
    )
    decl = Declaracoes(
        criticidade_recursos={"a": Nivel.ALTA},
        criticidade_aplicacoes={"y": Nivel.ALTA, "z": Nivel.MEDIA},
    )
    a = analisar_topologia(inv, decl)
    g = _grupos_por_nome(a)
    assert (g["x"].criticidade.efetiva, g["x"].criticidade.origem) == (Nivel.ALTA, "declarada")
    # A tag do recurso é mais específica que a declaração da aplicação.
    assert g["y"].criticidade.efetiva is Nivel.MEDIA
    # Valor fora do vocabulário: ignorado, cai na declaração da aplicação.
    assert g["z"].criticidade.efetiva is Nivel.MEDIA
    assert {p.campo for p in a.premissas} >= {"criticidade_tag_fora_do_vocabulario"}


def test_criticidade_por_aplicacao_sem_chave_e_ignorada_com_motivo() -> None:
    a = analisar_topologia(
        inventario([vm("a")]),
        Declaracoes(chave_aplicacao="", criticidade_aplicacoes={"x": Nivel.ALTA}),
    )
    assert a.grupos[0].criticidade.efetiva is None
    assert "criticidade_por_aplicacao_sem_chave" in {p.campo for p in a.premissas}


def test_ferramenta_nao_propaga_criticidade() -> None:
    rec, rels = camadas_app_banco("a", tags=False)
    rec2, rels2 = camadas_app_banco("b", tags=False)
    inv = inventario(
        [*rec, *rec2, vm("bastion", tags={"criticality": "high"}), sg("sg-bastion")],
        [*rels, *rels2, *protege("sg-bastion", "bastion")],
        [
            regra("sg-a-app", 22, de_grupo="sg-bastion"),
            regra("sg-b-app", 22, de_grupo="sg-bastion"),
        ],
    )
    a = analisar_topologia(inv)
    outras = [g for g in a.grupos if g.id != a.grupo_de[a.unidade_de["bastion"]]]
    assert all(g.criticidade.efetiva is None for g in outras)


# ── riscos e ociosos ────────────────────────────────────────────────────


def test_exposicoes() -> None:
    inv = inventario(
        [vm("web"), banco("db", publico=True), sg("sg-web"), sg("sg-db"), sg("sg-tudo"), vm("x")],
        [*protege("sg-web", "web"), *protege("sg-db", "db"), *protege("sg-tudo", "x")],
        [
            regra("sg-web", 443, internet=True),
            regra("sg-web", 22, internet=True),
            regra("sg-db", 5432, internet=True),
            regra("sg-tudo", None, internet=True, protocolo="todos"),
        ],
    )
    r = _regras(analisar_topologia(inv))
    assert r["exposicao_administrativa"].severidade is Severidade.ALTA
    assert r["exposicao_administrativa"].recursos == ("web",)
    assert r["banco_exposto"].severidade is Severidade.ALTA
    assert r["porta_ampla_aberta"].recursos == ("x",)
    # HTTPS aberto para a internet é exposição normal, não risco.
    assert "443" not in r["exposicao_administrativa"].detalhe


def test_ponto_unico_e_critico_sem_ha() -> None:
    rec_a, rels_a = camadas_app_banco("auth")
    rec_c, rels_c = camadas_app_banco("checkout")
    rec_a[0] = vm("vm-auth", tags={"app": "auth", "criticality": "high"})
    inv = inventario(
        [*rec_a, *rec_c],
        [*rels_a, *rels_c],
        [regra("sg-auth-dados", 5432, de_grupo="sg-checkout-app")],
    )
    r = _regras(analisar_topologia(inv))
    # Só o banco do auth serve a duas unidades.
    assert r["ponto_unico_de_falha"].recursos == ("db-auth",)
    # auth e checkout viram um grupo (banco compartilhado), e o grupo é crítico:
    # os dois bancos sem alta disponibilidade entram.
    assert r["critico_sem_ha"].recursos == ("db-auth", "db-checkout")


def test_dependencia_entre_ambientes() -> None:
    inv = inventario(
        [
            vm("p", tags={"app": "a", "env": "prod"}),
            vm("d", tags={"app": "b", "env": "dev"}),
            sg("sg-p"),
            sg("sg-d"),
        ],
        [*protege("sg-p", "p"), *protege("sg-d", "d")],
        [regra("sg-d", 8080, de_grupo="sg-p")],
    )
    r = _regras(analisar_topologia(inv))
    assert r["dependencia_entre_ambientes"].severidade is Severidade.MEDIA
    assert "a (prod) → b (dev)" in r["dependencia_entre_ambientes"].detalhe


def test_sem_regras_e_ip_publico() -> None:
    r = _regras(analisar_topologia(inventario([vm("a", ip_publico="1.1.1.1")])))
    assert "sem_regras" in r
    assert r["recurso_com_ip_publico"].recursos == ("a",)


def test_ociosos() -> None:
    inv = inventario(
        [
            recurso("disco-solto", T.DISCO, estado=EstadoDoRecurso.DISPONIVEL, tamanho_gb=500),
            vm("parada", estado=EstadoDoRecurso.PARADO),
            recurso("disco-da-parada", T.DISCO, tamanho_gb=100),
            recurso("ip-solto", T.IP_PUBLICO, estado=EstadoDoRecurso.DISPONIVEL),
            recurso("snap", T.SNAPSHOT, tamanho_gb=50),
        ],
        [rel("disco-da-parada", "parada", R.ANEXADO_A)],
    )
    ociosos = {(o.recurso, o.motivo, o.tamanho_gb) for o in analisar_topologia(inv).ociosos}
    assert ociosos == {
        ("disco-solto", "disco_sem_anexo", 500),
        ("parada", "instancia_parada", 100),
        ("ip-solto", "ip_sem_associacao", None),
        ("snap", "snapshot_sem_disco", 50),
    }


def test_resumo_e_fingerprint() -> None:
    inv = inventario(*_cadeia())
    a = analisar_topologia(inv)
    assert a.resumo["unidades"] == 4
    assert a.resumo["ondas"] == 3
    assert a.resumo["dependencias"] == {"servico": 2}
    assert a.fingerprint == analisar_topologia(inv).fingerprint
    outra = analisar_topologia(inv, Declaracoes(chave_aplicacao=""))
    assert outra.fingerprint != a.fingerprint
