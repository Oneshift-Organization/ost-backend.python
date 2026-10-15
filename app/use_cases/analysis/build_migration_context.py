from __future__ import annotations

from dataclasses import dataclass, replace
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
from app.domain.services.alertas import avaliar_cambio, avaliar_regioes
from app.domain.services.categoria_por_zona import dividir_por_zona
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
from app.domain.services.perfil_de_familia import perfil
from app.domain.services.pool_aggregation import aggregate_by_family
from app.domain.services.premissas_detalhadas import (
    base_de_custo,
    desconto_origem_sobre_lista,
    fracao,
)
from app.domain.services.provider_consolidation import (
    ProviderTally,
    consolidate,
    weakest_confidence,
)
from app.domain.services.reconciliation import reconcile
from app.domain.services.regioes import (
    ZONA_PADRAO,
    escolher_regiao_de_preco,
    regiao_canonica,
    regiao_da_linha,
    regiao_dominante,
    zona_da_regiao,
)
from app.domain.services.service_identity import (
    financial_category,
    is_financial_component,
    service_key,
    service_type_hint,
)
from app.services.billing_aggregator import BillingAggregator
from app.use_cases.analysis.leitura_completa import listar_todos


@dataclass(frozen=True)
class BuildMigrationContextInput:
    user_id: UUID
    report_id: UUID


def _reviewed_billing_identity(item, provider: str):
    """Correct known aliases in memory; do not reprice a registry as a disk."""
    if is_financial_component(item.service_name, item.category or ""):
        category = financial_category(item.service_name, item.category or "")
        component = "commitment" if category in {"commitment", "compromisso"} else "unclassified"
        return replace(item, component_type=component, category=category)
    hint = service_type_hint(provider, item.service_name)
    if hint and hint[1] != item.service_type:
        return replace(item, category=hint[0], service_type=hint[1], component_type="unclassified")
    return item


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


_RANK_CONFIANCA = {"official": 2, "reference": 1, "low": 0}


def _somar_partes(
    cotadas: list[tuple[Any, dict[str, CategoryRate], list[Any], Decimal]],
) -> dict[str, tuple[Decimal, Decimal, str]]:
    """Provedor → (custo, delta, confiança) somando as zonas.

    Zona sem taxa para um provedor usa a razão que esse provedor teve na parte maior,
    para a soma não sair menor só porque falta a taxa regional. A confiança é a mais
    fraca entre a parte maior e as que pesam (20% ou mais do custo): uma fatia pequena
    em zona sem taxa oficial não rebaixa a categoria inteira."""
    _, _, principais, custo_principal = cotadas[0]
    total = sum((c for *_, c in cotadas), Decimal(0))
    razao = {
        e.provider: (e.estimated_monthly_cost / custo_principal) if custo_principal > 0 else None
        for e in principais
    }
    somas: dict[str, tuple[Decimal, Decimal, str]] = {}
    for indice, (_parte, _rates, equivs, custo) in enumerate(cotadas):
        da_parte = {e.provider: e for e in equivs}
        pesa = indice == 0 or (total > 0 and custo / total >= _PARTE_QUE_PESA)
        for prov, r in razao.items():
            if r is None:
                continue
            e = da_parte.get(prov)
            estimado = e.estimated_monthly_cost if e else custo * r
            confianca = e.pricing_confidence if e else "low"
            antes = somas.get(prov)
            if antes is None:
                somas[prov] = (estimado, estimado - custo, confianca)
                continue
            somas[prov] = (
                antes[0] + estimado,
                antes[1] + (estimado - custo),
                _mais_fraca(antes[2], confianca) if pesa else antes[2],
            )
    return somas


_PARTE_QUE_PESA = Decimal("0.2")


def _mais_fraca(a: str, b: str) -> str:
    """A mesma ordem de `category_calculator`: official > reference > low."""
    ordem = {"official": 3, "reference": 2, "low": 1}
    return a if ordem.get(a, 0) <= ordem.get(b, 0) else b


def _taxas_por_provedor(
    pontos: list[Any],
    categoria: str,
    *,
    source_provider: str,
    zona: str,
    regiao_origem: str | None,
) -> dict[str, CategoryRate]:
    """Uma taxa por provedor, da região escolhida para ele.

    A região vem primeiro (`regioes.escolher_regiao_de_preco`); dentro dela,
    `official` vence `reference`, e a captura mais recente desempata. Taxa
    oficial de outra região entra como `reference`.
    """
    por_provedor: dict[str, list[Any]] = {}
    for p in pontos:
        if p.currency == ANALYSIS_CURRENCY:
            por_provedor.setdefault(p.provider.lower(), []).append(p)
    taxas: dict[str, CategoryRate] = {}
    for prov, pts in sorted(por_provedor.items()):
        preferidas = [regiao_canonica(zona, prov)]
        if prov == source_provider.lower():
            preferidas.insert(0, regiao_origem)
        escolha = escolher_regiao_de_preco(
            prov, preferidas, {p.region for p in pts}, service_type=categoria
        )
        if escolha is None:
            continue
        melhor = max(
            (p for p in pts if p.region == escolha.regiao),
            key=lambda x: (
                _RANK_CONFIANCA.get(x.confidence, 0),
                str(x.captured_at or ""),
                x.sku_key,
            ),
        )
        confianca = melhor.confidence
        if escolha.aproximada and confianca == "official":
            confianca = "reference"
        taxas[prov] = CategoryRate(
            provider=prov,
            rate=melhor.price,
            unit=melhor.unit,
            confidence=confianca,
            region=escolha.regiao,
            approximate=escolha.aproximada,
        )
    return taxas


def _sem_repetir(itens: list[dict[str, str]]) -> list[dict[str, str]]:
    vistos = {tuple(sorted(i.items())): i for i in itens}
    return [vistos[k] for k in sorted(vistos)]


def _ledger_de_regioes(
    *,
    zona_por_pool: dict[str, str],
    pools_sem_zona: list[str],
    zona_categorias: str,
    regiao_origem_categorias: str | None,
    aproximadas: list[dict[str, str]],
) -> dict[str, Any]:
    pools_por_zona: dict[str, list[str]] = {}
    for pool, zona in sorted(zona_por_pool.items()):
        if pool not in pools_sem_zona:
            pools_por_zona.setdefault(zona, []).append(pool)
    ledger: dict[str, Any] = {
        "zona_padrao": ZONA_PADRAO,
        "pools_por_zona": pools_por_zona,
        "zona_categorias": zona_categorias,
        "regiao_origem_categorias": regiao_origem_categorias,
    }
    if pools_sem_zona:
        ledger["pools_sem_zona"] = sorted(pools_sem_zona)
    if aproximadas:
        ledger["cotacoes_aproximadas"] = aproximadas
    return ledger


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
        self._matcher = EquivalenceMatcher(sku_repo, price_repo) if sku_repo else None
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

        items = await listar_todos(
            self._item_repo.list_by_report, payload.report_id, payload.user_id
        )
        if not items:
            raise ValueError(f"report {payload.report_id} has no billing items")
        items = [_reviewed_billing_identity(item, source_provider) for item in items]

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
        meta_map = {_normalize(m.service_name): m for m in source_metadata}
        # Índice insensível a espaço/pontuação. Os parsers emitem nomes
        # concatenados (`AmazonRDS`) e o catálogo curado usa nomes com espaço
        # (`Amazon RDS`); sem esta ponte o casamento falha e o `service_type` cai
        # no palpite do classificador de SKU — foi assim que object storage virou
        # block storage. Lista (não dict) porque um serviço aparece uma vez por
        # capacidade (BigQuery = interactive_query + data_lake + data_warehouse).
        canonicals_squashed: dict[str, list[ServiceComparison]] = {}
        for c in sorted(source_comparisons, key=lambda x: (x.category, x.service_type)):
            key = service_key(source_provider, c.service_name)
            canonicals_squashed.setdefault(key, []).append(c)
        meta_squashed = {service_key(source_provider, m.service_name): m for m in source_metadata}

        # ── 3. Load target provider metadata ─────────────────────
        # `sorted`, não `list(set(...))`: a ordem de iteração de um set varia
        # entre processos (randomização de hash) e se propaga para a ordem dos
        # `equivalentes` de TODOS os pools do payload. Isso tornava a request
        # não-reproduzível e injetava ruído na entrada do modelo — dois runs do
        # mesmo CSV produziam a mesma verdade em ordem diferente.
        target_providers = sorted(
            {
                c.provider
                for c in await self._comparison_repo.list_all()
                if c.provider != source_provider
            }
        )
        target_metadata_all = await self._metadata_repo.list_by_providers(target_providers)
        target_meta_map: dict[tuple[str, str], Any] = {
            (m.provider.lower(), service_key(m.provider, m.service_name)): m.metadata
            for m in target_metadata_all
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
                        metadata=target_meta_map.get(
                            (eq.provider.lower(), service_key(eq.provider, eq.service_name))
                        ),
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

        def _match(svc_name: str, hint_type: str | None = None) -> ServiceComparison | None:
            name = _canonical_name(svc_name)
            # Ponte concatenado <-> espaçado. Quando o serviço tem várias
            # capacidades, prefere a que bate com o tipo que o classificador
            # sugeriu; senão a primeira em ordem determinística.
            candidates = canonicals_squashed.get(service_key(source_provider, name))
            if candidates:
                if hint_type:
                    for cand in candidates:
                        if cand.service_type == hint_type:
                            return cand
                return candidates[0]
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
            category_name = (
                comp.category
                if comp
                else (raw.get("category") or _meta_category(raw["service_name"]) or "unknown")
            )
            service_type = comp.service_type if comp else (raw.get("service_type") or "unknown")
            hint = service_type_hint(source_provider, raw["service_name"])
            if hint and comp is None:
                category_name, service_type = hint

            # Missing quantities occur in aggregate exports too. Only explicit
            # financial identities/categories suppress functional equivalents.
            sem_uso = is_financial_component(raw["service_name"], category_name)
            if sem_uso:
                category_name = financial_category(raw["service_name"], category_name)

            equivs: list[EquivalentService] = []
            if not sem_uso and category_name != "unknown" and service_type != "unknown":
                equivs = await _get_equivalents(category_name, service_type)

            # Expectativa de migração (classe 6R + esforço) a partir da metadata
            # curada do serviço de ORIGEM. Instrumento financeiro não migra, então
            # não recebe expectativa. Derivação determinística no backend.
            svc_meta = meta_map.get(_normalize(_canonical_name(raw["service_name"])))
            if svc_meta is None:
                svc_meta = meta_squashed.get(
                    service_key(
                        source_provider,
                        _canonical_name(raw["service_name"]),
                    )
                )
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
                type_source=(
                    "curado" if comp is not None else ("catalog_alias" if hint else "inferido")
                ),
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

                has_specs = matched.source_specs is not None and (
                    matched.source_specs.vcpus is not None
                    or matched.source_specs.ram_gb is not None
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
                    tm = (
                        TechnicalMatch(
                            vcpus=_t.vcpus,
                            ram_gb=_t.ram_gb,
                        )
                        if _t
                        else None
                    )
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
        agg_result = None
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

        # ── 9a. Regiões: onde cada pool roda e onde cada destino é cotado ──
        # Cada pool é cotado na zona dele (São Paulo cota São Paulo em todos os
        # provedores). Sem isto, todo bill era comparado com preço dos EUA.
        zona_por_pool: dict[str, str] = {}
        pools_sem_zona: list[str] = []
        for fpool in family_pools:
            zona = zona_da_regiao(source_provider, fpool.pool_key.region)
            if zona is None:
                pools_sem_zona.append(fpool.pool_key.as_str())
                zona = ZONA_PADRAO
            zona_por_pool[fpool.pool_key.as_str()] = zona
        cotacoes_aproximadas: list[dict[str, str]] = []

        # P1b: pool burstable cotado em destino sem crédito pela linha de base da CPU
        burstable_pela_base: list[dict[str, Any]] = []
        if self._matcher is not None and target_providers:
            curated_compute = await _get_equivalents("compute", "virtual_server")

            for fpool in family_pools:
                monthly_cost_analysis = convert(fpool.monthly_cost, fx) or Decimal("0")
                zona = zona_por_pool[fpool.pool_key.as_str()]
                perfil_do_pool = perfil(source_provider, fpool.pool_key.machine_family)
                if perfil_do_pool and perfil_do_pool.burstable and perfil_do_pool.base_cpu:
                    burstable_pela_base.append(
                        {
                            "pool": fpool.pool_key.as_str(),
                            "base_cpu": float(perfil_do_pool.base_cpu),
                            "vcpus_nominais": round(float(fpool.capacity.vcpus), 2),
                        }
                    )
                pedidas = {p: regiao_canonica(zona, p) for p in target_providers}
                l2_options = await self._matcher.match_pool(
                    vcpus=fpool.capacity.vcpus,
                    ram_gib=fpool.capacity.ram_gib,
                    monthly_cost=monthly_cost_analysis,
                    service_type="virtual_server",
                    target_providers=target_providers,
                    regioes={p: [r] for p, r in pedidas.items()},
                    perfil_origem=perfil_do_pool,
                )
                providers_with_l2: set[str] = set()
                equivs = []

                for opt in l2_options:
                    providers_with_l2.add(opt.provider.lower())
                    _fo = opt.financials
                    if _fo is not None and _fo.price_region_approximate:
                        cotacoes_aproximadas.append(
                            {
                                "provedor": opt.provider.lower(),
                                "pedida": str(pedidas.get(opt.provider) or "?"),
                                "cotada": str(_fo.price_region),
                                "uso": "compute",
                            }
                        )
                    # L3: enrich with curated metadata
                    meta = target_meta_map.get(
                        (opt.provider.lower(), service_key(opt.provider, opt.service_name))
                    )
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
                            price_region=opt.financials.price_region,
                            price_region_approximate=opt.financials.price_region_approximate,
                        )
                    tm = None
                    if opt.technical_match is not None:
                        tm = TechnicalMatch(
                            vcpus=opt.technical_match.vcpus,
                            ram_gb=opt.technical_match.ram_gb,
                        )
                    equivs.append(
                        EquivalentService(
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
                            architecture_mismatch=opt.architecture_mismatch,
                        )
                    )

                # L3-only curated fallback for providers without hardware match
                for ce in curated_compute:
                    if ce.provider.lower() in providers_with_l2:
                        continue
                    equivs.append(
                        EquivalentService(
                            provider=ce.provider,
                            service_name=ce.service_name,
                            category=ce.category,
                            service_type=ce.service_type,
                            match_basis="curated_only",
                            metadata=ce.metadata,
                            docs_costs=ce.docs_costs,
                        )
                    )

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
        # Os custos auxiliares não são separados por região: a taxa é a da zona
        # dominante do bill (a de mais custo entre as linhas com região). A
        # origem usa a própria região quando houver taxa dela (PLANO D7).
        dominante = regiao_dominante(
            (source_provider, regiao_da_linha(source_provider, i.region, i.usage_type), i.cost)
            for i in items
        )
        zona_categorias, regiao_origem = dominante or (ZONA_PADRAO, None)
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
                # P1: cada parte da categoria na taxa da zona dela, somadas. Sem região na
                # linha, a parte vai para a zona dominante (fração declarada em `zonas`).
                partes = dividir_por_zona(
                    source_provider,
                    auxiliary.por_regiao.get(category) or {"": aux_cost},
                    zona_dominante=zona_categorias,
                    regiao_dominante=regiao_origem,
                )
                # Só avisa o que um sync corrige: taxa oficial do provedor
                # existe, mas não na zona. Taxa que só existe como referência
                # (banco, disco da Azure) não tem fonte regional para buscar.
                com_oficial = {
                    p.provider.lower() for p in rate_points if p.confidence == "official"
                }
                cotadas: list[tuple[Any, dict[str, CategoryRate], list[Any], Decimal]] = []
                for parte in partes:
                    rates = _taxas_por_provedor(
                        rate_points,
                        category,
                        source_provider=source_provider,
                        zona=parte.zona,
                        regiao_origem=parte.regiao_origem,
                    )
                    for prov, taxa in rates.items():
                        if taxa.approximate and prov in com_oficial:
                            cotacoes_aproximadas.append(
                                {
                                    "provedor": prov,
                                    "pedida": str(
                                        (parte.regiao_origem if prov == source_provider else None)
                                        or regiao_canonica(parte.zona, prov)
                                        or "?"
                                    ),
                                    "cotada": str(taxa.region),
                                    "uso": category,
                                }
                            )
                    custo_da_parte = convert(parte.custo, fx) or Decimal("0")
                    equivs = estimate_by_cost_ratio(
                        source_provider=source_provider,
                        source_cost=custo_da_parte,
                        rates=rates,
                    )
                    cotadas.append((parte, rates, equivs, custo_da_parte))
                if not cotadas or not cotadas[0][2]:
                    continue
                # A parte maior dá as taxas que a resposta mostra; a soma dá o custo
                _, rates, principais, _ = cotadas[0]
                somas = _somar_partes(cotadas)
                zonas = (
                    [
                        {
                            "zona": parte.zona,
                            "fracao": round(float(parte.fracao), 4),
                            "regiao_origem": parte.regiao_origem,
                            **(
                                {"sem_regiao": round(float(parte.sem_regiao / parte.custo), 4)}
                                if parte.sem_regiao > 0
                                else {}
                            ),
                        }
                        for parte, *_ in cotadas
                    ]
                    if len(cotadas) > 1
                    else None
                )
                category_equivalents[category] = [
                    {
                        "provedor": e.provider,
                        "custo_mensal_estimado": round(float(somas[e.provider][0]), 2),
                        "delta_custo": round(float(somas[e.provider][1]), 2),
                        "confianca_preco": somas[e.provider][2],
                        "metodo": "category_rate_ratio",
                        "taxa_origem": str(rates[source_provider].rate),
                        "taxa_destino": str(rates[e.provider].rate),
                        "unidade": rates[e.provider].unit,
                        "categoria": category,
                        "taxa_origem_regiao": rates[source_provider].region,
                        "taxa_destino_regiao": rates[e.provider].region,
                        **(
                            {"taxa_origem_aproximada": True}
                            if rates[source_provider].approximate
                            else {}
                        ),
                        **(
                            {"taxa_destino_aproximada": True}
                            if rates[e.provider].approximate
                            else {}
                        ),
                        **({"zonas": zonas} if zonas else {}),
                    }
                    for e in principais
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
                    confianca_por_provedor[e.provider.lower()] = e.financials.pricing_confidence
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
        family_vcpus = {fp.pool_key.as_str(): fp.capacity.vcpus for fp in family_pools}
        vcpus_cotados: dict[str, Decimal] = {}
        for fkey, equivs in family_equivalents.items():
            src = family_src.get(fkey, Decimal("0"))
            chosen: dict[str, EquivalentService] = {}
            for e in sorted(
                equivs,
                key=lambda e: (
                    e.financials.estimated_monthly_cost
                    if e.financials and e.financials.estimated_monthly_cost is not None
                    else Decimal("Infinity"),
                    e.sku_id or "",
                    e.service_name,
                ),
            ):
                fin = e.financials
                if fin is not None and fin.estimated_monthly_cost is not None:
                    if e.provider.lower() in chosen:
                        continue
                    chosen[e.provider.lower()] = e
                    _add(
                        e.provider.lower(),
                        fin.estimated_monthly_cost,
                        src,
                        fin.pricing_confidence,
                    )
                    vcpus_cotados[e.provider.lower()] = vcpus_cotados.get(
                        e.provider.lower(), Decimal("0")
                    ) + family_vcpus.get(fkey, Decimal("0"))
        # O compromisso que paga máquina virtual (Savings Plan de compute, reserva
        # de EC2, CUD) sai da origem junto com os pools: o destino foi cotado
        # on-demand pelas horas medidas, inclusive as que o compromisso pagava.
        # Sem isto ele ficava no saldo mantido ao custo atual e o destino pagava
        # as horas e a taxa do plano. Proporcional à capacidade que o provedor
        # cotou: quem não cotou um pool não ganha o crédito dele.
        compromisso_compute = (
            convert(auxiliary.commitment_compute, fx) or Decimal("0")
            if auxiliary is not None
            else Decimal("0")
        )
        vcpus_dos_pools = sum(family_vcpus.values(), Decimal("0"))
        compromisso_por_provedor: dict[str, Decimal] = {}
        if compromisso_compute > 0 and vcpus_dos_pools > 0:
            for provider, vcpus in vcpus_cotados.items():
                parte = compromisso_compute * min(Decimal("1"), vcpus / vcpus_dos_pools)
                compromisso_por_provedor[provider] = parte
                _tally[provider]["covered"] += parte
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
        alertas += avaliar_regioes(
            sem_zona=pools_sem_zona,
            zona_padrao=ZONA_PADRAO,
            aproximadas=_sem_repetir(cotacoes_aproximadas),
        )
        confianca = avaliar_confianca(_custo_por_confianca).com_rebaixamento(
            [a.titulo for a in alertas if a.rebaixa]
        )

        # Pools com capacidade medida e custo zero: o bill cobra aquelas
        # instâncias na linha do compromisso (Savings Plan, RI, CUD), não na
        # delas. A economia calculada para esses pools sai superestimada, porque
        # o comparativo assume origem de custo zero. Declarar é o que temos —
        # ratear o compromisso entre pools seria estimativa vestida de fato.
        cobertura_compromisso = avaliar_cobertura(
            {fp.pool_key.as_str(): (fp.capacity.vcpus, fp.monthly_cost) for fp in family_pools},
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
            **(
                {
                    "burstable_pela_base": {
                        "pools": burstable_pela_base,
                        "explicacao": (
                            "Máquina burstable (CPU com crédito) entrega sem crédito só a linha "
                            "de base da vCPU. Em destino sem burstable, estes pools foram "
                            "dimensionados pela base, não pela vCPU nominal; em destino com "
                            "burstable, pela nominal. Vence o mais barato. A memória não muda."
                        ),
                    }
                }
                if burstable_pela_base
                else {}
            ),
            # Como o compute foi cotado: pela capacidade-hora medida, não por
            # máquina inteira ligada o mês todo (PLANO_EMPACOTAMENTO).
            "cotacao_compute": {
                "base": "capacidade_hora",
                "explicacao": (
                    "Cada pool é cotado pelas horas de vCPU e de memória que rodou "
                    "na origem: preço da instância de destino x 730 h x a fração "
                    "dela que o pool ocupa. O número de instâncias é o "
                    "dimensionamento para provisionar, arredondado para cima. "
                    "Uma máquina pequena ligada o mês inteiro pode custar até "
                    "uma instância inteira no destino."
                ),
            },
            # Só aparece quando existe: o compromisso que paga máquina virtual
            # saiu da origem junto com os pools (domain/services/compromisso_de_compute).
            **(
                {
                    "compromisso_de_compute": {
                        "valor": round(float(compromisso_compute), 2),
                        "por_provedor": {
                            k: round(float(v), 2)
                            for k, v in sorted(compromisso_por_provedor.items())
                        },
                        "explicacao": (
                            "Savings Plan, reserva ou CUD que paga horas de "
                            "máquina virtual. O destino foi cotado on-demand "
                            "pelas horas medidas, inclusive as que o compromisso "
                            "pagava; por isso o compromisso sai da origem junto "
                            "com os pools, na proporção da capacidade que cada "
                            "provedor cotou, em vez de ficar no saldo mantido ao "
                            "custo atual. Comprar compromisso no destino reduz o "
                            "custo cotado. Pool a pool, o custo de origem segue "
                            "líquido do compromisso e não é comparável sozinho."
                        ),
                    }
                }
                if compromisso_compute > 0
                else {}
            ),
            # Onde cada coisa foi cotada (PLANO_PRECO_REGIONAL). Sem isto um
            # número de São Paulo e um da Virgínia são indistinguíveis.
            "regioes": _ledger_de_regioes(
                zona_por_pool=zona_por_pool,
                pools_sem_zona=pools_sem_zona,
                zona_categorias=zona_categorias,
                regiao_origem_categorias=regiao_origem,
                aproximadas=_sem_repetir(cotacoes_aproximadas),
            ),
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
            (
                sv.total_cost
                for c in categories.values()
                for sv in c["services"]
                if getattr(sv, "type_source", "curado") != "curado"
            ),
            Decimal("0"),
        )
        fracao_inferida = float(custo_inferido / total_spend) if total_spend > 0 else 0.0
        premissas["tipo_servico_inferido"] = fracao_inferida

        # ── 10c. Export detalhado: o que foi medido e em que base ─────
        # Só existe com export detalhado; o bill agregado não ganha as chaves.
        if agg_result is not None and agg_result.inventario is not None:
            base = base_de_custo(agg_result.custo_por_base)
            if base:
                premissas["base_de_custo"] = base
            desconto = desconto_origem_sobre_lista(
                agg_result.custo_sob_demanda, agg_result.custo_efetivo_referenciado
            )
            if desconto:
                premissas["desconto_origem_sobre_lista"] = desconto
            premissas["dimensionamento_medido"] = fracao(
                agg_result.measured_capacity_cost, total_spend
            )

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
            inventario=agg_result.inventario if agg_result is not None else None,
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
