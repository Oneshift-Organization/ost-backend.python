from decimal import Decimal
from uuid import uuid4

import pandas as pd

from app.infrastructure.file_ingestion.normalizer import (
    dataframe_to_items,
    deduplicate,
)


def _row(user_id, **overrides):
    base = {
        "user_id": str(user_id),
        "job_id": str(uuid4()),
        "provider": "aws",
        "schema_version": "AWS_CUR2.0",
        "account_id": "123",
        "service": "AmazonEC2",
        "service_raw": "AmazonEC2",
        "resource_id": None,
        "resource_type": None,
        "region": "us-east-1",
        "usage_start": None,
        "usage_end": None,
        "billing_period": "2026-05",
        "cost": Decimal("1.00"),
        "cost_discounted": Decimal("1.00"),
        "currency": "USD",
        "usage_amount": None,
        "usage_unit": None,
        "tags": {},
        "is_correction": False,
        "is_estimate": False,
        "raw_payload": {},
        "source_file": "x.csv",
        "line_hash": "a" * 64,
    }
    base.update(overrides)
    return base


def test_deduplicate_by_line_hash(user_id):
    df = pd.DataFrame(
        [
            _row(user_id, line_hash="x" * 64),
            _row(user_id, line_hash="x" * 64),
            _row(user_id, line_hash="y" * 64),
        ]
    )
    out = deduplicate(df)
    assert len(out) == 2


def test_dataframe_to_items_round_trip(user_id):
    report_id = uuid4()
    df = pd.DataFrame([_row(user_id, line_hash="a" * 64), _row(user_id, line_hash="b" * 64)])
    items = dataframe_to_items(df, report_id)
    assert len(items) == 2
    assert all(it.report_id == report_id for it in items)
    assert items[0].cost == Decimal("1.00")
