"""GCP Cloud Billing Export parser (BigQuery → JSON/CSV/XLSX).

CRITICAL: GCP `cost` is FLOAT in BigQuery. Never read it as float into Pandas.
Strategy: read as string, then `Decimal(str(value))`. This preserves precision
exactly equivalent to the "micros" trick (`int(cost*1e6)`) without intermediate float.

Supports:
- NDJSON (one JSON object per line) — typical BigQuery export
- JSON array
- CSV with the same column set
- XLSX / XLSM (manual exports from BigQuery via Looker Studio, etc.)
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.loaders import is_xlsx, read_tabular

_INVOICE_MONTH_LEN = 6  # GCP invoice.month format: "YYYYMM"


class GCPParser(BaseParser):
    _SIGNATURE_TOKENS = ("billing_account_id", "usage_start_time")

    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        try:
            text = content_sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
        return all(tok in text for tok in self._SIGNATURE_TOKENS)

    def parse(self, file_path: Path) -> pd.DataFrame:
        self._set_version("GCP_BILLING_STD")
        suffix = file_path.suffix.lower()
        if suffix in {".json", ".ndjson"}:
            rows = _load_json_lines(file_path)
        elif is_xlsx(file_path) or suffix == ".csv":
            rows = read_tabular(file_path).to_dict(orient="records")
        else:
            # Try JSON first, then tabular fallback.
            try:
                rows = _load_json_lines(file_path)
            except (ValueError, json.JSONDecodeError):
                rows = read_tabular(file_path).to_dict(orient="records")

        records: list[dict] = []
        for raw in rows:
            # Belt-and-suspenders: skip financial metadata / summary rows
            if str(raw.get("RecordType", "")) in ("StatementTotal", "InvoiceTotal", "Tax"):
                continue

            cost = self._cost_to_decimal(raw.get("cost"))
            # GCP delivers credits as an array of {name, amount, type}.
            credits_total = _sum_credits(raw.get("credits"))
            cost_discounted = cost + credits_total  # credits are typically negative

            usage_start = _parse_dt(raw.get("usage_start_time"))
            usage_end = _parse_dt(raw.get("usage_end_time"))

            service = (
                _nested(raw.get("service"), "description")
                or _nested(raw.get("service"), "id")
                or "unknown"
            )

            record = {
                "user_id": str(self.user_id),
                "job_id": str(self.job_id),
                "provider": "gcp",
                "schema_version": self.get_schema_version(),
                "account_id": str(raw.get("billing_account_id", "")) or "",
                "account_name": _nested(raw.get("project"), "name"),
                "service": service,
                "service_raw": service,
                "resource_id": _nested(raw.get("resource"), "name")
                or _nested(raw.get("resource"), "global_name"),
                "resource_type": _nested(raw.get("sku"), "description"),
                "region": _nested(raw.get("location"), "region"),
                "usage_start": usage_start,
                "usage_end": usage_end,
                "billing_period": _gcp_period(raw, usage_start),
                "cost": cost,
                "cost_discounted": cost_discounted,
                "currency": (str(raw.get("currency", "")) or "USD").upper()[:3] or "USD",
                "usage_amount": (
                    self._cost_to_decimal(_nested(raw.get("usage"), "amount"))
                    if _nested(raw.get("usage"), "amount") is not None
                    else None
                ),
                "usage_unit": _nested(raw.get("usage"), "unit"),
                "tags": _labels_to_dict(raw.get("labels")),
                "is_correction": raw.get("cost_type") in {"adjustment", "rounding_error"},
                "is_estimate": False,
                "raw_payload": raw,
                "source_file": file_path.name,
                "line_hash": self._hash_row(raw),
            }
            records.append(record)

        return pd.DataFrame(records)


def _load_json_lines(file_path: Path) -> list[dict]:
    """Read NDJSON (one obj per line) or a JSON array."""
    text = file_path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        return list(data) if isinstance(data, list) else []
    rows: list[dict] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows


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


def _nested(obj: object, key: str) -> str | None:
    if isinstance(obj, dict) and obj.get(key) is not None:
        return str(obj[key])
    return None


def _labels_to_dict(labels: object) -> dict[str, str]:
    if not isinstance(labels, list):
        return {}
    out: dict[str, str] = {}
    for item in labels:
        if isinstance(item, dict) and "key" in item:
            out[str(item["key"])] = str(item.get("value", ""))
    return out


def _sum_credits(credits: object) -> Decimal:
    if not isinstance(credits, list):
        return Decimal("0")
    total = Decimal("0")
    for c in credits:
        if isinstance(c, dict) and c.get("amount") is not None:
            total += Decimal(str(c["amount"]))
    return total


def _gcp_period(raw: dict, usage_start) -> str:
    invoice = raw.get("invoice") if isinstance(raw, dict) else None
    if isinstance(invoice, dict) and invoice.get("month"):
        month = str(invoice["month"])
        if len(month) == _INVOICE_MONTH_LEN:
            return f"{month[:4]}-{month[4:]}"
    if usage_start is not None:
        return usage_start.strftime("%Y-%m")
    return "0000-00"
