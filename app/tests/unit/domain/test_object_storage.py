"""Armazenamento de objetos é uma categoria própria.

Antes disto, `TimedStorage-ByteHrs` (a linha de armazenamento do S3) e o
`Cloud Storage` inteiro do GCP caíam em `storage_hdd`. O erro não era só de
rótulo: a reprecificação cross-cloud usa a taxa da categoria, e disco custa
~$0,045/GB-mês contra ~$0,023 de objeto. Um bucket era comparado com disco no
destino, e o GB estimado saía com o dobro do erro.

Para o Terraform importa ainda mais: um bucket não é um volume anexado a
instância. Gerar `google_compute_disk` para uma linha de Cloud Storage produziria
um `.tf` que descreve outra infraestrutura.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.entities.workload_pool import AuxiliaryCosts
from app.infrastructure.database.seed_category_rates import category_rate_points
from app.infrastructure.database.seed_classification_rules import (
    AWS_RULES,
    GCP_RULES,
)

# ── a categoria existe e é contada ──────────────────────────────────────


def test_o_balde_existe() -> None:
    aux = AuxiliaryCosts(object_storage=Decimal("1200"))
    assert aux.object_storage == Decimal("1200")


def test_custo_so_de_objeto_nao_passa_por_vazio() -> None:
    """`any_positive` decide se a seção não-compute entra na análise. Sem o
    balde novo, um bill que só tem bucket parecia não ter custo auxiliar."""
    assert AuxiliaryCosts(object_storage=Decimal("0.01")).any_positive


# ── classificação ───────────────────────────────────────────────────────


def _regra(regras: list, padrao: str) -> tuple:
    achadas = [r for r in regras if r[0] == padrao]
    assert achadas, f"regra {padrao!r} sumiu"
    return achadas[0]


def test_timedstorage_da_aws_e_objeto_nao_disco() -> None:
    """`TimedStorage-ByteHrs` É o S3. Ia para `storage_hdd`."""
    assert _regra(AWS_RULES, "*timedstorage*")[2] == "object_storage"


def test_glacier_e_s3_prefixado_sao_objeto() -> None:
    assert _regra(AWS_RULES, "*glacier*")[2] == "object_storage"
    assert _regra(AWS_RULES, "s3:*")[2] == "object_storage"


def test_cloud_storage_do_gcp_e_objeto() -> None:
    for padrao in ("*cloud storage*", "*nearline*", "*coldline*"):
        assert _regra(GCP_RULES, padrao)[2] == "object_storage"


def test_objeto_e_avaliado_antes_do_catch_all_de_storage() -> None:
    """`("*storage*", → storage_hdd, 53)` levava o Cloud Storage inteiro. As
    regras de objeto precisam rodar antes — prioridade menor roda primeiro."""
    catch_all = _regra(GCP_RULES, "*storage*")[5]
    for padrao in ("*cloud storage*", "*nearline*", "*coldline*", "*archive storage*"):
        assert _regra(GCP_RULES, padrao)[5] < catch_all, padrao


def test_disco_continua_disco() -> None:
    """A correção não pode arrastar bloco junto: EBS e PD seguem onde estavam."""
    assert _regra(AWS_RULES, "*volumeusage*")[2] == "storage_ssd"
    # `*ebs:*` (com prefixo de região) desde a E5 do PLANO_CUR_BIGQUERY.
    assert _regra(AWS_RULES, "*ebs:*")[2] == "storage_ssd"
    assert _regra(GCP_RULES, "*pd capacity*")[2] == "storage_hdd"
    assert _regra(GCP_RULES, "*ssd*")[2] == "storage_ssd"


def test_prioridades_nao_colidem() -> None:
    """Empate em prioridade deixa a ordem à sorte do banco, e a escada de
    classificação existe justamente para não depender disso."""
    for nome, regras in (("AWS", AWS_RULES), ("GCP", GCP_RULES)):
        prioridades = [r[5] for r in regras]
        duplicadas = {p for p in prioridades if prioridades.count(p) > 1}
        assert not duplicadas, f"{nome}: prioridades repetidas {sorted(duplicadas)}"


# ── taxas ───────────────────────────────────────────────────────────────


def test_todo_provedor_tem_taxa_de_objeto() -> None:
    """Sem taxa não há reprecificação: a categoria seria ignorada no destino."""
    pontos = [p for p in category_rate_points() if p.service_type == "object_storage"]
    assert {p.provider for p in pontos} == {"aws", "gcp", "azure", "oci"}
    assert all(p.unit == "gb_month" for p in pontos)


def test_objeto_e_mais_barato_que_bloco_em_todo_provedor() -> None:
    """É a diferença que tornava a classificação errada cara. Se algum dia
    inverter, é erro de digitação no seed."""
    taxas = {
        (p.provider, p.service_type): p.price
        for p in category_rate_points()
        if p.service_type in ("object_storage", "storage_ssd")
    }
    for provedor in ("aws", "gcp", "azure"):
        assert taxas[(provedor, "object_storage")] < taxas[(provedor, "storage_ssd")]


# ── o que NÃO é armazenamento ───────────────────────────────────────────
#
# Estes dois casos vieram de olhar os SKUs reais dos CSVs versionados depois da
# mudança, não de raciocínio abstrato.


def test_requisicao_de_api_nao_e_armazenamento() -> None:
    """`*request*` pega `RequestV2-Tier1` do WAF ($2,7 mil no bill real) e
    `Requests-FIFO` do SQS, não só o S3.

    Como `storage_hdd` inflava ~66 mil GB de disco; como `object_storage` ficava
    pior, porque a taxa menor gerava um bucket de 130 mil GB. Requisição é
    operação, não capacidade — em nenhum dos dois ela cabe.
    """
    assert _regra(AWS_RULES, "*request*")[2] == "unclassified"


def test_artifact_registry_nao_e_disco() -> None:
    """$324/mês somavam ~8 mil GB de disco fantasma ao dimensionamento."""
    regra = _regra(GCP_RULES, "*artifact registry*")
    assert regra[2] == "object_storage"
    assert regra[5] < _regra(GCP_RULES, "*storage*")[5], "precisa vir antes do catch-all"
