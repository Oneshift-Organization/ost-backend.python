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
    ("*kubernetes*", "glob", "gke_control_plane", None, "medium", 14),
    # ── Database ──
    ("*cloud sql*", "glob", "database", None, "high", 20),
    ("*db-custom*", "glob", "database", None, "high", 21),
    ("*bigquery*", "glob", "database", None, "medium", 22),
    ("*bigtable*", "glob", "database", None, "medium", 23),
    ("*firestore*", "glob", "database", None, "medium", 24),
    ("*datastore*", "glob", "database", None, "medium", 25),
    ("*spanner*", "glob", "database", None, "medium", 26),
    ("*memorystore*", "glob", "database", None, "medium", 27),
    # ── Networking (egress + LB + VPN + IPs) ──
    ("*egress*", "glob", "network_egress", None, "high", 30),
    ("*inter-region*", "glob", "network_egress", None, "medium", 31),
    ("*data transfer*", "glob", "network_egress", None, "high", 32),
    ("*load balancer*", "glob", "network_egress", None, "high", 33),
    ("*forwarding rule*", "glob", "network_egress", None, "high", 34),
    ("*vpn*", "glob", "network_egress", None, "high", 35),
    ("*static ip*", "glob", "network_egress", None, "high", 36),
    ("*external ip*", "glob", "network_egress", None, "high", 37),
    ("*ip charge*", "glob", "network_egress", None, "medium", 38),
    ("*cloud nat*", "glob", "network_egress", None, "medium", 39),
    # ── Storage ──
    ("*ssd*", "glob", "storage_ssd", None, "high", 40),
    ("*pd capacity*", "glob", "storage_hdd", None, "medium", 50),
    ("*filestore*", "glob", "storage_hdd", None, "high", 51),
    ("*snapshot*", "glob", "storage_hdd", None, "medium", 52),
    ("*storage*", "glob", "storage_hdd", None, "low", 53),
    # ── OS licensing ──
    ("*windows*", "glob", "os_license", None, "high", 60),
    ("*rhel*", "glob", "os_license", None, "high", 61),
    ("*sles*", "glob", "os_license", None, "high", 62),
    ("*ubuntu pro*", "glob", "os_license", None, "high", 63),
    ("*licensing*", "glob", "os_license", None, "medium", 64),
    # ── Cloud Run / request-based compute ──
    ("*min instance cpu*", "glob", "core", None, "medium", 66),
    ("*min instance memory*", "glob", "ram", None, "medium", 67),
    ("*instance cpu*", "glob", "core", None, "low", 68),
    ("*instance memory*", "glob", "ram", None, "low", 69),
    # ── Generic compute (family extracted by the matcher) ──
    ("*instance core running in*", "glob", "core", None, "high", 70),
    ("*instance ram running in*", "glob", "ram", None, "high", 71),
    ("*instance core*", "glob", "core", None, "medium", 72),
    ("*instance ram*", "glob", "ram", None, "medium", 73),
    ("*core running in*", "glob", "core", None, "medium", 74),
    ("*ram running in*", "glob", "ram", None, "medium", 75),
]

# (pattern, kind, component_type, family, confidence, priority) — AWS UsageType.
AWS_RULES: list[tuple[str, str, str, str | None, str, int]] = [
    # ── Database (before storage so RDS:*-Storage maps to database) ──
    ("rds:*", "glob", "database", None, "high", 10),
    ("aurora:*", "glob", "database", None, "high", 11),
    ("*instanceusage:db*", "glob", "database", None, "high", 12),
    ("*multi-az*", "glob", "database", None, "medium", 13),
    ("elasticache:*", "glob", "database", None, "medium", 14),
    ("dynamodb*", "glob", "database", None, "medium", 15),
    ("*nodeusage:cache*", "glob", "database", None, "high", 16),
    ("*kafka*", "glob", "database", None, "medium", 17),
    ("*elasticsearch*", "glob", "database", None, "medium", 18),
    ("*opensearch*", "glob", "database", None, "medium", 19),
    # ── Compute (instances + commitments) ──
    ("*boxusage:*", "glob", "core", None, "high", 20),
    ("boxusage:*", "glob", "core", None, "high", 21),
    ("*spotusage*", "glob", "core", None, "medium", 22),
    ("ec2sp:*", "glob", "core", None, "medium", 23),
    ("computesp:*", "glob", "core", None, "medium", 24),
    ("*heavyusage:*", "glob", "core", None, "medium", 25),
    ("*dedicatedusage:*", "glob", "core", None, "medium", 26),
    ("savingsplan*", "glob", "core", None, "low", 27),
    # ── Containers (EKS/Fargate control plane) ──
    ("*amazoneks*", "glob", "gke_control_plane", None, "high", 18),
    ("*eks*percluster*", "glob", "gke_control_plane", None, "high", 19),
    ("*fargate*", "glob", "core", None, "medium", 28),
    # ── Storage ──
    ("*volumeusage*", "glob", "storage_ssd", None, "high", 30),
    ("ebs:*", "glob", "storage_ssd", None, "high", 31),
    ("*timedstorage*", "glob", "storage_hdd", None, "high", 32),
    ("*-bytehrs", "glob", "storage_hdd", None, "medium", 33),
    ("*snapshotusage*", "glob", "storage_hdd", None, "medium", 34),
    ("*storage*", "glob", "storage_hdd", None, "low", 39),
    # ── Networking ──
    ("natgateway*", "glob", "network_egress", None, "high", 40),
    ("*natgateway*", "glob", "network_egress", None, "high", 41),
    ("*datatransfer*", "glob", "network_egress", None, "high", 42),
    ("*-out-bytes", "glob", "network_egress", None, "medium", 43),
    ("*-in-bytes", "glob", "network_egress", None, "medium", 44),
    ("*loadbalancer*", "glob", "network_egress", None, "medium", 45),
    ("*lcubytes*", "glob", "network_egress", None, "medium", 46),
    ("*lcuusage*", "glob", "network_egress", None, "medium", 47),
    ("*resolvernetworkinterface*", "glob", "network_egress", None, "medium", 48),
    ("*-bytes", "glob", "network_egress", None, "low", 49),
    # ── S3 / API requests (ops) ──
    ("*request*", "glob", "storage_hdd", None, "low", 50),
]

_SEEDS: dict[str, list[tuple[str, str, str, str | None, str, int]]] = {
    "GCP": GCP_RULES,
    "AWS": AWS_RULES,
}


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        total = 0
        for provider, rules in _SEEDS.items():
            await session.execute(
                text("DELETE FROM sku_classification_rules WHERE provider = :p"),
                {"p": provider},
            )
            for pattern, kind, component_type, family, confidence, priority in rules:
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
                        "provider": provider,
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
        print(f"Seeded {total} classification rules across {len(_SEEDS)} providers")  # noqa: T201


if __name__ == "__main__":
    run(main())
