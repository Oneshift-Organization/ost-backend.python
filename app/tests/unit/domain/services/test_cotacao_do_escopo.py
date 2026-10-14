"""Custo no destino de um escopo de migração (PLANO_ESCOPO_DE_MIGRACAO, E2)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.entities.topologia import TipoDeRecurso
from app.domain.services.cotacao_do_escopo import (
    CATEGORIA,
    MEDIA_DOS_POOLS,
    POOL,
    POOL_DA_FAMILIA,
    LinhaDoEscopo,
    PoolCotado,
    cotar_escopo,
)

D = Decimal
_POOLS = (
    # M7G na Virgínia: OCI cota pela metade, Azure pelo dobro
    PoolCotado("M7G", "us-east-1", D("1000"), {"oci": D("500"), "azure": D("2000")}),
    # C7G em São Paulo: 1,5x na OCI
    PoolCotado("C7G", "sa-east-1", D("200"), {"oci": D("300")}),
)
_CATEGORIAS = {"oci": {"banco": D("0.5"), "ssd": D("0.3"), "hdd": D("0.6"), "objetos": D("1.1")}}


def _linha(servico: str, custo: str, tipo: TipoDeRecurso | None, **kw: object) -> LinhaDoEscopo:
    return LinhaDoEscopo(servico=servico, custo=D(custo), tipo=tipo, **kw)  # type: ignore[arg-type]


def _por_servico(linhas: list[LinhaDoEscopo]) -> dict[str, tuple[Decimal | None, tuple[str, ...]]]:
    r = cotar_escopo(linhas, _POOLS, _CATEGORIAS, ("oci",))
    return {s.servico: (s.destino["oci"], s.metodos) for s in r.por_servico}


def test_instancia_usa_a_taxa_do_pool_da_familia_e_regiao_dela() -> None:
    r = _por_servico(
        [_linha("EC2", "100", TipoDeRecurso.INSTANCIA, familia="M7G", regiao="us-east-1")]
    )
    assert r["EC2"] == (D("50"), (POOL,))


def test_sem_pool_na_regiao_usa_a_familia_e_depois_a_media() -> None:
    em_outra_regiao = _linha(
        "EC2", "100", TipoDeRecurso.INSTANCIA, familia="M7G", regiao="eu-west-1"
    )
    sem_familia = _linha("EC2b", "100", TipoDeRecurso.INSTANCIA, familia="X9", regiao="us-east-1")
    r = _por_servico([em_outra_regiao, sem_familia])
    assert r["EC2"] == (D("50"), (POOL_DA_FAMILIA,))
    # Média ponderada pelo custo de origem: (500 + 300) / (1000 + 200)
    assert r["EC2b"][1] == (MEDIA_DOS_POOLS,)
    assert r["EC2b"][0] == D("100") * D("800") / D("1200")


def test_disco_banco_e_bucket_usam_a_razao_da_categoria() -> None:
    r = _por_servico(
        [
            _linha("EBS", "100", TipoDeRecurso.DISCO, tipo_disco="gp3"),
            _linha("EBS-frio", "100", TipoDeRecurso.DISCO, tipo_disco="st1"),
            _linha("RDS", "100", TipoDeRecurso.BANCO),
            _linha("ElastiCache", "100", TipoDeRecurso.CACHE),
            _linha("S3", "100", TipoDeRecurso.BUCKET),
        ]
    )
    assert r["EBS"] == (D("30.0"), (CATEGORIA,))
    assert r["EBS-frio"] == (D("60.0"), (CATEGORIA,))
    assert r["RDS"] == (D("50.0"), (CATEGORIA,))
    assert r["ElastiCache"] == (D("50.0"), (CATEGORIA,))
    assert r["S3"] == (D("110.0"), (CATEGORIA,))


def test_o_que_nao_tem_cotacao_fica_fora_da_comparacao_e_declarado() -> None:
    r = cotar_escopo(
        [
            _linha("EC2", "100", TipoDeRecurso.INSTANCIA, familia="M7G", regiao="us-east-1"),
            _linha("ELB", "40", TipoDeRecurso.BALANCEADOR),
        ],
        _POOLS,
        _CATEGORIAS,
        ("oci", "gcp"),
    )
    oci = next(t for t in r.por_provedor if t.provedor == "oci")
    assert (oci.custo_estimado, oci.custo_origem_cotado, oci.sem_cotacao) == (
        D("50"),
        D("100"),
        D("40"),
    )
    # Provedor sem pool nem categoria: nada cotado, tudo declarado
    gcp = next(t for t in r.por_provedor if t.provedor == "gcp")
    assert (gcp.custo_estimado, gcp.sem_cotacao) == (D(0), D("140"))
    elb = next(s for s in r.por_servico if s.servico == "ELB")
    assert (elb.destino, elb.metodos) == ({"oci": None, "gcp": None}, ())
