"""AWS Detailed Billing / Invoice CSV parser.

This is the file you get from AWS Cost Explorer → "Download CSV" or from
the Billing Console → "Invoices" → detailed line items. It is NOT the same
as a CUR (Cost and Usage Report) nor the wide "Daily costs by service" report.

Format: one row per usage line-item, columns like:
  InvoiceID, PayerAccountId, LinkedAccountId, RecordType,
  BillingPeriodStartDate, BillingPeriodEndDate,
  ProductCode, UsageType, Operation,
  UsageStartDate, UsageEndDate,
  UsageQuantity, BlendedRate, CurrencyCode,
  CostBeforeTax, Credits, TaxAmount, TotalCost

Signature: header contains both "ProductCode" AND "CostBeforeTax".
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import read_tabular

_SIGNATURE_COLS = ("ProductCode", "CostBeforeTax")


def _clean(value: object) -> str:
    """Return stripped string, or '' if value is NaN/None/empty."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return "" if s.lower() in {"nan", "none", "null", ""} else s


class AwsDetailedBillingParser(BaseParser):
    """AWS Detailed Billing / Invoice CSV → canonical long-form DataFrame."""

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8-sig", errors="ignore")
        except Exception:
            return False
        return all(col in text for col in _SIGNATURE_COLS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        self._set_version("AWS_DETAILED_BILLING")
        df_raw = read_tabular(file_path)
        return self._normalize(df_raw, file_path.name)

    def _normalize(self, df: pd.DataFrame, source_file: str) -> pd.DataFrame:
        records: list[dict] = []
        for _, row in df.iterrows():
            record_type = _clean(row.get("RecordType", ""))
            if record_type in ("StatementTotal", "InvoiceTotal"):
                continue

            cost = self._cost_to_decimal(_clean(row.get("CostBeforeTax", "0")))
            usage_start = _parse_dt_invoice(row.get("BillingPeriodStartDate"))
            usage_end = _parse_dt_invoice(row.get("BillingPeriodEndDate"))
            billing_period = usage_start.strftime("%Y-%m") if usage_start else "0000-00"

            service = _clean(row.get("ProductCode", "")) or "unknown"
            usage_type = _clean(row.get("UsageType", "")) or None
            raw_payload = {
                k: _clean(v)
                for k, v in row.items()
                if _clean(v)
            }

            record = {
                "user_id": str(self.user_id),
                "job_id": str(self.job_id),
                "provider": "aws",
                "schema_version": self.get_schema_version(),
                "account_id": _clean(row.get("LinkedAccountId", "")),
                "account_name": _clean(row.get("PayerAccountName", "")) or None,
                "service": service,
                "service_raw": service,
                "resource_id": None,
                "resource_type": usage_type,
                "region": None,
                "usage_start": usage_start,
                "usage_end": usage_end,
                "billing_period": billing_period,
                "cost": cost,
                "cost_discounted": cost,
                "currency": _clean(row.get("CurrencyCode", "USD")) or "USD",
                "usage_amount": self._cost_to_decimal(_clean(row.get("UsageQuantity", "0"))),
                "usage_unit": "hours" if usage_type and usage_type.startswith("BoxUsage") else None,
                "tags": {},
                "is_correction": False,
                "is_estimate": False,
                "raw_payload": raw_payload,
                "source_file": source_file,
                "line_hash": self._hash_row(raw_payload),
            }
            records.append(record)

        return pd.DataFrame(records)


def _parse_dt_invoice(value: object):
    """Parse AWS invoice date format: '2026/03/01 00:00:00'."""
    if not value or not str(value).strip():
        return None
    try:
        ts = pd.to_datetime(str(value).strip(), utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except (ValueError, TypeError):
        return None
