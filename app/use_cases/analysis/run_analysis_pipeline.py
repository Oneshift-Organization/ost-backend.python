from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.analysis import FinancialEstimate, MigrationContext
from app.domain.entities.analysis_job import AnalysisJob
from app.domain.repositories.i_analysis_job_repo import IAnalysisJobRepository
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.repositories.i_exchange_rate_repo import IExchangeRateRepository
from app.domain.services.currency import FxRate, convert, identity_rate
from app.domain.services.rede_por_uso import CHAVE_NO_PAYLOAD as CHAVE_DA_REDE
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
from app.use_cases.analysis.decision_support import impose_decision, prepare_decision_payload
from app.use_cases.analysis.topologia_no_payload import MontarTopologiaUseCase

# Shorthand used inside _add_financials to silently ignore bad Decimal conversions
_suppress = contextlib.suppress


@dataclass(frozen=True)
class RunAnalysisPipelineInput:
    user_id: UUID
    report_id: UUID


def _fx_from_premissas(ctx: MigrationContext) -> FxRate:
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


def conversor_da_analise(ctx: MigrationContext) -> Callable[[Decimal], float]:
    """Moeda de origem → moeda de análise, com a mesma taxa do payload inteiro.

    Quem acrescenta dinheiro ao payload (a topologia do inventário) usa isto:
    um número em outra moeda misturaria BRL com USD, o que o Precision Engine proíbe.
    """
    fx = _fx_from_premissas(ctx)
    return lambda valor: _money(valor, fx)


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

    # Ordem explícita e estável: maior gasto primeiro, desempate alfabético.
    # Garante request reproduzível independente da ordem que o banco devolveu, e
    # coloca o que importa no topo para quem lê (humano ou modelo).
    for fp in sorted(
        family_pools,
        key=lambda p: (-p.monthly_cost, p.pool_key.machine_family, p.pool_key.region),
    ):
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
        # Capacidade medida e custo zero: as instâncias são cobertas por
        # compromisso (Savings Plan, RI, CUD) e o custo é faturado na linha dele.
        # Sem esta marca, qualquer consumidor calcula economia contra zero e
        # conclui que migrar o pool ADICIONA o custo inteiro.
        if fp.capacity.vcpus > 0 and fp.monthly_cost <= 0:
            entry["custo_coberto_por_compromisso"] = True
        entry.update(_inventario_do_pool(fp, ctx))
        out.append(entry)

    return out, missing


# Tipos de origem por pool no payload: o topo basta para narrar e cabe no
# orçamento de tokens; a contagem total vai em `instancias_medidas`.
_MAX_TIPOS = 5
_MAX_GRUPOS_BANCO = 15
_NOMES_ARMAZENAMENTO = {
    "storage_ssd": "armazenamento_ssd",
    "storage_hdd": "armazenamento_hdd",
    "object_storage": "armazenamento_objetos",
    "snapshots": "snapshots",
}


def _inventario_do_pool(fp, ctx) -> dict[str, object]:
    """Fatos medidos do pool (export detalhado). Vazio no caminho agregado."""
    inv = getattr(fp, "inventario", None)
    if inv is None:
        return {}
    saida: dict[str, object] = {}
    if inv.instancias:
        saida["instancias_medidas"] = inv.instancias
        saida["tipos_origem"] = dict(list(inv.tipos().items())[:_MAX_TIPOS])
    inventario = getattr(ctx, "inventario", None)
    horas = inventario.horas_periodo if inventario is not None else Decimal("730")
    ativo = inv.fracao_tempo_ativo(horas)
    if ativo is not None:
        # Tempo LIGADA, não utilização de CPU: billing não mede carga.
        saida["fracao_tempo_ativo"] = round(float(ativo), 2)
    fonte = inv.fonte_capacidade()
    if fonte:
        saida["fonte_capacidade"] = fonte
    return saida


def _serialize_inventario(inv, fx: FxRate) -> dict[str, object]:
    """Armazenamento e bancos medidos (export detalhado)."""
    saida: dict[str, object] = {"horas_periodo": float(inv.horas_periodo)}
    armazenamento: dict[str, dict[str, object]] = {}
    for categoria, medido in sorted(inv.armazenamento.items()):
        entrada: dict[str, object] = {"gb": round(float(medido.gb)), "recursos": medido.quantidade}
        if categoria == "snapshots" and medido.custo_por_categoria:
            # De qual balde de disco o custo do snapshot saiu (ver derivador).
            entrada["custo"] = {
                _NOMES_ARMAZENAMENTO.get(c, c): _money(v, fx)
                for c, v in sorted(medido.custo_por_categoria.items())
            }
        armazenamento[_NOMES_ARMAZENAMENTO.get(categoria, categoria)] = entrada
    if armazenamento:
        saida["armazenamento"] = armazenamento
    if inv.bancos:
        grupos: dict[tuple, dict[str, Any]] = {}
        for b in inv.bancos:
            chave = (b.classe, b.motor, b.multi_az, b.regiao)
            g = grupos.setdefault(
                chave,
                {
                    "classe": b.classe,
                    "motor": b.motor,
                    "multi_az": b.multi_az,
                    "regiao": b.regiao,
                    "quantidade": 0,
                    "custo": Decimal("0"),
                },
            )
            g["quantidade"] += 1
            g["custo"] += b.custo
        ordenados = sorted(grupos.values(), key=lambda g: (-g["custo"], str(g["classe"])))
        saida["bancos"] = [
            {**g, "custo": _money(g["custo"], fx)} for g in ordenados[:_MAX_GRUPOS_BANCO]
        ]
        saida["total_bancos"] = len(inv.bancos)
    return saida


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
                    for e in sv.equivalents
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


def _naive(dt: datetime) -> datetime:
    """Normaliza para naive-UTC.

    `captured_at` pode vir aware ou naive dependendo do driver e da coluna. Sem
    normalizar, a subtração levanta TypeError — e derrubaria a montagem do
    payload inteiro por causa de um campo cosmético.
    """
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _round2(value: Decimal) -> float:
    """Arredonda para 2 casas no payload.

    Sem isto o ``delta_custo`` vaza a precisão bruta da divisão de câmbio
    (28 casas), o que só gasta token e sugere uma exatidão que não existe.
    """
    return round(float(value), 2)


# Categoria auxiliar → chave dela em `custos_auxiliares`
_CHAVE_DA_CATEGORIA = {
    "storage_ssd": "armazenamento_ssd",
    "storage_hdd": "armazenamento_hdd",
    "object_storage": "armazenamento_objetos",
    "network_egress": "egresso_rede",
    "database": "banco_dados",
    **CHAVE_DA_REDE,
}


def _por_servico(auxiliary: Any, fx: Any) -> dict[str, object]:
    """Quanto de cada categoria é de cada serviço da fatura. Com isso o preço da
    categoria chega ao serviço pela parte dele (equivalence_pricing)."""
    por_servico = {
        _CHAVE_DA_CATEGORIA[categoria]: {
            servico: _money(custo, fx) for servico, custo in sorted(servicos.items()) if custo > 0
        }
        for categoria, servicos in sorted(auxiliary.por_servico.items())
        if categoria in _CHAVE_DA_CATEGORIA
    }
    return {"por_servico": por_servico} if por_servico else {}


def _rede_por_tipo(auxiliary: Any, fx: Any) -> dict[str, object]:
    """O balde de rede além do egress, por tipo (PLANO_PRECO_NAO_COMPUTE)."""
    return {
        chave: _money(getattr(auxiliary, categoria), fx)
        for categoria, chave in CHAVE_DA_REDE.items()
        if getattr(auxiliary, categoria) > 0
    }


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

        compute_pools.append(
            {
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
            }
        )

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
        if auxiliary.object_storage > 0:
            aux_payload["armazenamento_objetos"] = _money(auxiliary.object_storage, fx)
        if auxiliary.network_egress > 0:
            aux_payload["egresso_rede"] = _money(auxiliary.network_egress, fx)
        aux_payload.update(_rede_por_tipo(auxiliary, fx))
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
        for category, equivs in category_equivalents.items():
            aux_payload[f"equivalentes_{_CHAVE_DA_CATEGORIA.get(category, category)}"] = equivs
        aux_payload |= _por_servico(auxiliary, fx)
        payload["custos_auxiliares"] = aux_payload

    mapping_confidence = getattr(ctx, "mapping_confidence", None)
    if mapping_confidence:
        payload["confianca_classificacao"] = {
            level: _money(cost, fx) for level, cost in mapping_confidence.items()
        }

    return json.dumps(payload, default=str), missing_equiv_pool_ids


def build_pipeline_llm_input_payload(
    ctx, decision_context: dict | None = None
) -> tuple[dict, list[str]]:
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
    # Fatos medidos de export detalhado. O bill agregado não ganha a chave.
    inventario = getattr(ctx, "inventario", None)
    if inventario is not None:
        context_payload["inventario_medido"] = _serialize_inventario(inventario, fx)
    context_payload["decision_workloads"] = _decision_workloads(ctx, fx)
    if decision_context:
        context_payload["decision_context"] = decision_context
    return prepare_decision_payload(context_payload), missing_equiv_ids


def _decision_workloads(ctx, fx: FxRate) -> list[dict]:
    """Persist catalog evidence before the model can reinterpret any of it."""
    return [
        {
            "name": service.service_name,
            "category": category.category,
            "service_type": service.service_type,
            "cost": _money(service.total_cost, fx),
            "migration": getattr(service, "migration_expectation", None) or {},
            "source_metadata": getattr(service, "migration_metadata", None) or {},
            "type_source": getattr(service, "type_source", "inferido"),
            "evidence_source": "catalog_snapshot",
            "targets": [
                {
                    "provider": normalize_provider(eq.provider),
                    "service_name": eq.service_name,
                    "service_type": eq.service_type,
                    "metadata": eq.metadata or {},
                }
                for eq in service.equivalents
            ],
        }
        for category in getattr(ctx, "categories", []) or []
        for service in category.services
    ]


def _serialize_financeiro(fin: FinancialEstimate) -> dict[str, object]:
    """O bloco `financeiro` de um equivalente: custo, delta e proveniência."""
    fin_dict: dict[str, object] = {}
    if fin.estimated_monthly_cost is not None:
        fin_dict["custo_mensal_estimado"] = _round2(fin.estimated_monthly_cost)
    if fin.cost_delta is not None:
        fin_dict["delta_custo"] = _round2(fin.cost_delta)
    fin_dict["confianca_preco"] = fin.pricing_confidence
    # Proveniência: sem ela, "official" pode ser de ontem ou de três meses e
    # ninguém sabe. A IDADE é o que torna isso acionável — é o número que
    # deixa um consumidor (tela ou agente) decidir se confia ou contesta.
    origem = getattr(fin, "price_source", None)
    if origem:
        fin_dict["preco_origem"] = origem
    capturado = getattr(fin, "price_captured_at", None)
    if capturado is not None:
        fin_dict["preco_capturado_em"] = capturado.date().isoformat()
        fin_dict["preco_idade_dias"] = max(
            0, (datetime.now(UTC).replace(tzinfo=None) - _naive(capturado)).days
        )
    # Onde o preço vale. `aproximada` só aparece quando é verdade: a região
    # pedida não tinha preço e a cotação usou outra (PLANO_PRECO_REGIONAL D6).
    regiao = getattr(fin, "price_region", None)
    if regiao:
        fin_dict["preco_regiao"] = regiao
    if getattr(fin, "price_region_approximate", False):
        fin_dict["preco_regiao_aproximada"] = True
    return fin_dict


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
    # O shape em si. `nome_servico` é rótulo ("Compute Engine N2"); é o `sku`
    # que diz `n2-standard-8`, e é ele que vira `machine_type` num Terraform.
    # Ausente quando o equivalente veio do catálogo curado, que não tem shape —
    # o mesmo caso em que `base_correspondencia` sai como `curated_only`.
    if getattr(equiv, "sku_id", None):
        d["sku"] = equiv.sku_id
    basis = getattr(equiv, "match_basis", "pool_capacity")
    if basis != "pool_capacity":
        d["base_correspondencia"] = basis
    instances = getattr(equiv, "instances", 1)
    if instances > 1:
        d["instancias"] = instances
    # Só aparece quando é verdade: a cotação trocou x86 por ARM (ou o inverso)
    # porque o destino não tinha a mesma arquitetura cobrindo o pool.
    if getattr(equiv, "architecture_mismatch", False):
        d["arquitetura_diferente"] = True

    tm = getattr(equiv, "technical_match", None)
    if tm is not None:
        d["correspondencia_tecnica"] = {
            k: _round2(v) for k, v in [("vcpus", tm.vcpus), ("ram_gb", tm.ram_gb)] if v is not None
        }

    fin = getattr(equiv, "financials", None)
    if fin is not None:
        d["financeiro"] = _serialize_financeiro(fin)

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
    familiar payload to the model. See docs/precision-engine.md.
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
            f"{pool.pool_key.project_id}/{pool.pool_key.machine_family}/{pool.pool_key.region}"
        )
        equivs = pool_equivalents.get(pool_key, [])
        workloads.append(
            {
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
            }
        )

    if auxiliary and auxiliary.database > 0 and db_equivalents:
        workloads.append(
            {
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
            }
        )

    result = {
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
    # The direct/legacy narrative keeps its format but shares exactly the same
    # deterministic snapshot and policy as the primary pipeline.
    result.update(
        {
            key: value
            for key, value in build_pipeline_llm_input_payload(ctx)[0].items()
            if key in {"decision_workloads", "decision_support", "resumo_executivo", "premissas"}
        }
    )
    return result


def _equiv_to_legacy(e) -> dict:
    tm = getattr(e, "technical_match", None)
    fin = getattr(e, "financials", None)
    return {
        "provider": e.provider,
        "service_name": e.service_name,
        "technical_match": {
            "vcpus": float(tm.vcpus) if tm and tm.vcpus is not None else None,
            "ram_gb": float(tm.ram_gb) if tm and tm.ram_gb is not None else None,
        }
        if tm
        else None,
        "financials": {
            "estimated_monthly_cost": float(fin.estimated_monthly_cost)
            if fin and fin.estimated_monthly_cost is not None
            else None,
            "cost_delta": float(fin.cost_delta) if fin and fin.cost_delta is not None else None,
            "pricing_confidence": fin.pricing_confidence if fin else "low",
        }
        if fin
        else None,
        "metadata": getattr(e, "metadata", None),
    }


class RunAnalysisPipelineUseCase:
    """Orquestra a pipeline de análise em background.

    1. Marca o job como PROCESSING.
    2. Executa BuildMigrationContextUseCase para gerar o contexto de migração.
    3. Envia contexto + prompt para o LLMOrchestrator.
    4. Persiste o resultado (structured + text) como COMPLETED ou FAILED.
    """

    def __init__(  # noqa: PLR0917 -- preserve the existing dependency-injection contract
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
        *,
        topologia: MontarTopologiaUseCase | None = None,
    ) -> None:
        self._topologia = topologia
        self._fx_repo = fx_repo
        self._price_repo = price_repo
        self._job_repo = analysis_job_repo
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo
        self._sku_repo = sku_repo
        self._orchestrator = llm_orchestrator

    async def execute(
        self,
        job: AnalysisJob,
        *,
        decision_context: dict | None = None,
        inventario_id: UUID | None = None,
    ) -> None:
        job.mark_processing()
        # Transação própria e curta ANTES de qualquer I/O externo: o estado
        # intermediário precisa existir no banco enquanto o LLM roda, senão quem
        # faz polling não distingue "na fila" de "executando" — e um processo
        # morto no meio deixa o job preso sem sinal nenhum.
        marcar = getattr(self._job_repo, "marcar_processando", None)
        if marcar is not None:
            await marcar(job.job_id)
        else:  # repositórios de teste sem a otimização
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
            llm_input, _ = build_pipeline_llm_input_payload(ctx, decision_context)
            if inventario_id is not None:
                # A topologia entra congelada no payload (PLANO_INVENTARIO_API
                # RI14): mudar a declaração depois não muda a análise feita.
                if self._topologia is None:
                    raise RuntimeError("inventário pedido sem o caso de uso de topologia")
                llm_input["topologia"] = await self._topologia.execute(
                    user_id=job.user_id,
                    inventario_id=inventario_id,
                    report_id=job.report_id,
                    ctx=ctx,
                    converter=conversor_da_analise(ctx),
                )

            llm_input = prepare_decision_payload(llm_input)
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
            ui_report = apply_backend_authority(ui_report, llm_input.get("resumo_executivo"))
            ui_report = impose_decision(ui_report, llm_input)

            flags = ui_report.flags
            if ctx.missing_sku_ids:
                flags.setdefault("catalog", []).extend(
                    {
                        "code": "MISSING_SKU_DEFINITION",
                        "message": f"SKU '{sku_id}' without vCPU/RAM definition in catalog",
                        "impact_area": "catalog",
                    }
                    for sku_id in ctx.missing_sku_ids
                )
            if missing_equiv_ids:
                flags.setdefault("equivalents", []).extend(
                    {
                        "code": "MISSING_EQUIVALENT",
                        "message": f"Pool '{pool_id}' has no cross-cloud equivalent in catalog",
                        "impact_area": "catalog",
                    }
                    for pool_id in missing_equiv_ids
                )

            decision_score = ui_report.decision_support.score
            score_description = (
                f"{decision_score.value}/100"
                if decision_score and decision_score.value is not None
                else "parcial ou indisponível; consulte decision_support"
            )
            llm_output = (
                f"Score determinístico: {score_description}. "
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
