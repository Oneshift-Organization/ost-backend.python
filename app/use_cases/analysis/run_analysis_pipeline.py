from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.entities.analysis_job import AnalysisJob
from app.domain.repositories.i_analysis_job_repo import IAnalysisJobRepository
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.repositories.i_exchange_rate_repo import IExchangeRateRepository
from app.domain.services.currency import FxRate, convert, identity_rate
from app.domain.services.sku_normalization import normalize_provider
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.schemas.analysis_v2 import AnalysisV2Response
from app.use_cases.analysis.backend_authority import apply_backend_authority
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)

# Shorthand used inside _add_financials to silently ignore bad Decimal conversions
_suppress = contextlib.suppress


@dataclass(frozen=True)
class RunAnalysisPipelineInput:
    user_id: UUID
    report_id: UUID


def _fx_from_premissas(ctx) -> FxRate:
    """Reconstrói a taxa aplicada a partir do ledger (ou identidade)."""
    prem = getattr(ctx, "premissas", None) or {}
    fx = prem.get("fx") or {}
    taxa = fx.get("taxa")
    if not taxa:
        return identity_rate(getattr(ctx, "currency", "USD"))
    return FxRate(
        par=fx.get("par", "?"),
        taxa=Decimal(str(taxa)),
        fonte=fx.get("fonte", "config"),
    )


def _serialize_family_pools(ctx, fx: FxRate) -> tuple[list[dict], list[str]]:
    """Serializa os pools já agregados por família (Precision Engine v3).

    A agregação e o dimensionamento acontecem em ``build_migration_context``;
    aqui apenas formatamos e convertemos os custos de origem para a moeda de
    análise. Os equivalentes já vêm em moeda de análise do matcher.
    """
    family_pools = getattr(ctx, "family_pools", ()) or ()
    family_equivalents: dict = getattr(ctx, "family_equivalents", {}) or {}

    out: list[dict] = []
    missing: list[str] = []

    for fp in family_pools:
        key = fp.pool_key.as_str()
        equivs = family_equivalents.get(key, [])
        if not equivs:
            missing.append(key)
        entry: dict[str, object] = {
            "familia": fp.pool_key.machine_family,
            "capacidade": {
                "vcpus": round(float(fp.capacity.vcpus), 2),
                "ram_gib": round(float(fp.capacity.ram_gib), 2),
            },
            "custo_computacao": _money(fp.costs.compute_base, fx),
            "equivalentes": [_serialize_equivalent(e) for e in equivs],
        }
        # Campos só quando informam algo (Onda 6/E5): região e licença eram
        # constantes ("global" / 0.0) em 20/20 pools, e a lista de projetos era
        # ["_unknown"] — puro ruído que disputa atenção do modelo.
        if fp.pool_key.region and fp.pool_key.region != "global":
            entry["regiao"] = fp.pool_key.region
        licenca = _money(fp.costs.os_licensing, fx)
        if licenca > 0:
            entry["custo_licenca_so"] = licenca
        projetos = [p for p in fp.source_projects if p and p != "_unknown"]
        if projetos:
            entry["qtd_projetos"] = len(projetos)
        out.append(entry)

    return out, missing


def _serialize_services(ctx, fx: FxRate) -> list[dict]:
    """Serviços nomeados + DE-PARA cross-cloud (Onda 6/E1).

    Sem isto a IA recebia só baldes agregados (`banco_dados: 27760`) e não sabia
    que o cliente usa Amazon RDS — impossível recomendar migração de serviço.
    Equivalentes vão como strings `provedor:serviço` (bem mais barato em tokens
    que objetos aninhados, e igualmente legível pelo modelo).
    """
    out: list[dict] = []
    for cat in getattr(ctx, "categories", []) or []:
        for sv in cat.services:
            custo = _money(sv.total_cost, fx)
            if custo <= 0:
                continue
            entry: dict[str, object] = {
                "nome": sv.service_name,
                "custo": custo,
                "categoria": cat.category,
                "tipo": sv.service_type,
            }
            if sv.equivalents:
                entry["equivalentes"] = [
                    f"{normalize_provider(e.provider).upper()}:{e.service_name}"
                    for e in sv.equivalents[:3]
                ]
            # Só quando NÃO é o caso normal: um tipo inferido (palpite do
            # classificador, não casado no catálogo curado) pode gerar DE-PARA
            # errado — quem lê precisa saber a diferença.
            if getattr(sv, "type_source", "curado") != "curado":
                entry["tipo_fonte"] = "inferido"
            # Expectativa de migração calculada no backend (classe 6R + esforço).
            expectativa = getattr(sv, "migration_expectation", None)
            if expectativa:
                entry["migracao"] = expectativa
            out.append(entry)
    out.sort(key=lambda d: -float(d["custo"]))
    return out


def _round2(value: Decimal) -> float:
    """Arredonda para 2 casas no payload.

    Sem isto o ``delta_custo`` vaza a precisão bruta da divisão de câmbio
    (28 casas), o que só gasta token e sugere uma exatidão que não existe.
    """
    return round(float(value), 2)


def _money(amount: Decimal | None, fx: FxRate) -> float:
    """Converte para a moeda de análise e arredonda para o payload."""
    converted = convert(amount, fx)
    return round(float(converted), 2) if converted is not None else 0.0


def _aggregate_pools_by_family(
    pools: tuple, pool_equivalents: dict
) -> tuple[list[dict], list[str]]:
    """Aggregate per-project pools into per-family pools for the LLM payload.

    Groups by (machine_family, region) and sums capacity/costs. Keeps
    source_projects so the family entry is traceable back to original projects.
    Equivalents are aggregated by (provider, service_name) summing cost_delta
    and estimated_monthly_cost — the math is preserved exactly.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for p in pools:
        if p.capacity.vcpus <= 0 and p.capacity.ram_gib <= 0:
            continue
        key = (p.pool_key.machine_family, p.pool_key.region)
        if key not in buckets:
            buckets[key] = {
                "family": p.pool_key.machine_family,
                "region": p.pool_key.region,
                "vcpus": Decimal("0"),
                "ram_gib": Decimal("0"),
                "compute_base": Decimal("0"),
                "os_licensing": Decimal("0"),
                "source_projects": [],
                "pool_keys": [],
            }
        b = buckets[key]
        b["vcpus"] += p.capacity.vcpus
        b["ram_gib"] += p.capacity.ram_gib
        b["compute_base"] += p.costs.compute_base
        b["os_licensing"] += p.costs.os_licensing
        b["source_projects"].append(p.pool_key.project_id)
        b["pool_keys"].append(
            f"{p.pool_key.project_id}/{p.pool_key.machine_family}/{p.pool_key.region}"
        )

    compute_pools: list[dict] = []
    missing_equiv_family_ids: list[str] = []

    for (family, region), b in buckets.items():
        # Aggregate equivalents from all constituent project pools
        agg_equivs: dict[tuple[str, str], dict] = {}
        for pool_key in b["pool_keys"]:
            for e in pool_equivalents.get(pool_key, []):
                agg_key = (e.provider, e.service_name)
                if agg_key not in agg_equivs:
                    agg_equivs[agg_key] = _serialize_equivalent(e)
                    # copy financeiro dict so we can mutate it for accumulation
                    fin = agg_equivs[agg_key].get("financeiro")
                    if fin:
                        agg_equivs[agg_key]["financeiro"] = dict(fin)
                else:
                    existing = agg_equivs[agg_key]
                    new_fin = getattr(e, "financials", None)
                    if new_fin and existing.get("financeiro"):
                        _add_financials(existing["financeiro"], new_fin)
                    inst = getattr(e, "instances", 1)
                    if inst > existing.get("instancias", 1):
                        existing["instancias"] = inst

        family_id = f"{family}/{region}"
        if not agg_equivs:
            missing_equiv_family_ids.append(family_id)

        compute_pools.append({
            "familia": family,
            "regiao": region,
            "projetos_origem": b["source_projects"],
            "capacidade": {
                "vcpus": round(float(b["vcpus"]), 2),
                "ram_gib": round(float(b["ram_gib"]), 2),
            },
            "custos": {
                "custo_computacao": round(float(b["compute_base"]), 2),
                "custo_licenca_so": round(float(b["os_licensing"]), 2),
            },
            "equivalentes": list(agg_equivs.values()),
        })

    return compute_pools, missing_equiv_family_ids


def _add_financials(existing: dict, new_fin) -> None:
    """Accumulate financial totals in place (Decimal-safe). Uses PT key names."""
    # Maps PT key in existing dict → attribute name on new_fin entity
    field_map = {
        "custo_mensal_estimado": "estimated_monthly_cost",
        "delta_custo": "cost_delta",
    }
    for pt_key, attr in field_map.items():
        existing_val = existing.get(pt_key)
        new_val = getattr(new_fin, attr, None)
        field_name = pt_key  # use PT key for assignment
        if existing_val is not None and new_val is not None:
            with _suppress():
                existing[field_name] = str(Decimal(str(existing_val)) + Decimal(str(new_val)))


def _serialize_pools(ctx) -> tuple[str, list[str]]:
    """Serialize family-level pools + equivalents into the LLM payload.

    Todos os valores monetários saem na MOEDA DE ANÁLISE (ctx.analysis_currency),
    já convertidos. A taxa aplicada está em ``premissas.fx``.
    """
    auxiliary = getattr(ctx, "auxiliary", None)
    database_equivalents: list = getattr(ctx, "database_equivalents", [])
    fx = _fx_from_premissas(ctx)

    compute_pools, missing_equiv_pool_ids = _serialize_family_pools(ctx, fx)

    payload: dict[str, object] = {"pools_computacao": compute_pools}

    if auxiliary is not None and auxiliary.any_positive:
        aux_payload: dict[str, object] = {}
        if auxiliary.gke_control_plane > 0:
            aux_payload["gke_plano_controle"] = _money(auxiliary.gke_control_plane, fx)
        if auxiliary.storage_ssd > 0:
            aux_payload["armazenamento_ssd"] = _money(auxiliary.storage_ssd, fx)
        if auxiliary.storage_hdd > 0:
            aux_payload["armazenamento_hdd"] = _money(auxiliary.storage_hdd, fx)
        if auxiliary.network_egress > 0:
            aux_payload["egresso_rede"] = _money(auxiliary.network_egress, fx)
        if auxiliary.database > 0:
            aux_payload["banco_dados"] = _money(auxiliary.database, fx)
            if database_equivalents:
                aux_payload["equivalentes_banco_dados"] = [
                    _serialize_equivalent(e) for e in database_equivalents
                ]
        if auxiliary.commitment > 0:
            # Compromisso/reserva (Savings Plan, RI, CUD): custo sem capacidade,
            # segregado para não distorcer o preço/vCPU dos pools.
            aux_payload["compromisso_reserva"] = _money(auxiliary.commitment, fx)
        if auxiliary.orphan > 0:
            aux_payload["nao_classificado"] = {
                "custo": _money(auxiliary.orphan, fx),
                "quantidade_skus": len(auxiliary.orphan_skus),
            }
        # Equivalentes cross-cloud por categoria (storage/egress), já em moeda de
        # análise — reprecificados por razão de taxa em build_migration_context.
        category_equivalents = getattr(ctx, "category_equivalents", {}) or {}
        _cat_key = {
            "storage_ssd": "armazenamento_ssd",
            "storage_hdd": "armazenamento_hdd",
            "network_egress": "egresso_rede",
            "database": "banco_dados",
        }
        for category, equivs in category_equivalents.items():
            aux_payload[f"equivalentes_{_cat_key.get(category, category)}"] = equivs
        payload["custos_auxiliares"] = aux_payload

    mapping_confidence = getattr(ctx, "mapping_confidence", None)
    if mapping_confidence:
        payload["confianca_classificacao"] = {
            level: _money(cost, fx) for level, cost in mapping_confidence.items()
        }

    return json.dumps(payload, default=str), missing_equiv_pool_ids


def build_pipeline_llm_input_payload(ctx) -> tuple[dict, list[str]]:
    pools_json, missing_equiv_ids = _serialize_pools(ctx)
    context_payload = json.loads(pools_json)
    fx = _fx_from_premissas(ctx)
    context_payload["provedor_origem"] = ctx.source_provider
    context_payload["periodo_faturamento"] = ctx.billing_period
    # Gasto total também na moeda de análise — payload inteiro em uma só moeda.
    context_payload["gasto_total"] = _money(ctx.total_spend, fx)
    context_payload["moeda"] = getattr(ctx, "analysis_currency", ctx.currency)
    context_payload["premissas"] = getattr(ctx, "premissas", {}) or {}
    # Serviços nomeados com DE-PARA — o que permite recomendar migração de
    # serviço (RDS -> Cloud SQL), não só de capacidade agregada.
    servicos = _serialize_services(ctx, fx)
    if servicos:
        context_payload["servicos"] = servicos
    # Roll-up por provedor + recomendação, já calculado no backend. A IA narra.
    resumo = getattr(ctx, "resumo_executivo", {}) or {}
    if resumo:
        context_payload["resumo_executivo"] = resumo
    return context_payload, missing_equiv_ids


def _serialize_equivalent(equiv) -> dict:
    """Serialize a single EquivalentService to a JSON-safe dict.

    ``categoria``/``tipo_servico`` são implicados pela seção que contém o
    equivalente (``pools_computacao``, ``equivalentes_banco_dados``) e eram
    idênticos em 100% das 60 ocorrências — repetí-los custava ~840 tokens sem
    informar nada (Onda 6/E5). ``base_correspondencia`` só aparece quando NÃO é
    o padrão, aí sim informativo (``curated_only`` = sem dimensionamento real).
    """
    d: dict[str, object] = {
        # Casing único: o mesmo payload trazia "GCP", "azure" e "oci".
        "provedor": normalize_provider(equiv.provider).upper(),
        "nome_servico": equiv.service_name,
    }
    basis = getattr(equiv, "match_basis", "pool_capacity")
    if basis != "pool_capacity":
        d["base_correspondencia"] = basis
    instances = getattr(equiv, "instances", 1)
    if instances > 1:
        d["instancias"] = instances

    tm = getattr(equiv, "technical_match", None)
    if tm is not None:
        d["correspondencia_tecnica"] = {
            k: _round2(v) for k, v in [("vcpus", tm.vcpus), ("ram_gb", tm.ram_gb)] if v is not None
        }

    fin = getattr(equiv, "financials", None)
    if fin is not None:
        fin_dict: dict[str, object] = {}
        if fin.estimated_monthly_cost is not None:
            fin_dict["custo_mensal_estimado"] = _round2(fin.estimated_monthly_cost)
        if fin.cost_delta is not None:
            fin_dict["delta_custo"] = _round2(fin.cost_delta)
        fin_dict["confianca_preco"] = fin.pricing_confidence
        d["financeiro"] = fin_dict

    meta = getattr(equiv, "metadata", None)
    if meta:
        d["metadados"] = meta

    docs = getattr(equiv, "docs_costs", None)
    if docs:
        d["docs_custos"] = docs

    return d


def serialize_as_legacy(ctx) -> dict:
    """Serialize MigrationContext into the legacy workload JSON format.

    Converts capacity pools + pool_equivalents into the ``workloads[]`` schema
    that was the original LLM input format, so the legacy pipeline sends a
    familiar payload to the model. See docs/refactor/analysis-v2-spec.md §5.
    """
    workloads: list[dict] = []
    pool_equivalents: dict = getattr(ctx, "pool_equivalents", {})
    pools = getattr(ctx, "pools", ())
    auxiliary = getattr(ctx, "auxiliary", None)
    db_equivalents: list = getattr(ctx, "database_equivalents", [])

    for pool in pools:
        if pool.capacity.vcpus <= 0 and pool.capacity.ram_gib <= 0:
            continue
        pool_key = (
            f"{pool.pool_key.project_id}/"
            f"{pool.pool_key.machine_family}/"
            f"{pool.pool_key.region}"
        )
        equivs = pool_equivalents.get(pool_key, [])
        workloads.append({
            "canonical_service": "virtual_server",
            "category": "compute",
            "service_name": (
                f"{pool.pool_key.machine_family} Compute ({pool.pool_key.project_id})"
            ),
            "service_type": "virtual_server",
            "source_usage": {
                "sku_id": pool_key,
                "billed_cost": round(float(pool.monthly_cost), 6),
                "usage_quantity": round(float(pool.capacity.vcpus), 2),
                "currency": ctx.currency,
            },
            "equivalents": [_equiv_to_legacy(e) for e in equivs],
        })

    if auxiliary and auxiliary.database > 0 and db_equivalents:
        workloads.append({
            "canonical_service": "relational_database",
            "category": "database",
            "service_name": "Database Services",
            "service_type": "relational_database",
            "source_usage": {
                "sku_id": "database",
                "billed_cost": round(float(auxiliary.database), 6),
                "currency": ctx.currency,
            },
            "equivalents": [_equiv_to_legacy(e) for e in db_equivalents],
        })

    return {
        "source_provider": ctx.source_provider,
        "billing_period": ctx.billing_period,
        "currency": ctx.currency,
        "total_spend": float(ctx.total_spend),
        "company_context": {
            "company_size": "unknown",
            "industry": "",
            "regulated_environment": False,
            "critical_workloads": False,
            "global_operations": False,
            "high_availability_required": False,
            "disaster_recovery_required": False,
            "security_sensitive": False,
            "cloud_maturity": "unknown",
            "migration_goal": "unknown",
        },
        "workloads": workloads,
    }


def _equiv_to_legacy(e) -> dict:
    tm = getattr(e, "technical_match", None)
    fin = getattr(e, "financials", None)
    return {
        "provider": e.provider,
        "service_name": e.service_name,
        "technical_match": {
            "vcpus": float(tm.vcpus) if tm and tm.vcpus is not None else None,
            "ram_gb": float(tm.ram_gb) if tm and tm.ram_gb is not None else None,
        } if tm else None,
        "financials": {
            "estimated_monthly_cost": float(fin.estimated_monthly_cost)
            if fin and fin.estimated_monthly_cost is not None else None,
            "cost_delta": float(fin.cost_delta)
            if fin and fin.cost_delta is not None else None,
            "pricing_confidence": fin.pricing_confidence if fin else "low",
        } if fin else None,
        "metadata": getattr(e, "metadata", None),
    }


class RunAnalysisPipelineUseCase:
    """Orquestra a pipeline de análise em background.

    1. Marca o job como PROCESSING.
    2. Executa BuildMigrationContextUseCase para gerar o contexto de migração.
    3. Envia contexto + prompt para o LLMOrchestrator.
    4. Persiste o resultado (structured + text) como COMPLETED ou FAILED.
    """

    def __init__(
        self,
        analysis_job_repo: IAnalysisJobRepository,
        report_repo: IBillingReportRepository,
        item_repo: IBillingEnrichedRepository,
        metadata_repo: PGServiceMetadataRepository,
        comparison_repo: PGServiceComparisonRepository,
        sku_repo: ICloudSkuRepository,
        llm_orchestrator: LLMOrchestrator,
        fx_repo: IExchangeRateRepository | None = None,
        price_repo: ICloudPricePointRepository | None = None,
    ) -> None:
        self._fx_repo = fx_repo
        self._price_repo = price_repo
        self._job_repo = analysis_job_repo
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo
        self._sku_repo = sku_repo
        self._orchestrator = llm_orchestrator

    async def execute(self, job: AnalysisJob) -> None:
        job.mark_processing()
        await self._job_repo.update(job)

        try:
            context_uc = BuildMigrationContextUseCase(
                self._report_repo,
                self._item_repo,
                self._metadata_repo,
                self._comparison_repo,
                sku_repo=self._sku_repo,
                fx_repo=self._fx_repo,
                price_repo=self._price_repo,
            )

            ctx = await context_uc.execute(
                BuildMigrationContextInput(
                    user_id=job.user_id,
                    report_id=job.report_id,
                )
            )

            pools_json, missing_equiv_ids = _serialize_pools(ctx)
            # Capture payload for persistence before sending to LLM
            llm_input, _ = build_pipeline_llm_input_payload(ctx)

            ui_report = await self._orchestrator.generate_report(
                context=ctx,
                source_provider=ctx.source_provider,
                billing_period=ctx.billing_period,
                total_spend=float(ctx.total_spend),
                currency=ctx.currency,
                pools_json=pools_json,
                payload=llm_input,
            )

            if not isinstance(ui_report, AnalysisV2Response):
                ui_report = AnalysisV2Response.resilient_parse(
                    ui_report.model_dump() if hasattr(ui_report, "model_dump") else {}
                )

            # O backend é a autoridade sobre número: sobrescreve custo, economia
            # e provedor recomendado com o cálculo determinístico já auditado
            # (premissas.reconciliacao). O que o modelo escreveu de qualitativo
            # é preservado. Sem isto a resposta da API poderia contradizer a
            # própria reconciliação.
            ui_report = apply_backend_authority(
                ui_report, getattr(ctx, "resumo_executivo", None)
            )

            flags = ui_report.flags
            if ctx.missing_sku_ids:
                flags.setdefault("catalog", []).extend(
                    {"code": "MISSING_SKU_DEFINITION",
                     "message": f"SKU '{sku_id}' without vCPU/RAM definition in catalog",
                     "impact_area": "catalog"}
                    for sku_id in ctx.missing_sku_ids
                )
            if missing_equiv_ids:
                flags.setdefault("equivalents", []).extend(
                    {"code": "MISSING_EQUIVALENT",
                     "message": f"Pool '{pool_id}' has no cross-cloud equivalent in catalog",
                     "impact_area": "catalog"}
                    for pool_id in missing_equiv_ids
                )

            llm_output = (
                f"Viability score: {ui_report.viability_score}/1000. "
                f"Recommendation: {ui_report.recommendation}. "
                f"Recommended provider: {ui_report.recommended_provider}. "
                f"Rationale: {ui_report.recommendation_rationale}. "
                f"Monthly savings: ${ui_report.savings_summary.total_monthly_savings_usd:.2f}. "
                f"Reduction: {ui_report.savings_summary.percentage_reduction}%."
            )

            job.mark_completed(
                output=llm_output,
                structured=ui_report.model_dump(),
                llm_input=llm_input,
            )
        except Exception as exc:
            job.mark_failed(str(exc))

        await self._job_repo.update(job)
