"""AWS Cost Explorer "Daily costs by service" parser.

This is the file you get when you click "Download CSV" inside the AWS Cost
Explorer UI — NOT the same as a CUR (Cost and Usage Report) delivered to S3.

Format quirks handled here:
- TSV (tab-separated) is the default; comma-separated also occurs. We sniff.
- UTF-8 BOM common (`utf-8-sig`).
- Wide / pivoted: columns = services, rows = dates. The CUR parser is long
  (one row per line-item); here we MELT the columns into long form.
- First row is often "Service total" — a rollup of every date. We drop it
  to avoid double-counting.
- Last column is "Total costs($)" — a row-wise sum. Also dropped.
- Service columns carry the currency suffix in the header: "EC2-Instances($)".
- Locale-formatted decimals: pt-BR / pt-PT exports use "," as decimal
  (e.g. `1369,5165049893`). en-US exports use ".". We handle both.

Zero-cost emission policy (signal for the migration AI agent):
- A service with cost>0 on at least one date is "active": emit ALL its daily
  rows, including cost=0 days (so the agent can detect "service zeroed this
  month").
- A service that is cost=0 across the entire export is "global zero": emit a
  SINGLE summary row per billing_period covering the whole month range, with
  `raw_payload.scope = "global_zero"`. The agent uses this to know the
  service is present in the account without inflating billing_items.

Signature: any column ends with "($)" AND row-label column is named "Service".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import read_tabular

_ROW_LABEL_COL = "Service"
_SUMMARY_ROW_LABEL = "Service total"
_CURRENCY_SUFFIX = "($)"
_TOTAL_COL = "Total costs($)"


class AWSCostExplorerParser(BaseParser):
    """Wide-format AWS Cost Explorer export → canonical long-form DataFrame."""

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8-sig", errors="ignore")
        except Exception:
            return False
        # Need the row-label column AND the total column to be confident.
        return _ROW_LABEL_COL in text and _TOTAL_COL in text

    def parse(self, file_path: Path) -> pd.DataFrame:
        self._set_version("AWS_CE_DAILY_BY_SERVICE")
        df_raw = read_tabular(file_path)
        return self._normalize(df_raw, file_path.name)

    def _normalize(self, df: pd.DataFrame, source_file: str) -> pd.DataFrame:
        if _ROW_LABEL_COL not in df.columns:
            return pd.DataFrame()

        service_cols = [
            c
            for c in df.columns
            if isinstance(c, str) and c.endswith(_CURRENCY_SUFFIX) and c != _TOTAL_COL
        ]

        active_services, period_spans = self._scan(df, service_cols)
        global_zero_services = [c for c in service_cols if c not in active_services]

        records: list[dict] = []
        records.extend(self._emit_active_rows(df, service_cols, active_services, source_file))
        records.extend(self._emit_global_zero_rows(global_zero_services, period_spans, source_file))
        return pd.DataFrame(records)

    def _scan(
        self,
        df: pd.DataFrame,
        service_cols: list[str],
    ) -> tuple[set[str], dict[str, tuple[datetime, datetime]]]:
        """Pass 1: classify services as 'active' (cost>0 anywhere) vs 'global zero',
        and collect the date span of each billing_period for summary rows."""
        active: set[str] = set()
        spans: dict[str, tuple[datetime, datetime]] = {}
        for _, row in df.iterrows():
            usage_start = self._row_date(row)
            if usage_start is None:
                continue
            period = usage_start.strftime("%Y-%m")
            cur = spans.get(period)
            spans[period] = (
                (usage_start, usage_start)
                if cur is None
                else (min(cur[0], usage_start), max(cur[1], usage_start))
            )
            for svc_col in service_cols:
                if _locale_to_decimal(row[svc_col]) > 0:
                    active.add(svc_col)
        return active, spans

    def _emit_active_rows(
        self,
        df: pd.DataFrame,
        service_cols: list[str],
        active_services: set[str],
        source_file: str,
    ) -> list[dict]:
        """Pass 2a: per-day rows for active services (including cost=0 days,
        so the AI agent can detect a service that zeroed in this period)."""
        out: list[dict] = []
        for _, row in df.iterrows():
            usage_start = self._row_date(row)
            if usage_start is None:
                continue
            usage_end = usage_start + timedelta(days=1) - timedelta(seconds=1)
            period = usage_start.strftime("%Y-%m")
            label = str(row[_ROW_LABEL_COL]).strip()
            for svc_col in service_cols:
                if svc_col not in active_services:
                    continue
                raw_value = row[svc_col]
                cost = _locale_to_decimal(raw_value)
                service = svc_col[: -len(_CURRENCY_SUFFIX)].strip()
                raw = {"date": label, "service_column": svc_col, "cost_raw": str(raw_value)}
                out.append(
                    self._build_record(
                        service=service,
                        service_raw=svc_col,
                        usage_start=usage_start,
                        usage_end=usage_end,
                        period=period,
                        cost=cost,
                        raw=raw,
                        source_file=source_file,
                    )
                )
        return out

    def _emit_global_zero_rows(
        self,
        global_zero_services: list[str],
        period_spans: dict[str, tuple[datetime, datetime]],
        source_file: str,
    ) -> list[dict]:
        """Pass 2b: one summary row per (billing_period, global-zero service).
        `raw_payload.scope = "global_zero"` is the marker the AI agent uses to
        recognize the signal 'service observed, never billed in this period'."""
        out: list[dict] = []
        for svc_col in global_zero_services:
            service = svc_col[: -len(_CURRENCY_SUFFIX)].strip()
            for period, (span_start, span_max) in period_spans.items():
                span_end = span_max + timedelta(days=1) - timedelta(seconds=1)
                raw = {
                    "scope": "global_zero",
                    "service_column": svc_col,
                    "date_range": f"{span_start.date().isoformat()}..{span_max.date().isoformat()}",
                    "billing_period": period,
                }
                out.append(
                    self._build_record(
                        service=service,
                        service_raw=svc_col,
                        usage_start=span_start,
                        usage_end=span_end,
                        period=period,
                        cost=Decimal("0"),
                        raw=raw,
                        source_file=source_file,
                    )
                )
        return out

    @staticmethod
    def _row_date(row: pd.Series) -> datetime | None:
        """Return the parsed date for a CE row, or None if it is the summary
        rollup, empty, or any other non-date label."""
        label = str(row[_ROW_LABEL_COL]).strip()
        # Belt-and-suspenders: skip financial metadata rows
        if str(row.get("RecordType", "")) in ("StatementTotal", "InvoiceTotal", "Tax"):
            return None
        if not label or label == _SUMMARY_ROW_LABEL:
            return None
        return _parse_date(label)

    def _build_record(
        self,
        *,
        service: str,
        service_raw: str,
        usage_start: datetime,
        usage_end: datetime,
        period: str,
        cost: Decimal,
        raw: dict,
        source_file: str,
    ) -> dict:
        return {
            "user_id": str(self.user_id),
            "job_id": str(self.job_id),
            "provider": "aws",
            "schema_version": self.get_schema_version(),
            "account_id": "",
            "account_name": None,
            "service": service,
            "service_raw": service_raw,
            "resource_id": None,
            "resource_type": None,
            "region": None,
            "usage_start": usage_start,
            "usage_end": usage_end,
            "billing_period": period,
            "cost": cost,
            "cost_discounted": cost,
            "currency": "USD",
            "usage_amount": None,
            "usage_unit": None,
            "tags": {},
            "is_correction": False,
            "is_estimate": False,
            "raw_payload": raw,
            "source_file": source_file,
            "line_hash": self._hash_row(raw),
        }


def _parse_date(value: str) -> datetime | None:
    """Cost Explorer row labels are dates like '2026-04-01'. tz=UTC."""
    if not value:
        return None
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except (ValueError, TypeError):
        return None


def _locale_to_decimal(value: object) -> Decimal:
    """Cost Explorer exports use the spreadsheet locale's decimal mark.

    Cases handled:
    - en-US:   "1369.5165"        → Decimal("1369.5165")
    - pt-BR:   "1369,5165"        → Decimal("1369.5165")
    - mixed:   "1.369,5165"       → assume `,` is decimal, `.` is thousands
    - mixed:   "1,369.5165"       → assume `.` is decimal, `,` is thousands
    - "0", "", None                → Decimal("0")
    """
    if value is None:
        return Decimal("0")
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "none", "null", "-"}:
        return Decimal("0")

    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        # Whichever appears LAST is the decimal mark.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        # Pure comma → treat as decimal (locale-formatted).
        s = s.replace(",", ".")
    # has_dot only OR neither → leave as-is.

    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")
