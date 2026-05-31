from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository

_HOURS_PER_MONTH = Decimal("730")


@dataclass(frozen=True)
class TechnicalMatch:
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None


@dataclass(frozen=True)
class FinancialEstimate:
    unit_price: Decimal | None = None
    estimated_monthly_cost: Decimal | None = None
    cost_delta: Decimal | None = None
    pricing_confidence: str = "low"


@dataclass(frozen=True)
class EquivalentOption:
    provider: str
    sku_id: str
    service_name: str
    service_type: str
    category: str
    instances: int = 1
    technical_match: TechnicalMatch | None = None
    financials: FinancialEstimate | None = None


@dataclass(frozen=True)
class MatchedWorkload:
    canonical_service: str
    sku_id: str
    usage_quantity: Decimal
    billed_cost: Decimal
    currency: str
    source_specs: CloudSku | None = None
    equivalents: list[EquivalentOption] = field(default_factory=list)


class EquivalenceMatcher:
    """Matches source SKUs to target equivalents by hardware specs.

    Provider-agnostic — no if/elif/else per cloud. Pure resource math:
    find SKUs with >= vcpus and >= ram_gb at the lowest unit price.
    """

    def __init__(self, sku_repo: ICloudSkuRepository) -> None:
        self._sku_repo = sku_repo

    async def get_equivalents(
        self,
        source_sku_id: str,
        source_provider: str,
        target_providers: list[str],
        usage_qty: Decimal,
        source_cost: Decimal,
    ) -> list[EquivalentOption]:
        """Return the cheapest equivalent for each target provider.

        Flow:
        1. Look up source SKU specs in cloud_sku_catalog.
        2. For each target provider, query candidates (>= vcpus, >= ram_gb).
        3. Pick the cheapest candidate per provider (min price_per_unit).
        4. Calculate estimated_cost and cost_delta for each.
        """
        source_sku = await self._sku_repo.get_by_id(source_provider, source_sku_id)
        if not source_sku or not source_sku.service_type:
            return []

        options: list[EquivalentOption] = []

        for provider in target_providers:
            candidates = await self._sku_repo.find_equivalents(
                provider=provider,
                service_type=source_sku.service_type,
                min_vcpus=source_sku.vcpus,
                min_ram_gb=source_sku.ram_gb,
                limit=10,
            )

            best = min(candidates, key=lambda x: x.price_per_unit or Decimal("0"), default=None)
            if best is None:
                continue

            unit_price = best.price_per_unit
            estimated_cost = (usage_qty * unit_price) if unit_price else None
            cost_delta = (estimated_cost - source_cost) if estimated_cost is not None else None

            options.append(
                EquivalentOption(
                    provider=best.provider,
                    sku_id=best.sku_id,
                    service_name=best.service_name,
                    service_type=best.service_type or "",
                    category=best.category or "",
                    technical_match=TechnicalMatch(
                        vcpus=best.vcpus,
                        ram_gb=best.ram_gb,
                    ),
                    financials=FinancialEstimate(
                        unit_price=unit_price,
                        estimated_monthly_cost=estimated_cost,
                        cost_delta=cost_delta,
                        pricing_confidence="high (mock)" if unit_price else "low",
                    ),
                )
            )

        return options

    async def match(
        self,
        *,
        source_provider: str,
        sku_id: str,
        usage_quantity: Decimal,
        billed_cost: Decimal,
        currency: str,
        canonical_service: str,
        target_providers: list[str] | None = None,
    ) -> MatchedWorkload:
        source_specs = await self._sku_repo.get_by_id(source_provider, sku_id)

        if target_providers is None:
            raw = await self._sku_repo.list_providers()
            target_providers = [p for p in raw if p.lower() != source_provider.lower()]

        equivalents = await self.get_equivalents(
            source_sku_id=sku_id,
            source_provider=source_provider,
            target_providers=target_providers,
            usage_qty=usage_quantity,
            source_cost=billed_cost,
        )

        return MatchedWorkload(
            canonical_service=canonical_service,
            sku_id=sku_id,
            usage_quantity=usage_quantity,
            billed_cost=billed_cost,
            currency=currency,
            source_specs=source_specs,
            equivalents=equivalents,
        )

    async def match_pool(
        self,
        *,
        vcpus: Decimal,
        ram_gib: Decimal,
        monthly_cost: Decimal,
        service_type: str,
        target_providers: list[str],
    ) -> list[EquivalentOption]:
        """Return the cheapest covering equivalent per target provider for a pool.

        Unlike get_equivalents, this does NOT require a source sku_id — it
        matches directly against pool capacity (spec §5 E1). For each provider:
        1. Query candidates with specs >= pool capacity.
        2. Pick the cheapest single SKU that covers the pool.
        3. If one SKU does not cover, scale with instances = ceil(max ratio).
        4. Compute estimated_monthly_cost and cost_delta deterministically.
        """
        options: list[EquivalentOption] = []

        min_vcpus = vcpus if vcpus > 0 else None
        min_ram_gb = ram_gib if ram_gib > 0 else None

        for provider in target_providers:
            candidates = await self._sku_repo.find_equivalents(
                provider=provider,
                service_type=service_type,
                min_vcpus=min_vcpus,
                min_ram_gb=min_ram_gb,
                limit=10,
            )

            if not candidates:
                # Relax constraints: take cheapest of that service_type
                candidates = await self._sku_repo.find_equivalents(
                    provider=provider,
                    service_type=service_type,
                    limit=10,
                )

            best = min(
                candidates,
                key=lambda x: x.price_per_unit or Decimal("999999"),
                default=None,
            )
            if best is None or best.price_per_unit is None:
                continue

            instances = _instances_needed(vcpus, ram_gib, best)
            estimated_monthly = best.price_per_unit * _HOURS_PER_MONTH * instances
            cost_delta = estimated_monthly - monthly_cost

            options.append(
                EquivalentOption(
                    provider=best.provider,
                    sku_id=best.sku_id,
                    service_name=best.service_name,
                    service_type=best.service_type or service_type,
                    category=best.category or "",
                    instances=instances,
                    technical_match=TechnicalMatch(
                        vcpus=(best.vcpus or Decimal("0")) * instances,
                        ram_gb=(best.ram_gb or Decimal("0")) * instances,
                    ),
                    financials=FinancialEstimate(
                        unit_price=best.price_per_unit,
                        estimated_monthly_cost=estimated_monthly,
                        cost_delta=cost_delta,
                        pricing_confidence="low",
                    ),
                )
            )

        return options


def _instances_needed(vcpus: Decimal, ram_gib: Decimal, cand: CloudSku) -> int:
    """How many candidate instances are needed to cover pool capacity."""
    ratios: list[float] = []
    if cand.vcpus and cand.vcpus > 0 and vcpus > 0:
        ratios.append(float(vcpus / cand.vcpus))
    if cand.ram_gb and cand.ram_gb > 0 and ram_gib > 0:
        ratios.append(float(ram_gib / cand.ram_gb))
    return max(1, math.ceil(max(ratios))) if ratios else 1
