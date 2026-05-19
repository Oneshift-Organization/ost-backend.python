"""AWS Cost and Usage Report (CUR 2.0 + Legacy) parser.

Supports:
- CUR 2.0 (snake_case columns: line_item_unblended_cost, etc.)
- Legacy CUR (camelCase / slashed: lineItem/UnblendedCost)
- Formats: .csv, .csv.gz, .csv.zip, .xlsx, .xlsm

Critical rules enforced:
- dtype=str on read (no float inference on cost columns)
- Decimal(str(value)) for every cost
- is_estimate=True when bill_invoice_id is empty
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import read_tabular


class AWSParser(BaseParser):
    _CUR2_SIGNATURE = "line_item_unblended_cost"
    _LEGACY_SIGNATURE = "lineItem/UnblendedCost"

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return self._CUR2_SIGNATURE in text or self._LEGACY_SIGNATURE in text

    def parse(self, file_path: Path) -> pd.DataFrame:
        df_raw = read_tabular(file_path)
        is_cur2 = self._CUR2_SIGNATURE in df_raw.columns
        self._set_version("AWS_CUR2.0" if is_cur2 else "AWS_CUR_LEGACY")
        return self._normalize(df_raw, file_path.name, is_cur2)

    def _normalize(self, df: pd.DataFrame, source_file: str, is_cur2: bool) -> pd.DataFrame:
        cols = _CUR2_COLS if is_cur2 else _LEGACY_COLS

        records: list[dict] = []
        for _, row in df.iterrows():
            raw = {k: v for k, v in row.to_dict().items() if v != ""}
            cost = self._cost_to_decimal(row.get(cols["cost"]))
            cost_disc = self._cost_to_decimal(row.get(cols["cost_net"]) or row.get(cols["cost"]))

            usage_start = _parse_dt(row.get(cols["usage_start"]))
            usage_end = _parse_dt(row.get(cols["usage_end"]))
            period_raw = str(row.get(cols["period"], ""))[:7] or (
                usage_start.strftime("%Y-%m") if usage_start is not None else "0000-00"
            )

            record = {
                "user_id": str(self.user_id),
                "job_id": str(self.job_id),
                "provider": "aws",
                "schema_version": self.get_schema_version(),
                "account_id": str(row.get(cols["account"], "")) or "",
                "account_name": None,
                "service": str(row.get(cols["service"], "")) or "unknown",
                "service_raw": str(row.get(cols["service"], "")) or "unknown",
                "resource_id": str(row.get(cols["resource"], "")) or None,
                "resource_type": None,
                "region": str(row.get(cols["region"], "")) or None,
                "usage_start": usage_start,
                "usage_end": usage_end,
                "billing_period": period_raw,
                "cost": cost,
                "cost_discounted": cost_disc,
                "currency": (str(row.get(cols["currency"], "")) or "USD").upper()[:3] or "USD",
                "usage_amount": (
                    self._cost_to_decimal(row.get(cols["amount"]))
                    if row.get(cols["amount"])
                    else None
                ),
                "usage_unit": str(row.get(cols["unit"], "")) or None,
                "tags": self._parse_tags(row.get(cols["tags"])),
                "is_correction": False,
                "is_estimate": not bool(row.get(cols["invoice"])),
                "raw_payload": raw,
                "source_file": source_file,
                "line_hash": self._hash_row(raw),
            }
            records.append(record)

        return pd.DataFrame(records)


_CUR2_COLS = {
    "cost": "line_item_unblended_cost",
    "cost_net": "line_item_net_unblended_cost",
    "usage_start": "line_item_usage_start_date",
    "usage_end": "line_item_usage_end_date",
    "service": "line_item_product_code",
    "resource": "line_item_resource_id",
    "amount": "line_item_usage_amount",
    "unit": "pricing_unit",
    "tags": "resource_tags",
    "invoice": "bill_invoice_id",
    "account": "bill_payer_account_id",
    "region": "product_region",
    "period": "bill_billing_period_start_date",
    "currency": "line_item_currency_code",
}

_LEGACY_COLS = {
    "cost": "lineItem/UnblendedCost",
    "cost_net": "lineItem/NetUnblendedCost",
    "usage_start": "lineItem/UsageStartDate",
    "usage_end": "lineItem/UsageEndDate",
    "service": "lineItem/ProductCode",
    "resource": "lineItem/ResourceId",
    "amount": "lineItem/UsageAmount",
    "unit": "pricing/unit",
    "tags": "resourceTags",
    "invoice": "bill/InvoiceId",
    "account": "bill/PayerAccountId",
    "region": "product/region",
    "period": "bill/BillingPeriodStartDate",
    "currency": "lineItem/CurrencyCode",
}


def _parse_dt(value: object):
    if not value:
        return None
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except (ValueError, TypeError):
        return None
