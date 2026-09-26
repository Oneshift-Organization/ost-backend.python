from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.domain.entities.workload_pool import AuxiliaryCosts, WorkloadPool
from app.domain.interfaces.i_service_repositories import (
    IServiceComparisonRepository,
    IServiceMetadataRepository,
)
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.repositories.i_exchange_rate_repo import IExchangeRateRepository
from app.domain.services.alertas import avaliar_cambio
from app.domain.services.category_calculator import (
    CategoryRate,
    estimate_by_cost_ratio,
)
from app.domain.services.cobertura_por_compromisso import avaliar_cobertura
from app.domain.services.confidence import avaliar_confianca
from app.domain.services.currency import (
    ANALYSIS_CURRENCY,
    FxRate,
    as_ledger,
    convert,
    identity_rate,
    taxa_desconhecida,
)
from app.domain.services.equivalence_matcher import EquivalenceMatcher
from app.domain.services.migration_expectation import (
    derive_expectation,
    learn_class_defaults,
)
from app.domain.services.pool_aggregation import aggregate_by_family
from app.domain.services.provider_consolidation import (
    ProviderTally,
    consolidate,
    weakest_confidence,
)
from app.domain.services.reconciliation import reconcile
from app.domain.services.sku_normalization import squash_key
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
    def __init__(  # noqa: PLR0917 -- existing dependency-injection contract
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingEnrichedRepository,
        metadata_repo: IServiceMetadataRepository,
        comparison_repo: IServiceComparisonRepository,
        sku_repo: ICloudSkuRepository | None = None,
        fx_repo: IExchangeRateRepository | None = None,
        price_repo: ICloudPricePointRepository | None = None,
    ) -> None:
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo
        self._matcher = (
            EquivalenceMatcher(sku_repo, price_repo) if sku_repo else None
        )
        self._aggregator = BillingAggregator(sku_repo) if sku_repo else None
        self._fx_repo = fx_repo
        self._price_repo = price_repo

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
        currency = (
            max(currency_counts, key=lambda c: currency_counts[c]) if currency_counts else "USD"
        )

        # ── 2. Load source catalog ───────────────────────────────
        source_comparisons = await self._comparison_repo.list_by_provider(source_provider)
        source_metadata = await self._metadata_repo.list_by_providers([source_provider])
        canonicals = {_normalize(c.service_name): c for c in source_comparisons}
        meta_map = {_normalize(m.service_name): m for m in source_metadata}
        # Índice insensível a espaço/pontuação. Os parsers emitem nomes
        # concatenados (`AmazonRDS`) e o catálogo curado usa nomes com espaço
        # (`Amazon RDS`); sem esta ponte o casamento falha e o `service_type` cai
        # no palpite do classificador de SKU — foi assim que object storage virou
        # block storage. Lista (não dict) porque um serviço aparece uma vez por
        # capacidade (BigQuery = interactive_query + data_lake + data_warehouse).
        canonicals_squashed: dict[str, list[ServiceComparison]] = {}
        for c in sorted(source_comparisons, key=lambda x: (x.category, x.service_type)):
            canonicals_squashed.setdefault(squash_key(c.service_name), []).append(c)
        meta_squashed = {squash_key(m.service_name): m for m in source_metadata}

        # ── 3. Load target provider metadata ─────────────────────
        # `sorted`, não `list(set(...))`: a ordem de iteração de um set varia
        # entre processos (randomização de hash) e se propaga para a ordem dos
        # `equivalentes` de TODOS os pools do payload. Isso tornava a request
        # não-reproduzível e injetava ruído na entrada do modelo — dois runs do
        # mesmo CSV produziam a mesma verdade em ordem diferente.
        target_providers = sorted(
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

        # Padrão de migração POR CLASSE, aprendido dos serviços já curados.
        # A dificuldade é propriedade do tipo (object storage é fácil em qualquer
        # nuvem; banco gerenciado é difícil em qualquer nuvem), então serviços sem
        # metadata própria herdam do tipo em vez de ficarem sem expectativa.
        _tipo_por_servico = {
            (c.provider, _normalize(c.service_name)): c.service_type
            for c in await self._comparison_repo.list_all()
        }
        class_defaults = learn_class_defaults(
            (
                _tipo_por_servico.get((m.provider, _normalize(m.service_name))),
                m.metadata,
            )
            for m in [*source_metadata, *target_metadata_all]
        )

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

        def _match(  # noqa: PLR0911 -- existing ordered catalog-matching fallbacks
            svc_name: str, hint_type: str | None = None
        ) -> ServiceComparison | None:
            name = _canonical_name(svc_name)
            n = _normalize(name)
            comp = canonicals.get(n)
            if comp:
                return comp
            # Ponte concatenado <-> espaçado. Quando o serviço tem várias
            # capacidades, prefere a que bate com o tipo que o classificador
            # sugeriu; senão a primeira em ordem determinística.
            candidates = canonicals_squashed.get(squash_key(name))
            if candidates:
                if hint_type:
                    for cand in candidates:
                        if cand.service_type == hint_type:
                            return cand
                return candidates[0]
            if meta_map.get(n) or meta_squashed.get(squash_key(name)):
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
            comp = _match(raw["service_name"], raw.get("service_type"))
            category_name = comp.category if comp else (
                raw.get("category") or _meta_category(raw["service_name"]) or "unknown"
            )
            service_type = comp.service_type if comp else (
                raw.get("service_type") or "unknown"
            )

            # Instrumento financeiro, não recurso migrável: custo sem NENHUMA
            # quantidade de uso (Savings Plan, RI, CUD, créditos, ajustes). Mesmo
            # invariante do balde `commitment` no agregador — sugerir "migre seu
            # Savings Plan para Compute Engine" seria conselho sem sentido.
            # Regra estrutural: nenhum nome de serviço/nuvem é citado.
            sem_uso = not any(
                (i.usage_amount or Decimal("0")) > 0 for i in raw["items"]
            )

            equivs: list[EquivalentService] = []
            if not sem_uso and category_name != "unknown" and service_type != "unknown":
                equivs = await _get_equivalents(category_name, service_type)

            # Expectativa de migração (classe 6R + esforço) a partir da metadata
            # curada do serviço de ORIGEM. Instrumento financeiro não migra, então
            # não recebe expectativa. Derivação determinística no backend.
            svc_meta = meta_map.get(_normalize(_canonical_name(raw["service_name"])))
            if svc_meta is None:
                svc_meta = meta_squashed.get(squash_key(_canonical_name(raw["service_name"])))
            expectation = None
            if not sem_uso:
                expectation = derive_expectation(svc_meta.metadata) if svc_meta else None
                if expectation is None:
                    # Herda o padrão do tipo (marcado como fonte="classe").
                    expectation = class_defaults.get(service_type)

            svc = BillingItemSummary(
                service_name=raw["service_name"],
                provider=raw["provider"],
                total_cost=raw["total_cost"],
                service_type=service_type,
                currency=raw["currency"],
                line_count=raw["line_count"],
                equivalents=equivs,
                migration_expectation=expectation.as_dict() if expectation else None,
                type_source="curado" if comp is not None else "inferido",
                migration_metadata=svc_meta.metadata if svc_meta and not sem_uso else None,
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
                    and (
                        matched.source_specs.vcpus is not None
                        or matched.source_specs.ram_gb is not None
                    )
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
                            price_source=getattr(_f, "price_source", None),
                            price_captured_at=getattr(_f, "price_captured_at", None),
                        )
                    equiv_items.append(
                        EquivalentService(
                            provider=eq.provider,
                            service_name=eq.service_name,
                            category=eq.category,
                            service_type=eq.service_type,
                            sku_id=getattr(eq, "sku_id", None),
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
                            price_source=opt.financials.price_source,
                            price_captured_at=opt.financials.price_captured_at,
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
                        sku_id=opt.sku_id,
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

        # ── 9b. Categorias não-compute: sizing por razão de taxa ──
        # Storage/egress não têm hardware; reprecificamos o custo (confiável) pela
        # razão de taxas por provedor. Provider-agnóstico, sem lógica por nuvem.
        category_equivalents: dict[str, list[dict[str, Any]]] = {}
        if auxiliary is not None and self._price_repo is not None:
            aux_by_category = {
                "storage_ssd": auxiliary.storage_ssd,
                "storage_hdd": auxiliary.storage_hdd,
                "object_storage": auxiliary.object_storage,
                "network_egress": auxiliary.network_egress,
                "database": auxiliary.database,
            }
            for category, aux_cost in aux_by_category.items():
                if aux_cost <= 0:
                    continue
                rate_points = await self._price_repo.rates_for_category(category)
                if not rate_points:
                    continue
                # Um provedor pode ter taxa official (fetch) E reference (seed).
                # A official prevalece — ordenamos para ela ser aplicada por último.
                _rank = {"official": 2, "reference": 1, "low": 0}
                rates: dict[str, CategoryRate] = {}
                for p in sorted(rate_points, key=lambda x: _rank.get(x.confidence, 0)):
                    rates[p.provider.lower()] = CategoryRate(
                        provider=p.provider.lower(),
                        rate=p.price,
                        unit=p.unit,
                        confidence=p.confidence,
                    )
                source_cost = convert(aux_cost, fx) or Decimal("0")
                equivs = estimate_by_cost_ratio(
                    source_provider=source_provider,
                    source_cost=source_cost,
                    rates=rates,
                )
                if equivs:
                    category_equivalents[category] = [
                        {
                            "provedor": e.provider,
                            "custo_mensal_estimado": round(float(e.estimated_monthly_cost), 2),
                            "delta_custo": round(float(e.cost_delta), 2),
                            "confianca_preco": e.pricing_confidence,
                        }
                        for e in equivs
                    ]

        # ── 10. Ledger de premissas (auditoria) ───────────────────
        mapped_cost = total_spend - (auxiliary.orphan if auxiliary else Decimal("0"))
        cobertura = float(mapped_cost / total_spend) if total_spend > 0 else 0.0
        # Confiança do preço é POR-PROVEDOR: 'official' quando veio de
        # cloud_price_points (API oficial), 'low' quando do catálogo seed.
        confianca_por_provedor: dict[str, str] = {}
        for equivs in family_equivalents.values():
            for e in equivs:
                if e.financials is not None:
                    confianca_por_provedor[e.provider.lower()] = (
                        e.financials.pricing_confidence
                    )
        # Cobertura de ANÁLISE: fração do gasto que tem equivalentes cross-cloud
        # (compute + categorias reprecificadas), vs. classificação apenas. É o que
        # mede o avanço da Wave 4: sair de balde opaco para comparação.
        analyzed = sum(
            (convert(fp.monthly_cost, fx) or Decimal("0") for fp in family_pools),
            Decimal("0"),
        )
        for category in category_equivalents:
            analyzed += convert(getattr(auxiliary, category, Decimal("0")), fx) or Decimal("0")
        total_analysis = convert(total_spend, fx) or Decimal("0")
        cobertura_analise = float(analyzed / total_analysis) if total_analysis > 0 else 0.0

        # ── 9c. Consolidação por provedor (backend calcula, IA narra) ──
        # Cada provedor só reprecifica o que ELE cobre; o resto fica ao custo atual.
        _tally: dict[str, dict[str, Any]] = {}
        # Custo cotado agrupado pelo rótulo de confiança do PREÇO. É o insumo da
        # confiança da análise (domain/services/confidence.py): a pergunta que
        # ele responde é "que fração do dinheiro cotado veio de preço oficial?".
        _custo_por_confianca: dict[str, Decimal] = {}

        def _add(provider: str, analyzed_cost: Decimal, source_cost: Decimal, conf: str) -> None:
            acc = _tally.setdefault(
                provider,
                {"analyzed": Decimal("0"), "covered": Decimal("0"), "confs": []},
            )
            acc["analyzed"] += analyzed_cost
            acc["covered"] += source_cost
            acc["confs"].append(conf)
            _custo_por_confianca[conf] = (
                _custo_por_confianca.get(conf, Decimal("0")) + analyzed_cost
            )

        family_src = {
            fp.pool_key.as_str(): convert(fp.monthly_cost, fx) or Decimal("0")
            for fp in family_pools
        }
        for fkey, equivs in family_equivalents.items():
            src = family_src.get(fkey, Decimal("0"))
            for e in equivs:
                fin = e.financials
                if fin is not None and fin.estimated_monthly_cost is not None:
                    _add(
                        e.provider.lower(),
                        fin.estimated_monthly_cost,
                        src,
                        fin.pricing_confidence,
                    )
        for category, cat_equivs in category_equivalents.items():
            src = convert(getattr(auxiliary, category, Decimal("0")), fx) or Decimal("0")
            for ce in cat_equivs:
                _add(
                    str(ce["provedor"]).lower(),
                    Decimal(str(ce["custo_mensal_estimado"])),
                    src,
                    str(ce["confianca_preco"]),
                )

        tallies = {
            provider: ProviderTally(
                analyzed_cost=acc["analyzed"],
                source_covered=acc["covered"],
                confidence=weakest_confidence(acc["confs"]),
            )
            for provider, acc in _tally.items()
        }
        resumo_executivo = consolidate(
            source_provider=source_provider,
            current_total=total_analysis,
            tallies=tallies,
        )

        # ── 10b. Alertas do backend contra os próprios insumos ────
        # O agente pode contestar um número nosso; aqui o backend contesta a si
        # mesmo. Sem isto, um bill sem taxa de câmbio era convertido por 1 —
        # somando moedas diferentes — e o rótulo `SEM_TAXA_CAMBIO` morria no
        # ledger sem que ninguém lesse.
        alertas = [a for a in (avaliar_cambio(fx, datetime.now(UTC).date()),) if a]
        confianca = avaliar_confianca(_custo_por_confianca).com_rebaixamento(
            [a.titulo for a in alertas if a.rebaixa]
        )

        # Pools com capacidade medida e custo zero: o bill cobra aquelas
        # instâncias na linha do compromisso (Savings Plan, RI, CUD), não na
        # delas. A economia calculada para esses pools sai superestimada, porque
        # o comparativo assume origem de custo zero. Declarar é o que temos —
        # ratear o compromisso entre pools seria estimativa vestida de fato.
        cobertura_compromisso = avaliar_cobertura(
            {
                fp.pool_key.as_str(): (fp.capacity.vcpus, fp.monthly_cost)
                for fp in family_pools
            },
            auxiliary.commitment if auxiliary is not None else Decimal("0"),
        )

        premissas: dict[str, Any] = {
            "fx": as_ledger(fx),
            "moeda_origem": currency,
            "moeda_analise": ANALYSIS_CURRENCY,
            "janela_horas": float(family_pools[0].hours_window) if family_pools else 730.0,
            "fonte_precos": "cloud_price_points (oficial) + seed_catalog_pareto (fallback)",
            "confianca_precos": confianca_por_provedor or "low",
            # Confiança DA ANÁLISE, medida em vez de opinada: razão do custo
            # cotado com preço oficial sobre o custo total cotado. Antes esse
            # nível vinha do LLM — um modelo opinando sobre a qualidade dos dados
            # que ele próprio recebeu.
            "confianca_analise": confianca.as_ledger(),
            # Sugestões que o backend levanta sozinho. Vão para a mesma lista de
            # tela que as do agente, com `origem: backend` — quem lê precisa
            # saber que existe uma dúvida, não quem a levantou.
            "sugestoes_backend": [a.as_sugestao() for a in alertas],
            "cobertura_classificacao": cobertura,
            "cobertura_analise": cobertura_analise,
            # Só aparece quando existe — um bill sem compromisso não precisa
            # carregar a explicação de um problema que ele não tem.
            **(
                {"cobertura_por_compromisso": cobertura_compromisso.as_ledger()}
                if cobertura_compromisso.existe
                else {}
            ),
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
        # Fração do gasto cujo tipo é palpite do classificador, não catálogo curado.
        custo_inferido = sum(
            (sv.total_cost for c in categories.values() for sv in c["services"]
             if getattr(sv, "type_source", "curado") != "curado"),
            Decimal("0"),
        )
        fracao_inferida = (
            float(custo_inferido / total_spend) if total_spend > 0 else 0.0
        )
        premissas["tipo_servico_inferido"] = fracao_inferida

        premissas["reconciliacao"] = reconcile(
            total_spend=total_spend,
            family_pools=family_pools,
            auxiliary=auxiliary,
            family_equivalents=family_equivalents,
            target_providers=target_providers,
            cobertura_classificacao=cobertura,
            fx=fx,
            fracao_tipo_inferido=fracao_inferida,
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
            category_equivalents=category_equivalents,
            analysis_currency=ANALYSIS_CURRENCY,
            premissas=premissas,
            resumo_executivo=resumo_executivo,
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
        # Preferimos sinalizar do que devolver número errado — `avaliar_cambio`
        # transforma esse rótulo em rebaixamento de confiança e aviso na tela.
        return taxa_desconhecida(par)
