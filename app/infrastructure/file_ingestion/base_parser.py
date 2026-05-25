from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pandas as pd


class BaseParser(ABC):
    """Contract every provider parser must follow.

    Subclasses MUST:
    - return DataFrame in the canonical normalized schema (see `value_objects/billing_record.py`)
    - never produce `float` columns for monetary values
    - tag every row with `user_id` (provided at construction)
    - preserve the original row inside `raw_payload` for replay
    """

    def __init__(self, user_id: UUID, job_id: UUID) -> None:
        self.user_id = user_id
        self.job_id = job_id
        self._detected_version: str = "UNKNOWN"

    @abstractmethod
    def can_parse(self, file_path: Path, content_sample: bytes) -> bool:
        """Return True iff this parser recognizes the file. Must NOT raise."""

    @abstractmethod
    def parse(self, file_path: Path) -> pd.DataFrame:
        """Read file → canonical DataFrame.

        Implementations must call `_set_version()` so `get_schema_version()` is consistent.
        """

    def get_schema_version(self) -> str:
        return self._detected_version

    def _set_version(self, v: str) -> None:
        self._detected_version = v

    def _cost_to_decimal(self, value: object) -> Decimal:
        """Safe cost conversion. Always via str — never `Decimal(float)`."""
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str):
            s = value.strip()
            if s == "" or s.lower() in {"nan", "none", "null"}:
                return Decimal("0")
            return Decimal(s)
        return Decimal(str(value))

    @staticmethod
    def _hash_row(row: dict) -> str:
        """Deterministic SHA-256 over the original line for dedup."""
        payload = json.dumps(row, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _parse_tags(raw: object) -> dict[str, str]:
        if raw is None or raw == "":
            return {}
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return {str(k): str(v) for k, v in parsed.items()}
            except (ValueError, TypeError):
                pass
        return {}
