"""GCP Cost Table CSV parser (Google Cloud Billing console export).

This is the file exported from the Google Cloud Billing console →
"Cost Table" → "Download CSV". It is a semicolon-delimited CSV with
Portuguese/BRL locale column names.

Column names (PT-BR):
  Billing account name; Billing account ID; Project name; Project ID;
  Project hierarchy; Service description; Service ID;
  SKU description; SKU ID; Consumption model description;
  Credit type; Cost type;
  Usage start date; Usage end date;
  Usage amount; Usage unit;
  Unrounded Cost (R$); Cost (R$)

Locale: `.` is decimal, `,` is thousands separator (BR locale).
E.g. "1,564.84" → 1564.84, "1,489" → 1489.

Signature: columns "Service description" AND "Cost (R$)" — not present in
the BigQuery export format (which uses "billing_account_id" + "usage_start_time").
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import read_tabular

_SIGNATURE_COLS = ("Service description", "Cost (R$)")


class GcpCostTableParser(BaseParser):
    """GCP Cost Table CSV → canonical long-form DataFrame."""

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(col in text for col in _SIGNATURE_COLS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        self._set_version("GCP_COST_TABLE")
        df_raw = read_tabular(file_path)
        return self._normalize(df_raw, file_path.name)

    def _normalize(self, df: pd.DataFrame, source_file: str) -> pd.DataFrame:
        records: list[dict] = []
        for _, row in df.iterrows():
            cost = _br_to_decimal(row.get("Cost (R$)", "0"))

            cost_type = _clean_gcp(row.get("Cost type", "")).lower()
            if cost_type == "tax":
                continue

            usage_start = _parse_date_gcp(row.get("Usage start date"))
            usage_end = _parse_date_gcp(row.get("Usage end date"))
            billing_period = usage_start.strftime("%Y-%m") if usage_start else "0000-00"

            service = _clean_gcp(row.get("Service description", "")) or "unknown"
            raw_payload = {
                k: _clean_gcp(v)
                for k, v in row.items()
                if _clean_gcp(v)
            }

            record = {
                "user_id": str(self.user_id),
                "job_id": str(self.job_id),
                "provider": "gcp",
                "schema_version": self.get_schema_version(),
                "account_id": _clean_gcp(row.get("Billing account ID", "")),
                "account_name": _clean_gcp(row.get("Project name", "")) or None,
                "service": service,
                "service_raw": service,
                "resource_id": None,
                "resource_type": _clean_gcp(row.get("SKU description", "")) or None,
                "region": None,
                "usage_start": usage_start,
                "usage_end": usage_end,
                "billing_period": billing_period,
                "cost": cost,
                "cost_discounted": cost,
                "currency": "BRL",
                "usage_amount": _br_to_decimal(row.get("Usage amount", "0")),
                "usage_unit": _clean_gcp(row.get("Usage unit", "")) or None,
                "tags": {},
                "is_correction": (
                    _clean_gcp(row.get("Cost type", "")).lower()
                    in ("adjustment", "rounding_error")
                    or cost < 0
                ),
                "is_estimate": False,
                "raw_payload": raw_payload,
                "source_file": source_file,
                "line_hash": self._hash_row(raw_payload),
            }
            records.append(record)

        return pd.DataFrame(records)


def _parse_date_gcp(value: object):
    """Parse GCP cost table date format: '2025-12-31'."""
    if not value or not str(value).strip():
        return None
    try:
        ts = pd.to_datetime(str(value).strip(), utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except (ValueError, TypeError):
        return None


def _clean_gcp(value: object) -> str:
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


def _br_to_decimal(value: object) -> Decimal:
    """Convert a BR-locale numeric string to Decimal.

    In this export format:
      - `.` is the decimal separator (e.g. "4.38")
      - `,` is the thousands separator (e.g. "1,489" → 1489)
    Strip all commas before conversion.
    """
    if value is None:
        return Decimal("0")
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "none", "null", "-"}:
        return Decimal("0")
    s = s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")
