"""Contrato canônico ampliado para exports detalhados (E1).

Duas garantias: os campos novos atravessam normalizer e validador intactos, e
um DataFrame de parser legado — que não tem nenhum deles — passa como antes.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest

from app.core.exceptions import ValidationError
from app.infrastructure.file_ingestion.normalizer import dataframe_to_items
from app.infrastructure.file_ingestion.validator import validate_normalized_df


def _linha(user_id, **extra):
    base = {
        "user_id": str(user_id),
        "job_id": str(uuid4()),
        "provider": "aws",
        "schema_version": "AWS_CUR2.0",
        "account_id": "123",
        "service": "AmazonEC2",
        "service_raw": "AmazonEC2",
        "billing_period": "2026-03",
        "cost": Decimal("1.50"),
        "currency": "USD",
        "is_correction": False,
        "is_estimate": False,
        "raw_payload": {},
        "source_file": "cur.csv.gz",
        "line_hash": uuid4().hex + uuid4().hex,
    }
    base.update(extra)
    return base


def test_legado_sem_colunas_novas_valida_e_normaliza(user_id) -> None:
    df = pd.DataFrame([_linha(user_id)])
    validate_normalized_df(df)
    item = dataframe_to_items(df, uuid4())[0]
    assert item.usage_type is None
    assert item.charge_kind is None
    assert item.cost_billed is None
    assert item.attributes == {}


def test_campos_novos_atravessam_o_normalizer(user_id) -> None:
    atributos = {"instance_type": "m7g.2xlarge", "vcpus": "8", "memory_gib": "32"}
    df = pd.DataFrame(
        [
            _linha(
                user_id,
                usage_type="BoxUsage:m7g.2xlarge",
                resource_type="m7g.2xlarge",
                charge_kind="usage",
                cost_billed=Decimal("0.00"),
                attributes=atributos,
            )
        ]
    )
    validate_normalized_df(df)
    item = dataframe_to_items(df, uuid4())[0]
    assert item.usage_type == "BoxUsage:m7g.2xlarge"
    assert item.resource_type == "m7g.2xlarge"
    assert item.charge_kind == "usage"
    # Zero faturado com custo amortizado positivo é o caso do Savings Plan —
    # o zero precisa sobreviver como Decimal, não virar None.
    assert item.cost_billed == Decimal("0.00")
    assert item.attributes == atributos


def test_atributos_ausentes_em_parte_das_linhas_viram_dict_vazio(user_id) -> None:
    # Pandas preenche com NaN a coluna que falta numa linha; NaN não é dict.
    df = pd.DataFrame(
        [
            _linha(user_id, attributes={"vcpus": "2"}),
            _linha(user_id),
        ]
    )
    itens = dataframe_to_items(df, uuid4())
    assert itens[0].attributes == {"vcpus": "2"}
    assert itens[1].attributes == {}


def test_natureza_fora_do_vocabulario_e_recusada(user_id) -> None:
    # Natureza desconhecida passaria pelo enriquecimento sem filtro nem rota.
    df = pd.DataFrame([_linha(user_id, charge_kind="SavingsPlanNegation")])
    with pytest.raises(ValidationError):
        validate_normalized_df(df)


def test_natureza_nula_e_aceita(user_id) -> None:
    df = pd.DataFrame([_linha(user_id, charge_kind=None), _linha(user_id, charge_kind="tax")])
    validate_normalized_df(df)


def test_custo_faturado_em_float_e_recusado(user_id) -> None:
    # Monetário nunca em float — nem no valor faturado.
    df = pd.DataFrame([_linha(user_id, cost_billed=1.5)])
    with pytest.raises(ValidationError):
        validate_normalized_df(df)
