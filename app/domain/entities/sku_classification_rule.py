from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class SkuClassificationRule:
    """A data-driven rule that maps a billing descriptor to a component_type.

    Rules are evaluated by ``priority`` ascending (lower runs first); the first
    matching rule wins. ``kind`` selects the matching strategy:

    - ``glob``  — ``fnmatch`` against the (case-folded) descriptor
    - ``regex`` — ``re.search`` against the (case-folded) descriptor

    See ``docs/refactor/sku-accuracy-spec.md`` §4.1.
    """

    provider: str
    pattern: str
    kind: str  # 'glob' | 'regex'
    component_type: str
    family: str | None = None
    confidence: str = "medium"
    priority: int = 100
    active: bool = True
    created_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
