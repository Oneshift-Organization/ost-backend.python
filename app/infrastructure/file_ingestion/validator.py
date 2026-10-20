"""Pandera validation for the normalized billing DataFrame.

Use `validate_normalized_df(df)` after a parser returns and BEFORE persistence.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

from app.core.exceptions import ValidationError
from app.domain.value_objects.charge_kind import VALORES as CHARGE_KINDS

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")

_CURRENCY_LEN = 3
_SHA256_HEX_LEN = 64


def _is_uuid_str(s: str) -> bool:
    return bool(_UUID_RE.match(s)) if isinstance(s, str) else False


def _is_decimal(v: object) -> bool:
    return isinstance(v, Decimal)


def _is_period(s: str) -> bool:
    return bool(_PERIOD_RE.match(s)) if isinstance(s, str) else False


BillingNormalizedSchema = DataFrameSchema(
    {
        "user_id": Column(str, Check(lambda s: s.apply(_is_uuid_str))),
        "job_id": Column(str, Check(lambda s: s.apply(_is_uuid_str))),
        "provider": Column(str, Check.isin(["aws", "azure", "gcp", "oci"])),
        "schema_version": Column(str, nullable=False),
        "account_id": Column(str, nullable=True, coerce=True),
        "service": Column(str, nullable=False),
        "service_raw": Column(str, nullable=False),
        "cost": Column(object, Check(lambda s: s.apply(_is_decimal))),
        "currency": Column(str, Check(lambda s: s.str.len() == _CURRENCY_LEN)),
        "billing_period": Column(str, Check(lambda s: s.apply(_is_period))),
        "is_correction": Column(bool),
        "is_estimate": Column(bool),
        "raw_payload": Column(object, nullable=False),
        "source_file": Column(str, nullable=False),
        "line_hash": Column(str, Check(lambda s: s.str.len() == _SHA256_HEX_LEN)),
        # Exports detalhados. `required=False`: parser legado não emite estas
        # colunas, e exigi-las quebraria o caminho travado pela caracterização.
        # Quando presentes, o vocabulário é fechado — uma natureza desconhecida
        # passaria pelo enriquecimento sem filtro nem rota.
        # dtype livre: pandas 3 infere `str` para coluna só de texto e `object`
        # quando há None — os dois são válidos aqui.
        "charge_kind": Column(
            None,
            Check(lambda s: s.dropna().isin(CHARGE_KINDS).all()),
            nullable=True,
            required=False,
        ),
        "cost_billed": Column(
            object,
            Check(lambda s: s.dropna().apply(_is_decimal).all()),
            nullable=True,
            required=False,
        ),
    },
    strict=False,
    coerce=False,
)


def validate_normalized_df(df: pd.DataFrame) -> pd.DataFrame:
    """Run schema validation; raise ValidationError with friendly message on failure."""
    if df.empty:
        raise ValidationError("normalized DataFrame is empty")
    try:
        return BillingNormalizedSchema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        raise ValidationError(f"schema validation failed: {e.failure_cases}") from e
