"""Algoritmos de grafo da topologia (I1 do PLANO_INVENTARIO_API).

Além dos casos pontuais, uma comparação contra força bruta em grafos
aleatórios pequenos: componentes pela alcançabilidade mútua, camada pela
definição recursiva, dependentes pela alcançabilidade reversa.
"""

from __future__ import annotations

import random

import pytest

from app.domain.services.grafo import (
    UniaoBusca,
    camadas,
    condensar,
    dependentes_transitivos,
)

# ── união-busca ─────────────────────────────────────────────────────────


def test_representante_e_a_menor_chave_em_qualquer_ordem() -> None:
    a = UniaoBusca()
    a.unir("c", "b")
    a.unir("b", "a")
    b = UniaoBusca()
    b.unir("a", "b")
    b.unir("b", "c")
    assert a.conjuntos() == b.conjuntos() == {"a": ("a", "b", "c")}


def test_elementos_isolados_viram_conjuntos_proprios() -> None:
    u = UniaoBusca(["x", "y"])
    u.unir("z", "y")
    assert u.conjuntos() == {"x": ("x",), "y": ("y", "z")}
    assert "z" in u
    assert "w" not in u


def test_unir_duas_vezes_nao_muda_nada() -> None:
    u = UniaoBusca()
    assert u.unir("b", "a") == "a"
    assert u.unir("a", "b") == "a"
    assert u.conjuntos() == {"a": ("a", "b")}


def test_caminho_longo_e_comprimido() -> None:
    u = UniaoBusca()
    for i in range(500):
        u.unir(f"n{i:04d}", f"n{i + 1:04d}")
    assert u.raiz("n0500") == "n0000"
    assert len(u.conjuntos()["n0000"]) == 501


# ── componentes fortemente conexos ──────────────────────────────────────


def test_grafo_vazio() -> None:
    cond = condensar([], [])
    assert cond.componentes == ()
    assert cond.arestas == frozenset()
    assert camadas([], []) == {}
    assert dependentes_transitivos([], []) == {}


def test_ciclo_vira_um_componente_e_autolaco_nao_junta_ninguem() -> None:
    cond = condensar(["a", "b", "c", "d"], [("a", "b"), ("b", "a"), ("c", "c"), ("c", "a")])
    assert cond.componentes == (("a", "b"), ("c",), ("d",))
    assert cond.arestas == frozenset({(1, 0)})


def test_no_citado_so_em_aresta_entra_no_grafo() -> None:
    cond = condensar([], [("x", "y")])
    assert cond.componentes == (("x",), ("y",))


def test_cadeia_profunda_nao_estoura_a_pilha() -> None:
    n = 5000
    nos = [f"n{i:05d}" for i in range(n)]
    arestas = [(nos[i], nos[i + 1]) for i in range(n - 1)] + [(nos[-1], nos[0])]
    cond = condensar(nos, arestas)
    assert len(cond.componentes) == 1


# ── camadas (ondas) ─────────────────────────────────────────────────────


def test_dependencia_fica_antes_de_quem_depende() -> None:
    # web -> api -> banco ; relatorio -> banco
    arestas = [("web", "api"), ("api", "banco"), ("relatorio", "banco")]
    assert camadas(["web", "api", "banco", "relatorio", "solto"], arestas) == {
        "api": 2,
        "banco": 1,
        "relatorio": 2,
        "solto": 1,
        "web": 3,
    }


def test_ciclo_compartilha_camada_e_empurra_quem_depende_dele() -> None:
    arestas = [("a", "b"), ("b", "a"), ("c", "a"), ("b", "d")]
    assert camadas(["a", "b", "c", "d"], arestas) == {"a": 2, "b": 2, "c": 3, "d": 1}


def test_camada_e_o_caminho_mais_longo_nao_o_mais_curto() -> None:
    # x depende de y direto e também de z, que depende de y.
    arestas = [("x", "y"), ("x", "z"), ("z", "y")]
    assert camadas(["x", "y", "z"], arestas)["x"] == 3


# ── dependentes transitivos (raio de impacto) ───────────────────────────


def test_dependentes_transitivos() -> None:
    arestas = [("web", "api"), ("api", "banco"), ("relatorio", "banco")]
    dep = dependentes_transitivos(["web", "api", "banco", "relatorio"], arestas)
    assert dep["banco"] == {"api", "web", "relatorio"}
    assert dep["api"] == {"web"}
    assert dep["web"] == frozenset()


def test_membros_do_ciclo_dependem_uns_dos_outros() -> None:
    dep = dependentes_transitivos(["a", "b", "c"], [("a", "b"), ("b", "a"), ("c", "a")])
    assert dep["a"] == {"b", "c"}
    assert dep["b"] == {"a", "c"}


# ── contra força bruta ──────────────────────────────────────────────────


def _alcance(nos: list[str], arestas: list[tuple[str, str]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {n: set() for n in nos}
    for u, v in arestas:
        adj[u].add(v)
    fecho: dict[str, set[str]] = {}
    for n in nos:
        vistos: set[str] = set()
        fila = list(adj[n])
        while fila:
            x = fila.pop()
            if x in vistos:
                continue
            vistos.add(x)
            fila.extend(adj[x])
        fecho[n] = vistos
    return fecho


@pytest.mark.parametrize("semente", range(40))
def test_bate_com_forca_bruta(semente: int) -> None:
    rng = random.Random(semente)
    nos = [f"n{i}" for i in range(rng.randint(1, 12))]
    arestas = [(rng.choice(nos), rng.choice(nos)) for _ in range(rng.randint(0, len(nos) * 2))]
    alcance = _alcance(nos, arestas)

    cond = condensar(nos, arestas)
    for u in nos:
        for v in nos:
            juntos = u == v or (v in alcance[u] and u in alcance[v])
            assert (cond.componente_de[u] == cond.componente_de[v]) is juntos

    camada = camadas(nos, arestas)
    for u in nos:
        deps = {
            v for (x, v) in arestas if x == u and cond.componente_de[v] != cond.componente_de[u]
        }
        # Mesmo componente: mesma camada; cada dependência externa, camada menor.
        for v in deps:
            assert camada[u] > camada[v]
        irmaos = [m for m in cond.componentes[cond.componente_de[u]]]
        externas = {
            v
            for m in irmaos
            for (x, v) in arestas
            if x == m and cond.componente_de[v] != cond.componente_de[u]
        }
        assert camada[u] == 1 + max((camada[v] for v in externas), default=0)

    dependentes = dependentes_transitivos(nos, arestas)
    for v in nos:
        esperado = {u for u in nos if u != v and v in alcance[u]}
        assert dependentes[v] == esperado


def test_ordem_de_entrada_nao_muda_a_saida() -> None:
    rng = random.Random(7)
    nos = [f"n{i}" for i in range(30)]
    arestas = [(rng.choice(nos), rng.choice(nos)) for _ in range(60)]
    base = (condensar(nos, arestas), camadas(nos, arestas), dependentes_transitivos(nos, arestas))
    for _ in range(5):
        rng.shuffle(nos)
        rng.shuffle(arestas)
        assert (
            condensar(nos, arestas),
            camadas(nos, arestas),
            dependentes_transitivos(nos, arestas),
        ) == base
