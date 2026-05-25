"""Azure Cost Management Export parser.

Supports:
- PAYG (Pay-As-You-Go):  SubscriptionGuid, Date
- EA   (Enterprise):     SubscriptionId,   UsageDateTime
- Formats: .csv (UTF-8 BOM aware), .csv.gz, .xlsx, .xlsm
  (Parquet via pyarrow is deferred.)

Detection key: presence of CostInBillingCurrency + (Date | UsageDateTime).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import read_tabular


class AzureParser(BaseParser):
    _SIGNATURE_TOKENS = ("CostInBillingCurrency", "MeterCategory")

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(tok in text for tok in self._SIGNATURE_TOKENS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        # Azure CSV exports often carry UTF-8 BOM; XLSX path ignores encoding.
        df_raw = read_tabular(file_path, encoding="utf-8-sig")

        # Detect dialect (PAYG vs EA) by column name presence.
        date_col = "UsageDateTime" if "UsageDateTime" in df_raw.columns else "Date"
        sub_col = "SubscriptionId" if "SubscriptionId" in df_raw.columns else "SubscriptionGuid"
        is_ea = date_col == "UsageDateTime"
        self._set_version("AZURE_EA" if is_ea else "AZURE_PAYG")

        records: list[dict] = []
        for _, row in df_raw.iterrows():
            raw = {k: v for k, v in row.to_dict().items() if v != ""}
            cost = self._cost_to_decimal(row.get("CostInBillingCurrency"))

            usage_dt = _parse_dt(row.get(date_col))
            period = usage_dt.strftime("%Y-%m") if usage_dt is not None else "0000-00"

            service_raw = str(row.get("ServiceName")) or str(row.get("MeterCategory")) or "unknown"

            record = {
                "user_id": str(self.user_id),
                "job_id": str(self.job_id),
                "provider": "azure",
                "schema_version": self.get_schema_version(),
                "account_id": str(row.get(sub_col, "")) or "",
                "account_name": str(row.get("SubscriptionName", "")) or None,
                "service": service_raw,
                "service_raw": service_raw,
                "resource_id": str(row.get("ResourceId", "")) or None,
                "resource_type": str(row.get("MeterSubCategory", "")) or None,
                "region": str(row.get("ResourceLocation", "")) or None,
                "usage_start": usage_dt,
                "usage_end": usage_dt,
                "billing_period": period,
                "cost": cost,
                "cost_discounted": cost,
                "currency": (str(row.get("BillingCurrency", "")) or "USD").upper()[:3],
                "usage_amount": (
                    self._cost_to_decimal(row.get("Quantity")) if row.get("Quantity") else None
                ),
                "usage_unit": str(row.get("UnitOfMeasure", "")) or None,
                "tags": self._parse_tags(row.get("Tags")),
                "is_correction": False,
                "is_estimate": False,
                "raw_payload": raw,
                "source_file": file_path.name,
                "line_hash": self._hash_row(raw),
            }
            records.append(record)

        return pd.DataFrame(records)


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
