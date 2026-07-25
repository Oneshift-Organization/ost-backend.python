from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.domain.analysis import (
    BillingItemSummary,
    EquivalentService,
    FinancialEstimate,
    MigrationContext,
    ServiceCategoryGroup,
    ServiceComparison,
    SourceUsage,
    TechnicalMatch,
    WorkloadSummary,
)
from app.domain.interfaces.i_service_repositories import (
    IServiceComparisonRepository,
    IServiceMetadataRepository,
)
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.repositories.i_exchange_rate_repo import IExchangeRateRepository
from app.domain.services.currency import (
    ANALYSIS_CURRENCY,
    FxRate,
    as_ledger,
    convert,
    identity_rate,
)
from app.domain.services.equivalence_matcher import EquivalenceMatcher
from app.domain.services.pool_aggregation import aggregate_by_family
from app.domain.services.reconciliation import reconcile
from app.services.billing_aggregator import BillingAggregator


@dataclass(frozen=True)
class BuildMigrationContextInput:
    user_id: UUID
    report_id: UUID


PROVIDER_SERVICE_ALIASES: dict[str, dict[str, str]] = {
    "aws": {
        "EC2-Instances": "Amazon EC2",
        "EC2-Other": "Amazon EC2",
        "EC2 Container Registry (ECR)": "Amazon Elastic Container Registry (ECR)",
        "Elastic Container Service for Kubernetes": "Amazon Elastic Kubernetes Service (EKS)",
        "Elastic Container Service": "Amazon Elastic Container Service (ECS)",
        "Relational Database Service": "Amazon RDS",
        "Elastic File System": "Amazon Elastic File System (EFS)",
        "Key Management Service": "AWS Key Management Service (KMS)",
        "Lambda": "AWS Lambda",
        "Elastic Load Balancing": "Elastic Load Balancing",
        "DMS": "AWS Database Migration Service",
        "Q": "Amazon Q Business",
        "DocumentDB (with MongoDB compatibility)": "Amazon DocumentDB",
        "CloudWatch Events": "Amazon CloudWatch",
        "Elastic Container Registry Public": "Amazon Elastic Container Registry (ECR)",
        "Kinesis Firehose": "Amazon Kinesis Data Streams",
        "Simple Workflow Service": "AWS Step Functions",
    },
}


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


# service types that require vCPU/RAM specs for equivalence matching
_COMPUTE_SERVICE_TYPES = frozenset({"virtual_server", "relational_database", "compute"})


class BuildMigrationContextUseCase:
    def __init__(
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingEnrichedRepository,
        metadata_repo: IServiceMetadataRepository,
        comparison_repo: IServiceComparisonRepository,
        sku_repo: ICloudSkuRepository | None = None,
        fx_repo: IExchangeRateRepository | None = None,
    ) -> None:
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo
        self._matcher = EquivalenceMatcher(sku_repo) if sku_repo else None
        self._aggregator = BillingAggregator(sku_repo) if sku_repo else None
        self._fx_repo = fx_repo

    async def execute(self, payload: BuildMigrationContextInput) -> MigrationContext:  # noqa: PLR0912, PLR0915
        report = await self._report_repo.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"report {payload.report_id} not found")
        if report.status != "done":
            raise ValueError(f"report {payload.report_id} status is {report.status}, expected done")
        if report.provider is None:
            raise ValueError(f"report {payload.report_id} has no provider set")

        source_provider = report.provider.value

        items = await self._item_repo.list_by_report(
            payload.report_id, payload.user_id, limit=100000, offset=0,
        )
        if not items:
            raise ValueError(f"report {payload.report_id} has no billing items")

        # Bug fix: use cost-weighted dominant period, not first item's period.
        # A CSV can span two months; we want the one with the most spend.
        period_costs: dict[str, Decimal] = {}
        currency_counts: dict[str, int] = {}
        total_spend = Decimal("0")

        # ── 1. Aggregate by service ──────────────────────────────
        service_agg: dict[str, dict[str, Any]] = {}
        for item in items:
            total_spend += item.cost
            period_costs[item.billing_period] = (
                period_costs.get(item.billing_period, Decimal("0")) + item.cost
            )
            currency_counts[item.currency] = currency_counts.get(item.currency, 0) + 1
            key = item.service_name
            if key not in service_agg:
                service_agg[key] = {
                    "service_name": item.service_name,
                    "provider": item.provider,
                    "total_cost": Decimal("0"),
                    "currency": item.currency,
                    "line_count": 0,
                    "category": item.category,
                    "service_type": item.service_type,
                    "items": [],
                }
            service_agg[key]["total_cost"] += item.cost
            service_agg[key]["line_count"] += 1
            service_agg[key]["items"].append(item)

        billing_period = max(period_costs, key=lambda p: period_costs[p])
        currency = max(currency_counts, key=lambda c: currency_counts[c]) if currency_counts else "USD"

        # ── 2. Load source catalog ───────────────────────────────
        source_comparisons = await self._comparison_repo.list_by_provider(source_provider)
        source_metadata = await self._metadata_repo.list_by_providers([source_provider])
        canonicals = {_normalize(c.service_name): c for c in source_comparisons}
        meta_map = {_normalize(m.service_name): m for m in source_metadata}

        # ── 3. Load target provider metadata ─────────────────────
        target_providers = list(
            {c.provider for c in await self._comparison_repo.list_all()
             if c.provider != source_provider}
        )
        target_metadata_all = await self._metadata_repo.list_by_providers(target_providers)
        target_meta_map: dict[tuple[str, str], Any] = {
            (m.provider, m.service_name): m.metadata for m in target_metadata_all
        }
        target_docs_map: dict[tuple[str, str], str | None] = {
            (m.provider, m.service_name): m.docs_costs for m in target_metadata_all
        }
        equivalents_cache: dict[tuple[str, str], list[EquivalentService]] = {}

        async def _get_equivalents(category: str, service_type: str) -> list[EquivalentService]:
            key = (category, service_type)
            if key not in equivalents_cache:
                raw = await self._comparison_repo.find_equivalents(category, source_provider)
                equivalents_cache[key] = [
                    EquivalentService(
                        provider=eq.provider,
                        service_name=eq.service_name,
                        category=category,
                        service_type=eq.service_type,
                        metadata=target_meta_map.get((eq.provider, eq.service_name)),
                        docs_costs=target_docs_map.get((eq.provider, eq.service_name)),
                    )
                    for eq in raw
                    if eq.service_type == service_type
                ]
            return equivalents_cache[key]

        # ── 4. Match helpers ─────────────────────────────────────
        def _canonical_name(name: str) -> str:
            aliases = PROVIDER_SERVICE_ALIASES.get(source_provider, {})
            return aliases.get(name, name)

        def _match(svc_name: str) -> ServiceComparison | None:
            n = _normalize(_canonical_name(svc_name))
            comp = canonicals.get(n)
            if comp:
                return comp
            if meta_map.get(n):
                return None
            for canon_name, comp in canonicals.items():
                if n in canon_name or canon_name in n:
                    return comp
            for canon_name in meta_map:
                if n in canon_name or canon_name in n:
                    return None
            return None

        def _meta_category(name: str) -> str | None:
            meta = meta_map.get(_normalize(name))
            return meta.category if meta else None

        # ── 5. Build old-style (categories) structure ────────────
        sorted_services = sorted(
            service_agg.values(),
            key=lambda x: x["total_cost"],
            reverse=True,
        )

        categories: dict[str, dict[str, Any]] = {}
        for raw in sorted_services:
            comp = _match(raw["service_name"])
            category_name = comp.category if comp else (
                raw.get("category") or _meta_category(raw["service_name"]) or "unknown"
            )
            service_type = comp.service_type if comp else (
                raw.get("service_type") or "unknown"
            )

            equivs: list[EquivalentService] = []
            if category_name != "unknown" and service_type != "unknown":
                equivs = await _get_equivalents(category_name, service_type)

            svc = BillingItemSummary(
                service_name=raw["service_name"],
                provider=raw["provider"],
                total_cost=raw["total_cost"],
                service_type=service_type,
                currency=raw["currency"],
                line_count=raw["line_count"],
                equivalents=equivs,
            )

            if category_name not in categories:
                categories[category_name] = {
                    "category": category_name,
                    "billing_total": Decimal("0"),
                    "services": [],
                }
            categories[category_name]["services"].append(svc)
            categories[category_name]["billing_total"] += svc.total_cost

        # ── 6. Build new-style (workloads) structure ─────────────
        workloads: list[WorkloadSummary] = []
        missing_sku_set: set[str] = set()
        if self._matcher is not None:
            sku_agg: dict[str, dict[str, Any]] = {}
            for item in items:
                sku_key = item.sku_id or item.service_name
                if sku_key not in sku_agg:
                    sku_agg[sku_key] = {
                        "sku_id": item.sku_id or item.service_name,
                        "usage_quantity": Decimal("0"),
                        "billed_cost": Decimal("0"),
                        "currency": item.currency,
                        "canonical_service": item.service_type or item.service_name,
                        "line_count": 0,
                    }
                sku_agg[sku_key]["usage_quantity"] += item.usage_amount or Decimal("0")
                sku_agg[sku_key]["billed_cost"] += item.cost
                sku_agg[sku_key]["line_count"] += 1

            for _sku_key, agg in sku_agg.items():
                matched = await self._matcher.match(
                    source_provider=source_provider,
                    sku_id=agg["sku_id"],
                    usage_quantity=agg["usage_quantity"],
                    billed_cost=agg["billed_cost"],
                    currency=agg["currency"],
                    canonical_service=agg["canonical_service"],
                    target_providers=target_providers,
                )

                has_specs = (
                    matched.source_specs is not None
                    and (matched.source_specs.vcpus is not None or matched.source_specs.ram_gb is not None)
                )
                if not has_specs and matched.canonical_service in _COMPUTE_SERVICE_TYPES:
                    missing_sku_set.add(agg["sku_id"])

                source_usage = SourceUsage(
                    sku_id=matched.sku_id,
                    usage_quantity=matched.usage_quantity,
                    billed_cost=matched.billed_cost,
                    currency=matched.currency,
                )

                equiv_items = []
                for eq in matched.equivalents:
                    _t = eq.technical_match
                    tm = TechnicalMatch(
                        vcpus=_t.vcpus,
                        ram_gb=_t.ram_gb,
                    ) if _t else None
                    _f = eq.financials
                    fin = None
                    if _f is not None:
                        fin = FinancialEstimate(
                            unit_price=_f.unit_price,
                            estimated_monthly_cost=_f.estimated_monthly_cost,
                            cost_delta=_f.cost_delta,
                            pricing_confidence=_f.pricing_confidence,
                        )
                    equiv_items.append(
                        EquivalentService(
                            provider=eq.provider,
                            service_name=eq.service_name,
                            category=eq.category,
                            service_type=eq.service_type,
                            technical_match=tm,
                            financials=fin,
                        )
                    )
                workloads.append(
                    WorkloadSummary(
                        canonical_service=matched.canonical_service,
                        source_usage=source_usage,
                        equivalents=equiv_items,
                    )
                )

        # ── 7. Aggregate into capacity pools ──────────────────────
        pools: tuple[WorkloadPool, ...] = ()
        auxiliary: AuxiliaryCosts | None = None
        mapping_confidence: dict[str, Decimal] = {}
        estimated_capacity_cost = Decimal("0")
        if self._aggregator is not None:
            agg_result = await self._aggregator.aggregate(items, source_provider)
            pools = agg_result.pools
            auxiliary = agg_result.auxiliary
            mapping_confidence = agg_result.confidence_breakdown
            estimated_capacity_cost = agg_result.estimated_capacity_cost

        # ── 8. Câmbio: normaliza custos para a moeda de análise ───
        # Nunca comparar BRL de origem com USD de catálogo (Precision Engine A1).
        fx = await self._resolve_fx(currency)

        # ── 9. Agrega pools por família ANTES de dimensionar ──────
        # Dimensionar projeto a projeto e somar depois produzia hardware de um
        # projeto com custo de dezenove. Agregando primeiro, o matcher vê a
        # capacidade real da família.
        family_pools = aggregate_by_family(pools)

        family_equivalents: dict[str, list[EquivalentService]] = {}
        pool_equivalents: dict[str, list[EquivalentService]] = {}
        database_equivalents: list[EquivalentService] = []

        if self._matcher is not None and target_providers:
            curated_compute = await _get_equivalents("compute", "virtual_server")

            for fpool in family_pools:
                monthly_cost_analysis = convert(fpool.monthly_cost, fx) or Decimal("0")
                l2_options = await self._matcher.match_pool(
                    vcpus=fpool.capacity.vcpus,
                    ram_gib=fpool.capacity.ram_gib,
                    monthly_cost=monthly_cost_analysis,
                    service_type="virtual_server",
                    target_providers=target_providers,
                )
                providers_with_l2: set[str] = set()
                equivs: list[EquivalentService] = []

                for opt in l2_options:
                    providers_with_l2.add(opt.provider.lower())
                    # L3: enrich with curated metadata
                    meta = target_meta_map.get((opt.provider, opt.service_name))
                    docs = target_docs_map.get((opt.provider, opt.service_name))
                    fin = None
                    if opt.financials is not None:
                        fin = FinancialEstimate(
                            unit_price=opt.financials.unit_price,
                            estimated_monthly_cost=opt.financials.estimated_monthly_cost,
                            cost_delta=opt.financials.cost_delta,
                            pricing_confidence=opt.financials.pricing_confidence,
                        )
                    tm = None
                    if opt.technical_match is not None:
                        tm = TechnicalMatch(
                            vcpus=opt.technical_match.vcpus,
                            ram_gb=opt.technical_match.ram_gb,
                        )
                    equivs.append(EquivalentService(
                        provider=opt.provider,
                        service_name=opt.service_name,
                        category=opt.category,
                        service_type=opt.service_type,
                        match_basis="pool_capacity",
                        instances=opt.instances,
                        metadata=meta,
                        docs_costs=docs,
                        technical_match=tm,
                        financials=fin,
                    ))

                # L3-only curated fallback for providers without hardware match
                for ce in curated_compute:
                    if ce.provider.lower() in providers_with_l2:
                        continue
                    equivs.append(EquivalentService(
                        provider=ce.provider,
                        service_name=ce.service_name,
                        category=ce.category,
                        service_type=ce.service_type,
                        match_basis="curated_only",
                        metadata=ce.metadata,
                        docs_costs=ce.docs_costs,
                    ))

                family_equivalents[fpool.pool_key.as_str()] = equivs
                # Compat: expõe os mesmos equivalentes por projeto (drill-down
                # e formato legado). O dimensionamento continua sendo o da família.
                for proj in fpool.source_projects:
                    key = f"{proj}/{fpool.pool_key.machine_family}/{fpool.pool_key.region}"
                    pool_equivalents[key] = equivs

            # Database aux: L3 curated (no hardware sizing available at pool level)
            if auxiliary and auxiliary.database > 0:
                db_curated = await _get_equivalents("database", "relational_database")
                database_equivalents = list(db_curated)

        # ── 10. Ledger de premissas (auditoria) ───────────────────
        mapped_cost = total_spend - (auxiliary.orphan if auxiliary else Decimal("0"))
        cobertura = float(mapped_cost / total_spend) if total_spend > 0 else 0.0
        premissas: dict[str, Any] = {
            "fx": as_ledger(fx),
            "moeda_origem": currency,
            "moeda_analise": ANALYSIS_CURRENCY,
            "janela_horas": float(family_pools[0].hours_window) if family_pools else 730.0,
            "fonte_precos": "seed_catalog_pareto",
            "confianca_precos": "low",
            "cobertura_classificacao": cobertura,
            "fator_utilizacao": 1.0,
            # Fração do gasto de compute cuja capacidade foi estimada por shape
            # (instance-hours → vCPU/RAM) em vez de faturada por-vCPU.
            "dimensionamento_estimado": (
                float(convert(estimated_capacity_cost, fx) or Decimal("0"))
                / float(convert(total_spend, fx) or Decimal("1"))
                if total_spend > 0
                else 0.0
            ),
        }

        # ── 11. Reconciliação: a análise auditando a si mesma ─────
        # Roda sempre; grava violações/suspeitas em premissas. É o gate que
        # transforma "achei um bug lendo o CSV" em "a API sabe onde não confia".
        premissas["reconciliacao"] = reconcile(
            total_spend=total_spend,
            family_pools=family_pools,
            auxiliary=auxiliary,
            family_equivalents=family_equivalents,
            target_providers=target_providers,
            cobertura_classificacao=cobertura,
            fx=fx,
        )

        return MigrationContext(
            source_provider=source_provider,
            billing_period=billing_period,
            total_spend=total_spend,
            currency=currency,
            categories=[
                ServiceCategoryGroup(
                    category=c["category"],
                    billing_total=c["billing_total"],
                    services=c["services"],
                )
                for c in categories.values()
            ],
            workloads=workloads,
            pools=pools,
            auxiliary=auxiliary,
            missing_sku_ids=tuple(sorted(missing_sku_set)),
            mapping_confidence=mapping_confidence,
            pool_equivalents=pool_equivalents,
            database_equivalents=database_equivalents,
            family_pools=family_pools,
            family_equivalents=family_equivalents,
            analysis_currency=ANALYSIS_CURRENCY,
            premissas=premissas,
        )

    async def _resolve_fx(self, source_currency: str) -> FxRate:
        """Taxa origem→moeda de análise: tabela primeiro, config como fallback."""
        src = (source_currency or ANALYSIS_CURRENCY).upper()
        if src == ANALYSIS_CURRENCY:
            return identity_rate(src)

        par = f"{src}/{ANALYSIS_CURRENCY}"
        if self._fx_repo is not None:
            rate = await self._fx_repo.get_latest(par)
            if rate is not None:
                return rate

        settings = get_settings()
        configured = getattr(settings, f"fx_{src.lower()}_{ANALYSIS_CURRENCY.lower()}", None)
        if configured:
            return FxRate(par=par, taxa=Decimal(str(configured)), fonte="config")

        # Sem taxa conhecida: identidade seria comparar moedas diferentes.
        # Preferimos sinalizar do que devolver número errado.
        return FxRate(par=par, taxa=Decimal("1"), fonte="SEM_TAXA_CAMBIO")
