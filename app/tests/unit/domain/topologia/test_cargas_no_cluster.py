"""Prováveis aplicações dentro do cluster, pelos rastros no inventário (opção A)."""

from __future__ import annotations

from app.domain.services.topologia import analisar_topologia
from app.domain.services.topologia.cargas_no_cluster import cargas_no_cluster
from app.tests.unit.domain.topologia.construtores import R, T, inventario, lb, recurso, rel, vm


def _comp(nome: str, componente: str, **kw):
    return recurso(nome, T.OUTRO, componente=componente, **kw)


def _conta():
    recursos = [
        recurso("eks-loja", T.CLUSTER_KUBERNETES),
        vm("i-no"),
        lb("k8s-kong", tags={"service.k8s.aws/stack": "kong/kong-kong-proxy"}),
        _comp("acme-erp-venda", "registro_de_imagens"),
        _comp("acme-erp-estoque", "registro_de_imagens"),
        _comp("acme-kit-empresa", "registro_de_imagens"),
        _comp("sm-acme-erp-venda-prd", "segredo"),
        _comp("sm-acme-erp-estoque-prd", "segredo"),
        _comp("secret-rds-loja-prd", "segredo"),  # credencial: segredo sozinho não é carga
        _comp("empresa", "descoberta_de_servico"),
        _comp("keycloak", "descoberta_de_servico"),
        recurso(
            "pv-1", T.DISCO, tags={"kubernetes.io/created-for/pvc/namespace": "observabilidade"}
        ),
        _comp("outra-conta-api", "registro_de_imagens", conta="c2"),
    ]
    relacoes = [rel("i-no", "eks-loja", R.MEMBRO_DE), rel("k8s-kong", "eks-loja", R.MEMBRO_DE)]
    return inventario(recursos, relacoes)


def test_rastros_viram_cargas_provaveis_do_cluster() -> None:
    inv = _conta()
    analise = analisar_topologia(inv)
    [cluster] = cargas_no_cluster(inv, analise).values()
    nomes = {c.nome: set(c.fontes) for c in cluster.cargas}
    assert cluster.cluster == "eks-loja"
    # Prefixo comum (`acme`) sai; linha de produto (`erp`, `kit`) fica
    assert nomes["erp-venda"] == {"imagem", "segredo"}
    assert nomes["erp-estoque"] == {"imagem", "segredo"}
    # `empresa` do service discovery é a mesma carga de `kit-empresa`
    assert nomes["kit-empresa"] == {"imagem", "service discovery"}
    assert nomes["keycloak"] == {"service discovery"}
    assert nomes["kong-proxy"] == {"balanceador"}
    assert "rds-loja" not in nomes
    assert "outra-conta-api" not in " ".join(nomes)
    assert set(cluster.namespaces) == {"kong", "observabilidade"}


def test_sem_cluster_nao_ha_cargas() -> None:
    inv = inventario([vm("a"), _comp("acme-api", "registro_de_imagens")])
    assert cargas_no_cluster(inv, analisar_topologia(inv)) == {}
