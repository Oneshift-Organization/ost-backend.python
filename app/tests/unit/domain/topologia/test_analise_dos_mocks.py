"""A análise inteira sobre os quatro mocks de inventário (I4).

Os mocks foram montados para exercitar cada regra; aqui se confere que cada
regra de fato disparou onde devia, e que a saída é determinística.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from app.domain.entities.topologia import UNIDADE
from app.domain.services.topologia import Declaracoes, analisar_topologia
from app.domain.services.topologia.modelo import Nivel
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos


@pytest.fixture(scope="module")
def analises(mocks_inventario):
    resultado = {}
    for provedor in ("aws", "gcp", "azure", "oci"):
        arquivos = getattr(mocks_inventario, provedor)
        inv = normalizar_arquivos([(a.name, a) for a in arquivos]).inventario
        resultado[provedor] = (inv, analisar_topologia(inv))
    return resultado


@pytest.mark.parametrize("provedor", ["aws", "gcp", "azure", "oci"])
def test_toda_unidade_cobre_os_recursos_de_papel_unidade(analises, provedor) -> None:
    inv, a = analises[provedor]
    de_unidade = {r.chave for r in inv.recursos if r.tipo in UNIDADE}
    assert de_unidade <= set(a.unidade_de)
    assert len({u.nome for u in a.unidades}) == len(a.unidades)  # nomes distinguíveis
    em_grupo = {u for g in a.grupos for u in g.unidades}
    assert em_grupo == {u.id for u in a.unidades}


def test_aws_dispara_cada_regra_montada_no_mock(analises, mocks_inventario) -> None:
    _, a = analises["aws"]
    encontradas = {r.regra for r in a.riscos}
    esperadas = mocks_inventario.resumos["aws"].riscos_esperados
    assert esperadas <= encontradas, esperadas - encontradas


def test_aws_ondas_seguem_as_chamadas(analises) -> None:
    _, a = analises["aws"]
    onda = {}
    for o in a.ondas:
        for g in o.grupos:
            onda[next(x.nome for x in a.grupos if x.id == g)] = o.numero
    grupo_auth = next(n for n in onda if n.startswith("auth (prod) · us-east-1"))
    # checkout e pagamentos presos ao banco do auth; relatórios depois da ingestão.
    assert "checkout (prod)" in grupo_auth and "pagamentos (prod)" in grupo_auth
    assert onda["relatorios (prod) · us-east-1"] > onda["ingestao (prod) · us-east-1"]
    # O monitoramento acessa os bancos de todos: fica por último.
    assert onda["monitoramento (prod) · us-east-1"] == max(onda.values())


def test_aws_criticidade_propagada(analises) -> None:
    _, a = analises["aws"]
    g = {x.nome: x for x in a.grupos}
    catalogo = g["catalogo (prod)"]
    assert catalogo.criticidade.efetiva is Nivel.ALTA
    assert catalogo.criticidade.origem.startswith("herdada de auth (prod)")


def test_gcp_e_azure_tem_dependencias_e_so_na_mesma_regiao(analises) -> None:
    for provedor in ("gcp", "azure"):
        _, a = analises[provedor]
        assert a.dependencias, provedor
        unidades = {u.id: u for u in a.unidades}
        for d in a.dependencias:
            assert unidades[d.de].regiao == unidades[d.para].regiao, provedor


def test_oci_balanceador_depois_das_aplicacoes(analises) -> None:
    _, a = analises["oci"]
    onda_de = {g: o.numero for o in a.ondas for g in o.grupos}
    lbs = [g.id for g in a.grupos if g.nome.startswith("lb")]
    outros = [onda_de[g] for g in onda_de if g not in lbs]
    assert lbs and all(onda_de[lb] == max([*outros, onda_de[lb]]) for lb in lbs)


@pytest.mark.parametrize("provedor", ["aws", "gcp", "azure", "oci"])
def test_determinismo_com_entrada_embaralhada(analises, provedor) -> None:
    inv, a = analises[provedor]
    rng = random.Random(3)
    recursos, relacoes, regras = list(inv.recursos), list(inv.relacoes), list(inv.regras)
    rng.shuffle(recursos)
    rng.shuffle(relacoes)
    rng.shuffle(regras)
    embaralhado = dataclasses.replace(
        inv, recursos=tuple(recursos), relacoes=tuple(relacoes), regras=tuple(regras)
    )
    b = analisar_topologia(embaralhado)
    assert b == a


def test_declaracoes_mudam_o_fingerprint_mas_nao_o_inventario(analises) -> None:
    inv, a = analises["aws"]
    b = analisar_topologia(inv, Declaracoes(ferramentas=("aplicacao:monitoramento",)))
    assert b.fingerprint != a.fingerprint
    assert len(b.ferramentas) > len(a.ferramentas)
