"""OCI Cost Report parser (proprietary format).

Notable quirks:
- Columns use `/` as namespace separator: `lineItem/intervalUsageStart`, `cost/myCost`...
- Native format is `.csv.gz`; multiple files per day (every 6h).
- Also accepts `.xlsx` / `.xlsm` (manual exports / reports prepared by FinOps teams).
- `lineItem/isCorrection=true` rows REPLACE the original (referenced by
  `lineItem/backReference`). They must not be summed.
- Tag columns are dynamic — every key prefixed by `tags/`.

Detection key: presence of `cost/myCost` + `lineItem/intervalUsageStart`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import read_tabular


class OCIParser(BaseParser):
    _SIGNATURE_TOKENS = ("cost/myCost", "lineItem/intervalUsageStart")

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(tok in text for tok in self._SIGNATURE_TOKENS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        df_raw = read_tabular(file_path)
        self._set_version("OCI_COST_REPORT")

        df_raw = self._apply_corrections(df_raw)

        records: list[dict] = []
        for _, row in df_raw.iterrows():
            raw = {k: v for k, v in row.to_dict().items() if v != ""}
            cost = self._cost_to_decimal(row.get("cost/myCost"))

            usage_start = _parse_dt(row.get("lineItem/intervalUsageStart"))
            usage_end = _parse_dt(row.get("lineItem/intervalUsageEnd"))
            period = usage_start.strftime("%Y-%m") if usage_start is not None else "0000-00"

            service = str(row.get("product/service", "")) or "unknown"
            tags = {
                col[len("tags/") :]: str(val)
                for col, val in row.to_dict().items()
                if col.startswith("tags/") and val
            }

            record = {
                "user_id": str(self.user_id),
                "job_id": str(self.job_id),
                "provider": "oci",
                "schema_version": self.get_schema_version(),
                "account_id": str(row.get("tenantId", "")) or "",
                "account_name": str(row.get("compartmentName", "")) or None,
                "service": service,
                "service_raw": service,
                "resource_id": str(row.get("product/resourceId", "")) or None,
                "resource_type": str(row.get("product/resource", "")) or None,
                "region": str(row.get("product/region", "")) or None,
                "usage_start": usage_start,
                "usage_end": usage_end,
                "billing_period": period,
                "cost": cost,
                "cost_discounted": cost,
                "currency": (str(row.get("cost/currencyCode", "")) or "USD").upper()[:3] or "USD",
                "usage_amount": (
                    self._cost_to_decimal(row.get("usage/billedQuantity"))
                    if row.get("usage/billedQuantity")
                    else None
                ),
                "usage_unit": str(row.get("usage/billedUnit", "")) or None,
                "tags": tags,
                "is_correction": str(row.get("lineItem/isCorrection", "")).lower() == "true",
                "is_estimate": False,
                "raw_payload": raw,
                "source_file": file_path.name,
                "line_hash": self._hash_row(raw),
            }
            records.append(record)

        return pd.DataFrame(records)

    @staticmethod
    def _apply_corrections(df: pd.DataFrame) -> pd.DataFrame:
        """Correction rows REPLACE referenced rows — never sum on top.

        Strategy:
        1. Identify correction rows (isCorrection == true).
        2. Drop the referenced original rows (matched by referenceNo).
        3. Keep corrections themselves (they carry the right cost).
        """
        if "lineItem/isCorrection" not in df.columns:
            return df

        flag = df["lineItem/isCorrection"].astype(str).str.lower() == "true"
        corrections = df[flag]
        if corrections.empty:
            return df

        if "lineItem/backReference" not in df.columns or "lineItem/referenceNo" not in df.columns:
            return df

        back_refs = set(corrections["lineItem/backReference"].dropna().astype(str))
        keep_mask = ~df["lineItem/referenceNo"].astype(str).isin(back_refs) | flag
        return df[keep_mask].reset_index(drop=True)


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
