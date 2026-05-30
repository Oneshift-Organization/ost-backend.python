"""Seed initial SKU classification rules (Matching Ladder L2).

Patterns are written case-folded; the SkuMatcher case-folds the billing
descriptor before matching (see ``docs/refactor/sku-accuracy-spec.md`` §4).
Lower ``priority`` is evaluated first, so specific service rules precede the
generic compute core/ram rules. ``family=None`` means the matcher extracts the
machine family from the descriptor.

Idempotent: deletes existing rules for each provider, then re-inserts.
"""

from __future__ import annotations

from asyncio import run

from sqlalchemy import text

from app.infrastructure.database.session import get_sessionmaker

# (pattern, kind, component_type, family, confidence, priority)
GCP_RULES: list[tuple[str, str, str, str | None, str, int]] = [
    # ── Specific services first (low priority number = first) ──
    ("*cluster management fee*", "glob", "gke_control_plane", None, "high", 10),
    ("*kubernetes*management*", "glob", "gke_control_plane", None, "high", 11),
    ("*kubernetes engine*", "glob", "gke_control_plane", None, "medium", 12),
    ("*gke*", "glob", "gke_control_plane", None, "medium", 13),
    ("*cloud sql*", "glob", "database", None, "high", 20),
    ("*db-custom*", "glob", "database", None, "high", 21),
    ("*bigquery*", "glob", "database", None, "medium", 22),
    ("*egress*", "glob", "network_egress", None, "high", 30),
    ("*inter-region*", "glob", "network_egress", None, "medium", 31),
    ("*network*data transfer*", "glob", "network_egress", None, "medium", 32),
    ("*ssd*", "glob", "storage_ssd", None, "high", 40),
    ("*pd capacity*", "glob", "storage_hdd", None, "medium", 50),
    ("*storage*", "glob", "storage_hdd", None, "low", 51),
    # ── OS licensing ──
    ("*windows*", "glob", "os_license", None, "high", 60),
    ("*rhel*", "glob", "os_license", None, "high", 61),
    ("*sles*", "glob", "os_license", None, "high", 62),
    ("*ubuntu pro*", "glob", "os_license", None, "high", 63),
    # ── Generic compute (family extracted by the matcher) ──
    ("*instance core running in*", "glob", "core", None, "high", 70),
    ("*instance ram running in*", "glob", "ram", None, "high", 71),
    ("*instance core*", "glob", "core", None, "medium", 72),
    ("*instance ram*", "glob", "ram", None, "medium", 73),
    ("*core running in*", "glob", "core", None, "medium", 74),
    ("*ram running in*", "glob", "ram", None, "medium", 75),
]


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(
            text("DELETE FROM sku_classification_rules WHERE provider = :p"),
            {"p": "GCP"},
        )
        total = 0
        for pattern, kind, component_type, family, confidence, priority in GCP_RULES:
            await session.execute(
                text("""
                    INSERT INTO sku_classification_rules
                        (provider, pattern, kind, component_type, family,
                         confidence, priority)
                    VALUES
                        (:provider, :pattern, :kind, :component_type, :family,
                         :confidence, :priority)
                """),
                {
                    "provider": "GCP",
                    "pattern": pattern,
                    "kind": kind,
                    "component_type": component_type,
                    "family": family,
                    "confidence": confidence,
                    "priority": priority,
                },
            )
            total += 1
        await session.commit()
        print(f"Seeded {total} GCP classification rules")  # noqa: T201


if __name__ == "__main__":
    run(main())
