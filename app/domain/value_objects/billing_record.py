"""Schema canônico de billing após normalização.

Toda saída de parser DEVE bater com este shape antes de virar `BillingItem` persistido.
"""

from datetime import datetime
from decimal import Decimal
from typing import TypedDict


class NormalizedBillingRecord(TypedDict, total=False):
    user_id: str
    job_id: str

    provider: str
    schema_version: str
    account_id: str
    account_name: str | None

    service: str
    service_raw: str
    resource_id: str | None
    resource_type: str | None
    region: str | None

    usage_start: datetime | None
    usage_end: datetime | None
    billing_period: str

    cost: Decimal
    cost_discounted: Decimal
    currency: str

    usage_amount: Decimal | None
    usage_unit: str | None

    tags: dict
    is_correction: bool
    is_estimate: bool

    raw_payload: dict
    source_file: str
    line_hash: str

    # ── Exports detalhados (CUR, BigQuery, Azure, OCI) ──────────────────
    # Opcionais: parser legado não os preenche e continua como antes.
    # Descritor por SKU (`BoxUsage:m7g.2xlarge`, `N2 Instance Core running
    # in Americas`). É o que a escada de SKU casa; antes o enriquecimento o
    # procurava no raw_payload pelo nome da coluna de cada formato.
    usage_type: str | None
    # Natureza da linha — ver `value_objects/charge_kind.py`.
    charge_kind: str | None
    # Valor como aparece na fatura (unblended). `cost` é o amortizado.
    cost_billed: Decimal | None
    # Fatos MEDIDOS pelo provedor: vcpus, memory_gib, instance_type,
    # project_id, first_usage… Nunca estimativa — estimativa gravada aqui
    # chegaria a jusante como fato.
    attributes: dict[str, object]


NORMALIZED_COLUMNS: tuple[str, ...] = (
    "user_id",
    "job_id",
    "provider",
    "schema_version",
    "account_id",
    "account_name",
    "service",
    "service_raw",
    "resource_id",
    "resource_type",
    "region",
    "usage_start",
    "usage_end",
    "billing_period",
    "cost",
    "cost_discounted",
    "currency",
    "usage_amount",
    "usage_unit",
    "tags",
    "is_correction",
    "is_estimate",
    "raw_payload",
    "source_file",
    "line_hash",
    "usage_type",
    "charge_kind",
    "cost_billed",
    "attributes",
)
