"""Enriquecimento com exports detalhados (E5 do PLANO_CUR_BIGQUERY).

Repositórios falsos: o que se testa é a decisão do use case (descritor,
natureza, rota do compromisso, o que segue para o agregador), não o banco.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.entities.billing_item import BillingItem
from app.domain.entities.billing_report import BillingReport
from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.value_objects.cloud_provider import CloudProvider
from app.use_cases.analysis import leitura_completa
from app.use_cases.analysis.build_billing_enriched import (
    BuildBillingEnrichedInput,
    BuildBillingEnrichedUseCase,
)
from app.use_cases.analysis.leitura_completa import listar_todos

_USER = UUID("11111111-1111-1111-1111-111111111111")


class _Relatorios:
    def __init__(self, report: BillingReport) -> None:
        self._report = report

    async def get_by_id(self, report_id, user_id):
        return self._report


class _Itens:
    def __init__(self, itens: list[BillingItem]) -> None:
        self.itens = itens
        self.chamadas = 0

    async def list_by_report(self, report_id, user_id, *, limit=100, offset=0):
        self.chamadas += 1
        return self.itens[offset : offset + limit]


class _Enriquecidos:
    def __init__(self) -> None:
        self.gravados = []

    async def delete_by_report(self, report_id, user_id):
        return 0

    async def bulk_insert(self, itens):
        self.gravados.extend(itens)
        return len(itens)


class _Catalogo:
    async def list_by_provider(self, provider):
        return []


class _Regras:
    async def list_active_by_provider(self, provider):
        return [
            SkuClassificationRule(
                "AWS", "*boxusage:*", "glob", "core", confidence="high", priority=20
            ),
            # A escada casaria o compromisso como `core` — é justamente o que a
            # rota pela natureza precisa impedir.
            SkuClassificationRule("AWS", "computesp:*", "glob", "core", priority=24),
        ]


class _Orfaos:
    def __init__(self) -> None:
        self.gravados = []

    async def bulk_insert(self, itens):
        self.gravados.extend(itens)
        return len(itens)


def _item(**extra) -> BillingItem:
    base = {
        "user_id": _USER,
        "report_id": uuid4(),
        "provider": CloudProvider.AWS,
        "service": "AmazonEC2",
        "cost": Decimal("10"),
        "currency": "USD",
        "billing_period": "2026-03",
        "usage_amount": Decimal("744"),
        "usage_unit": "hours",
        "tags": {"raw_payload": {}},
    }
    base.update(extra)
    return BillingItem(**base)


async def _enriquecer(itens: list[BillingItem]):
    report = BillingReport(user_id=_USER, source_filename="cur.csv.gz")
    report.mark_processing()
    enriquecidos = _Enriquecidos()
    orfaos = _Orfaos()
    uc = BuildBillingEnrichedUseCase(
        _Relatorios(report), _Itens(itens), enriquecidos, _Catalogo(), _Regras(), orfaos
    )
    await uc.execute(BuildBillingEnrichedInput(user_id=_USER, report_id=report.id))
    return enriquecidos.gravados, orfaos.gravados


async def test_descritor_vem_do_usage_type() -> None:
    # Sem `usage_type`, o descritor caía no nome do serviço: o EC2 inteiro
    # virava "AmazonEC2" e nada casava por tipo de instância.
    [linha], _ = await _enriquecer([_item(usage_type="BoxUsage:m7g.2xlarge", charge_kind="usage")])
    assert linha.usage_type == "BoxUsage:m7g.2xlarge"
    assert linha.component_type == "core"


@pytest.mark.parametrize("natureza", ["tax", "credit", "refund", "adjustment"])
async def test_fora_da_base_nao_entra(natureza: str) -> None:
    gravados, orfaos = await _enriquecer(
        [
            _item(usage_type="", charge_kind=natureza, cost=Decimal("-5")),
            _item(usage_type="BoxUsage:m7g.large", charge_kind="usage"),
        ]
    )
    assert [g.usage_type for g in gravados] == ["BoxUsage:m7g.large"]
    assert orfaos == []


async def test_compromisso_vai_pela_natureza_e_nao_pela_escada() -> None:
    [linha], orfaos = await _enriquecer(
        [_item(usage_type="ComputeSP:3yrNoUpfront", charge_kind="commitment")]
    )
    assert linha.component_type == "commitment"
    assert linha.match_layer == "LN"
    assert linha.mapping_confidence == "high"
    assert linha.category == "commitment"
    assert orfaos == []


async def test_recurso_e_atributos_seguem_para_o_agregador() -> None:
    [linha], _ = await _enriquecer(
        [
            _item(
                usage_type="BoxUsage:m7g.2xlarge",
                charge_kind="usage",
                resource_id="i-0abc",
                resource_type="m7g.2xlarge",
                attributes={"vcpus": "8", "memory_gib": "32", "project_id": "222222222222"},
            )
        ]
    )
    assert linha.resource_id == "i-0abc"
    assert linha.project_id == "222222222222"
    assert linha.attributes == {
        "vcpus": "8",
        "memory_gib": "32",
        "project_id": "222222222222",
        "instance_type": "m7g.2xlarge",
        "charge_kind": "usage",
    }


async def test_legado_sem_natureza_segue_pelo_raw_payload() -> None:
    gravados, _ = await _enriquecer(
        [
            _item(tags={"raw_payload": {"UsageType": "BoxUsage:c7g.large", "Project ID": "p1"}}),
            _item(tags={"raw_payload": {"RecordType": "StatementTotal"}}),
            _item(tags={"raw_payload": {"ProductCode": "Tax"}}),
        ]
    )
    assert len(gravados) == 1
    linha = gravados[0]
    assert linha.usage_type == "BoxUsage:c7g.large"
    assert linha.project_id == "p1"
    assert linha.resource_id is None
    assert linha.attributes is None  # legado não ganha atributos inventados


async def test_promocao_do_cost_table_legado_fica_fora() -> None:
    gravados, _ = await _enriquecer(
        [
            _item(tags={"raw_payload": {"SKU description": "E2 Core", "Credit type": "PROMOTION"}}),
            _item(
                tags={
                    "raw_payload": {
                        "SKU description": "E2 Core",
                        "Credit type": "SPENDING_BASED_DISCOUNT",
                    }
                }
            ),
        ]
    )
    # Desconto estrutural fica (é preço); promoção sai (acaba).
    assert len(gravados) == 1


# ── paginação ───────────────────────────────────────────────────────────────


async def test_le_todas_as_paginas() -> None:
    repo = _Itens([_item() for _ in range(12)])
    todos = await listar_todos(repo.list_by_report, uuid4(), _USER, pagina=5)
    assert len(todos) == 12
    assert repo.chamadas == 3


async def test_pagina_cheia_no_fim_nao_perde_nem_repete() -> None:
    repo = _Itens([_item(cost=Decimal(i)) for i in range(10)])
    todos = await listar_todos(repo.list_by_report, uuid4(), _USER, pagina=5)
    assert [i.cost for i in todos] == [Decimal(i) for i in range(10)]
    assert repo.chamadas == 3  # a terceira volta vazia e encerra


async def test_enriquecimento_le_alem_da_primeira_pagina(monkeypatch) -> None:
    """Antes: `limit=100000` numa chamada só, e o excedente sumia sem erro."""
    monkeypatch.setattr(leitura_completa, "PAGINA", 2)
    itens = [_item(usage_type=f"BoxUsage:m7g.large{i}", charge_kind="usage") for i in range(5)]
    gravados, _ = await _enriquecer(itens)
    assert len(gravados) == 5
