from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository

_HOURS_PER_MONTH = Decimal("730")
# Peso do desperdício no score de packing. 0 = escolhe sempre o mais barato
# (comportamento antigo); valores altos priorizam ajuste fino sobre preço.
_WASTE_PENALTY = Decimal("0.30")
# Teto de instâncias por mix. É *relativo*: o que torna uma proposta inoperável
# não é o número absoluto de instâncias (um pool de 228 vCPU precisa de muitas),
# e sim a fragmentação — escolher um SKU minúsculo quando existe um que cobre o
# pool com poucas máquinas. O teto é então
#   max(_MIN_INSTANCE_ALLOWANCE, menor_mix_viável x _FRAGMENTATION_FACTOR).
_MIN_INSTANCE_ALLOWANCE = 12
_FRAGMENTATION_FACTOR = 3
# Quantos SKUs por provider o packer avalia (ordenados por preço no repo).
_CANDIDATE_LIMIT = 60


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
        pricing_confidence: str = "low",
    ) -> list[EquivalentOption]:
        """Return the best-value covering mix per target provider for a pool.

        ``monthly_cost`` MUST already be expressed in the analysis currency
        (see ``domain/services/currency.py``) — this method does no conversion
        and would otherwise produce a currency-mixed ``cost_delta``.

        Selection is a small bin-packing: every candidate SKU is scored by the
        **total monthly cost of the mix that covers the pool**, penalised for
        over-provisioning. Picking the cheapest SKU per unit (the previous
        behaviour) produced absurd mixes like 14x1vCPU/15GB for a 13vCPU/52GiB
        pool — technically covering, financially cheap, architecturally wrong.
        """
        options: list[EquivalentOption] = []

        for provider in target_providers:
            # Query unfiltered: the packer needs both SKUs that cover alone and
            # smaller ones it can multiply. Filtering by min_vcpus here would
            # hide every option that only covers when scaled.
            candidates = await self._sku_repo.find_equivalents(
                provider=provider,
                service_type=service_type,
                limit=_CANDIDATE_LIMIT,
            )

            best = _best_packing(vcpus, ram_gib, candidates)
            if best is None:
                continue

            cand, instances, estimated_monthly = best
            cost_delta = estimated_monthly - monthly_cost

            options.append(
                EquivalentOption(
                    provider=cand.provider,
                    sku_id=cand.sku_id,
                    service_name=cand.service_name,
                    service_type=cand.service_type or service_type,
                    category=cand.category or "",
                    instances=instances,
                    technical_match=TechnicalMatch(
                        vcpus=(cand.vcpus or Decimal("0")) * instances,
                        ram_gb=(cand.ram_gb or Decimal("0")) * instances,
                    ),
                    financials=FinancialEstimate(
                        unit_price=cand.price_per_unit,
                        estimated_monthly_cost=estimated_monthly,
                        cost_delta=cost_delta,
                        pricing_confidence=pricing_confidence,
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


def _waste_ratio(offered: Decimal, needed: Decimal) -> Decimal:
    """Fração de recurso ofertado além do necessário (0 = ajuste perfeito)."""
    if needed <= 0:
        return Decimal("0")
    excess = offered - needed
    return excess / needed if excess > 0 else Decimal("0")


def _best_packing(
    vcpus: Decimal,
    ram_gib: Decimal,
    candidates: list[CloudSku],
) -> tuple[CloudSku, int, Decimal] | None:
    """Escolhe (SKU, instâncias, custo_mensal) de menor custo total ajustado.

    Score = custo_mensal x (1 + λ x desperdício). O desperdício soma o excesso
    relativo de vCPU e de RAM, então um mix que entrega 4x a RAM necessária é
    penalizado mesmo sendo barato. Retorna None se nenhum candidato cobre a
    demanda dentro do teto de instâncias.
    """
    priced: list[tuple[CloudSku, int]] = []
    for cand in candidates:
        if cand.price_per_unit is None or cand.price_per_unit <= 0:
            continue
        # Um SKU sem specs de hardware não pode ser dimensionado contra o pool.
        if vcpus > 0 and not cand.vcpus:
            continue
        if ram_gib > 0 and not cand.ram_gb:
            continue
        priced.append((cand, _instances_needed(vcpus, ram_gib, cand)))

    if not priced:
        return None

    min_feasible = min(inst for _, inst in priced)
    cap = max(_MIN_INSTANCE_ALLOWANCE, min_feasible * _FRAGMENTATION_FACTOR)

    best: tuple[CloudSku, int, Decimal] | None = None
    best_score: Decimal | None = None

    for cand, instances in priced:
        if instances > cap:
            continue

        monthly = cand.price_per_unit * _HOURS_PER_MONTH * instances
        waste = _waste_ratio((cand.vcpus or Decimal("0")) * instances, vcpus) + _waste_ratio(
            (cand.ram_gb or Decimal("0")) * instances, ram_gib
        )
        score = monthly * (Decimal("1") + _WASTE_PENALTY * waste)

        if best_score is None or score < best_score:
            best_score = score
            best = (cand, instances, monthly)

    return best
