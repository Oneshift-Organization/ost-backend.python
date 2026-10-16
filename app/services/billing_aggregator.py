from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from app.domain.entities.inventario_medido import (
    ArmazenamentoMedido,
    BancoMedido,
    InventarioDoPool,
    InventarioMedido,
)
from app.domain.entities.workload_pool import (
    AuxiliaryCosts,
    PoolCapacity,
    PoolCosts,
    PoolKey,
    WorkloadPool,
)
from app.domain.services.azure_shape import parse_azure_shape
from app.domain.services.compromisso_de_compute import paga_maquina_virtual
from app.domain.services.instance_shape import parse_instance_shape
from app.domain.services.rede_por_uso import CATEGORIAS as _CATEGORIAS_DE_REDE
from app.domain.services.rede_por_uso import EGRESS as _REDE
from app.domain.services.rede_por_uso import quantidade_na_unidade, tipo_de_rede
from app.domain.services.regioes import regiao_da_linha
from app.domain.services.sku_matcher import extract_family

if TYPE_CHECKING:
    from app.domain.entities.billing_enriched import BillingEnriched
    from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_HOURS_IN_MONTH = Decimal("730")
_COMPUTE_ROLES = frozenset({"core", "ram", "os_license"})
# Only accumulate usage into capacity pools when the billing unit is time-based.
# Second-billed units (Cloud Run, serverless) would inflate vcpus by ~3600x.
_HOUR_UNITS = frozenset({"hour", "hours", "hr", "hrs", "h", "minute", "minutes", "min"})
# component_type -> AuxiliaryCosts scalar bucket name (data-driven dispatch).
_AUX_BUCKET = {
    "gke_control_plane": "gke_control_plane",
    "network_egress": "network_egress",
    "storage_ssd": "storage_ssd",
    "storage_hdd": "storage_hdd",
    "object_storage": "object_storage",
    "database": "database",
}
_ARMAZENAMENTO = frozenset({"storage_ssd", "storage_hdd", "object_storage"})
_GIB_MES = "gibibyte month"

# Para onde vai o custo de uma linha de compute que não virou capacidade.
_COMPROMISSO = "commitment"
_ORFAO = "orphan"


def _dec(valor: object) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class AggregateResult:
    pools: tuple[WorkloadPool, ...]
    auxiliary: AuxiliaryCosts
    unmapped_cost: Decimal
    hours_window: Decimal
    confidence_breakdown: dict[str, Decimal] = field(default_factory=dict)
    # Custo de compute cuja capacidade foi ESTIMADA por shape (instance-hours →
    # vCPU/RAM), não faturada por-vCPU. Alimenta premissas.dimensionamento_estimado.
    estimated_capacity_cost: Decimal = Decimal("0")
    # ── Exports detalhados (PLANO_CUR_BIGQUERY, RN4/RN9/RN11) ──────────────
    # Custo de compute com capacidade MEDIDA (shape do provedor ou faturada
    # por vCPU com recurso identificado).
    measured_capacity_cost: Decimal = Decimal("0")
    inventario: InventarioMedido | None = None
    # Σ sob demanda (preço de lista) e Σ custo efetivo das MESMAS linhas —
    # a razão é o desconto que a origem tem hoje e perde ao migrar.
    custo_sob_demanda: Decimal = Decimal("0")
    custo_efetivo_referenciado: Decimal = Decimal("0")
    # Custo por base (amortized/billed): mostra se houve degradação.
    custo_por_base: dict[str, Decimal] = field(default_factory=dict)


def _novo_bucket() -> dict[str, Any]:
    return {
        "core_hours": _ZERO,
        "ram_gib_hours": _ZERO,
        "compute_cost": _ZERO,
        "license_cost": _ZERO,
        "shaped_cost": _ZERO,
        "measured_cost": _ZERO,
        # inventário (só export detalhado)
        "recursos": set(),
        "tipos": defaultdict(set),
        "horas_instancia": _ZERO,
        "vcpu_med": _ZERO,
        "vcpu_est": _ZERO,
    }


def _horas_do_periodo(items: list[BillingEnriched]) -> Decimal:
    """Horas do mês dominante — 744 em março, não os 730 da média."""
    contagem: dict[str, int] = defaultdict(int)
    for item in items:
        contagem[item.billing_period] += 1
    periodo = max(contagem, key=lambda p: contagem[p]) if contagem else ""
    try:
        ano, mes = int(periodo[:4]), int(periodo[5:7])
        return Decimal(calendar.monthrange(ano, mes)[1] * 24)
    except (ValueError, IndexError):
        return _HOURS_IN_MONTH


class BillingAggregator:
    """Converts BillingEnriched lines into WorkloadPools + auxiliary buckets.

    Classification is a dispatch on the line's ``component_type`` (assigned by
    the SkuMatcher) — no regex over descriptions, no per-cloud branching
    (spec §5.3). FOCUS-aligned: consumption (usage) is separated from cost;
    capacity is derived by dividing usage hours by the billing window.

    Export detalhado (linha com ``attributes``): a unidade de capacidade vem
    declarada pelo parser (``capacity_unit`` = instance | vcpu | gib) e o shape
    medido prevalece sobre o estimado pelo nome. Linha sem atributos segue o
    caminho de sempre — é o que mantém o legado idêntico.
    """

    def __init__(self, sku_repo: ICloudSkuRepository | None = None) -> None:
        # Kept for construction compatibility; classification no longer needs
        # the catalog (component_type lives on the enriched line).
        self._sku_repo = sku_repo

    async def aggregate(  # noqa: PLR0912, PLR0915 - despacho único por linha
        self,
        items: list[BillingEnriched],
        source_provider: str,
    ) -> AggregateResult:
        if not items:
            return AggregateResult(
                pools=(),
                auxiliary=AuxiliaryCosts(),
                unmapped_cost=Decimal("0"),
                hours_window=_HOURS_IN_MONTH,
                confidence_breakdown={},
            )

        hours_window = _HOURS_IN_MONTH  # default; can be refined by usage window

        pool_buckets: dict[PoolKey, dict[str, Any]] = defaultdict(_novo_bucket)
        aux_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        aux_por_regiao: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: Decimal("0"))
        )
        # Rede cotada pela quantidade: categoria → região → quantidade e custo medido
        aux_quantidade: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: _ZERO)
        )
        aux_medido: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
        aux_por_servico: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: _ZERO)
        )
        commitment_cost = Decimal("0")
        commitment_compute = Decimal("0")
        orphan_cost = Decimal("0")
        orphan_skus: list[str] = []
        confidence_breakdown: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

        detalhado = False
        armazenamento: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"gb": _ZERO, "recursos": set(), "custo": defaultdict(lambda: _ZERO)}
        )
        bancos: dict[str, dict[str, Any]] = {}
        custo_sob_demanda = _ZERO
        custo_referenciado = _ZERO
        custo_por_base: dict[str, Decimal] = defaultdict(lambda: _ZERO)

        for item in items:
            cost = item.cost
            component_type = item.component_type or "unclassified"
            atributos = item.attributes or {}
            confidence_breakdown[item.mapping_confidence or "missing"] += cost

            if atributos:
                detalhado = True
                if atributos.get("cost_basis"):
                    custo_por_base[atributos["cost_basis"]] += cost
                sob_demanda = _dec(atributos.get("on_demand_cost"))
                if sob_demanda is not None and atributos.get("charge_kind") == "usage":
                    custo_sob_demanda += sob_demanda
                    custo_referenciado += cost

            if component_type == "commitment":
                commitment_cost += cost
                if paga_maquina_virtual(item.usage_type, item.service_name):
                    commitment_compute += cost
            elif component_type in _COMPUTE_ROLES:
                destino, sobra = self._accumulate_compute(pool_buckets, item, component_type, cost)
                if destino == _COMPROMISSO:
                    commitment_cost += sobra
                    if paga_maquina_virtual(item.usage_type, item.service_name):
                        commitment_compute += sobra
                elif destino == _ORFAO:
                    orphan_cost += sobra
                    orphan_skus.append(item.sku_id or item.usage_type or "unknown")
            elif component_type == _REDE:
                # O balde de rede mistura egress, balanceador, NAT, IP e VPN; cada um
                # tem taxa própria (domain/services/rede_por_uso)
                tipo = tipo_de_rede(item.provider, item.service_name, item.usage_type)
                regiao = regiao_da_linha(item.provider, item.region, item.usage_type) or ""
                aux_totals[tipo.categoria] += cost
                aux_por_regiao[tipo.categoria][regiao] += cost
                aux_por_servico[tipo.categoria][item.service_name] += cost
                # Só linha paga dá quantidade: faixa gratuita da origem não é cobrada
                # e, como nas outras categorias, a do destino também não entra
                quantidade = (
                    quantidade_na_unidade(item.provider, tipo, item.usage_amount, item.usage_unit)
                    if cost > 0
                    else None
                )
                if quantidade is not None:
                    aux_quantidade[tipo.categoria][regiao] += quantidade
                    aux_medido[tipo.categoria][regiao] += cost
            elif component_type in _AUX_BUCKET:
                aux_totals[_AUX_BUCKET[component_type]] += cost
                aux_por_servico[_AUX_BUCKET[component_type]][item.service_name] += cost
                regiao = regiao_da_linha(item.provider, item.region, item.usage_type) or ""
                aux_por_regiao[_AUX_BUCKET[component_type]][regiao] += cost
                if atributos:
                    _medir_auxiliar(item, component_type, armazenamento, bancos)
            else:
                orphan_cost += cost
                orphan_skus.append(item.sku_id or item.usage_type or "unknown")

        pools: list[WorkloadPool] = []
        estimated_capacity_cost = Decimal("0")
        measured_capacity_cost = Decimal("0")
        for key, bucket in pool_buckets.items():
            estimated_capacity_cost += bucket["shaped_cost"]
            measured_capacity_cost += bucket["measured_cost"]
            vcpus = bucket["core_hours"] / hours_window if hours_window > 0 else Decimal("0")
            ram_gib = bucket["ram_gib_hours"] / hours_window if hours_window > 0 else Decimal("0")
            pools.append(
                WorkloadPool(
                    pool_key=key,
                    capacity=PoolCapacity(vcpus=vcpus, ram_gib=ram_gib),
                    costs=PoolCosts(
                        compute_base=bucket["compute_cost"],
                        os_licensing=bucket["license_cost"],
                    ),
                    hours_window=hours_window,
                    inventario=_inventario_do_pool(bucket),
                )
            )

        inventario = None
        if detalhado:
            inventario = InventarioMedido(
                horas_periodo=_horas_do_periodo(items),
                armazenamento={
                    categoria: ArmazenamentoMedido(
                        gb=v["gb"],
                        recursos=frozenset(v["recursos"]),
                        custo_por_categoria=dict(v["custo"]),
                    )
                    for categoria, v in armazenamento.items()
                    if v["gb"] > 0
                },
                bancos=_bancos_medidos(bancos),
            )

        return AggregateResult(
            pools=tuple(pools),
            auxiliary=AuxiliaryCosts(
                gke_control_plane=aux_totals["gke_control_plane"],
                storage_ssd=aux_totals["storage_ssd"],
                storage_hdd=aux_totals["storage_hdd"],
                object_storage=aux_totals["object_storage"],
                **{c: aux_totals[c] for c in _CATEGORIAS_DE_REDE},
                database=aux_totals["database"],
                commitment=commitment_cost,
                commitment_compute=commitment_compute,
                orphan=orphan_cost,
                orphan_skus=tuple(sorted(set(orphan_skus))),
                por_regiao={c: dict(r) for c, r in aux_por_regiao.items()},
                quantidade_por_regiao={c: dict(r) for c, r in aux_quantidade.items()},
                medido_por_regiao={c: dict(r) for c, r in aux_medido.items()},
                por_servico={c: dict(s) for c, s in aux_por_servico.items()},
            ),
            unmapped_cost=orphan_cost,
            hours_window=hours_window,
            confidence_breakdown=dict(confidence_breakdown),
            estimated_capacity_cost=estimated_capacity_cost,
            measured_capacity_cost=measured_capacity_cost,
            inventario=inventario,
            custo_sob_demanda=custo_sob_demanda,
            custo_efetivo_referenciado=custo_referenciado,
            custo_por_base=dict(custo_por_base),
        )

    def _accumulate_compute(
        self,
        pool_buckets: dict[PoolKey, dict[str, Any]],
        item: BillingEnriched,
        component_type: str,
        cost: Decimal,
    ) -> tuple[str | None, Decimal]:
        """Acumula uma linha de compute. Devolve (balde, custo) do que NÃO virou pool.

        Invariante: custo só entra no pool quando a capacidade correspondente
        entrou. No caminho agregado, a linha core/ram sem capacidade é
        compromisso (Savings Plan, RI, CUD) — é o único caso que o bill agregado
        produz. No export detalhado o compromisso chega com natureza própria, e
        uma linha de USO sem capacidade (CPU credit, cobrança por segundo) vai
        para órfão: chamá-la de compromisso esconderia um custo real atrás de
        um rótulo errado.
        """
        if component_type == "os_license":
            # Licença é custo recorrente legítimo do pool sem unidade de capacidade.
            pool_buckets[self._pool_key(item)]["license_cost"] += cost
            return None, Decimal("0")

        atributos = item.attributes or {}
        sem_capacidade = _ORFAO if atributos.get("charge_kind") is not None else _COMPROMISSO
        unidade_cap = atributos.get("capacity_unit")
        if unidade_cap == "instance":
            virou = self._por_instancia(pool_buckets, item, atributos, cost)
        elif unidade_cap in ("vcpu", "gib"):
            virou = self._por_unidade(pool_buckets, item, atributos, unidade_cap, cost)
        else:
            virou = self._agregado(pool_buckets, item, component_type, cost)
        return (None, Decimal("0")) if virou else (sem_capacidade, cost)

    def _por_instancia(
        self,
        pool_buckets: dict[PoolKey, dict[str, Any]],
        item: BillingEnriched,
        atributos: dict,
        cost: Decimal,
    ) -> bool:
        """Instância-hora (AWS, Azure): capacidade = horas vezes shape."""
        usage = item.usage_amount or Decimal("0")
        forma, medida = _shape(item, atributos)
        if forma is None or not _em_horas(item) or usage <= 0:
            return False
        vcpus, ram = forma
        bucket = pool_buckets[self._pool_key(item)]
        bucket["core_hours"] += usage * vcpus
        bucket["ram_gib_hours"] += usage * ram
        bucket["compute_cost"] += cost
        if medida:
            bucket["measured_cost"] += cost
            bucket["vcpu_med"] += usage * vcpus
        else:
            bucket["shaped_cost"] += cost
            bucket["vcpu_est"] += usage * vcpus
        _registrar_recurso(bucket, item, atributos, horas=usage)
        return True

    def _por_unidade(
        self,
        pool_buckets: dict[PoolKey, dict[str, Any]],
        item: BillingEnriched,
        atributos: dict,
        unidade_cap: str,
        cost: Decimal,
    ) -> bool:
        """vCPU-hora ou GiB-hora já faturados por unidade (GCP, OCI)."""
        usage = item.usage_amount or Decimal("0")
        unidade_ok = _em_horas(item) if unidade_cap == "vcpu" else "gib" in (item.usage_unit or "")
        if not unidade_ok or usage <= 0:
            return False
        bucket = pool_buckets[self._pool_key(item)]
        if unidade_cap == "vcpu":
            por_unidade = _dec(atributos.get("vcpus_per_unit")) or Decimal("1")
            vcpu_horas = usage * por_unidade
            bucket["core_hours"] += vcpu_horas
            bucket["vcpu_med"] += vcpu_horas
            cores = _dec(atributos.get("vcpus"))
            _registrar_recurso(bucket, item, atributos, horas=vcpu_horas / cores if cores else None)
        else:
            bucket["ram_gib_hours"] += usage
        bucket["compute_cost"] += cost
        bucket["measured_cost"] += cost
        return True

    def _agregado(
        self,
        pool_buckets: dict[PoolKey, dict[str, Any]],
        item: BillingEnriched,
        component_type: str,
        cost: Decimal,
    ) -> bool:
        """Caminho histórico, sem ``capacity_unit`` (bill agregado)."""
        usage = item.usage_amount or Decimal("0")
        is_hour_unit = _em_horas(item)
        # Faturamento por INSTÂNCIA (AWS BoxUsage): usage é hora-de-instância, não
        # hora-de-vCPU. Uma única linha carrega os dois eixos — expande ambos pelo
        # shape (m7g.2xlarge = 8 vCPU / 32 GiB). Sem shape (ex.: 'N2 Instance Core'
        # do GCP, já por-vCPU), segue o caminho por-unidade abaixo.
        shape = parse_instance_shape(item.usage_type) or parse_instance_shape(
            (item.sku_metadata or {}).get("instance_type")
        )
        if shape is not None and is_hour_unit and usage > 0:
            bucket = pool_buckets[self._pool_key(item)]
            bucket["core_hours"] += usage * shape.vcpus
            bucket["ram_gib_hours"] += usage * shape.ram_gib
            bucket["compute_cost"] += cost
            bucket["shaped_cost"] += cost
            return True

        # core / ram: a capacidade só acumula com unidade temporal (core/ram) ou
        # GiB (ram). Sem isso, a linha não representa capacidade viva.
        capacity_accrues = is_hour_unit or (
            component_type == "ram" and "gib" in (item.usage_unit or "").lower()
        )
        if not capacity_accrues or usage <= 0:
            return False

        bucket = pool_buckets[self._pool_key(item)]
        if component_type == "core":
            bucket["core_hours"] += usage
        else:  # ram
            bucket["ram_gib_hours"] += usage
        bucket["compute_cost"] += cost
        return True

    @staticmethod
    def _pool_key(item: BillingEnriched) -> PoolKey:
        # A região sai da coluna do export ou, no bill agregado, da própria
        # linha (prefixo do UsageType, localidade do SKU). Sem ela, um bill com
        # carga em São Paulo e na Virgínia virava um pool só, cotado num lugar só.
        return PoolKey(
            project_id=item.project_id or "_unknown",
            machine_family=BillingAggregator._family_of(item),
            region=regiao_da_linha(item.provider, item.region, item.usage_type) or "global",
        )

    @staticmethod
    def _family_of(item: BillingEnriched) -> str:
        # Família informada pelo parser (tamanho da VM Azure, shape OCI) vem
        # antes: o descritor dessas nuvens não tem o formato `família.tamanho`
        # e toda VM cairia em "general".
        informada = (item.attributes or {}).get("instance_family")
        if isinstance(informada, str) and informada:
            return informada
        metadata = item.sku_metadata or {}
        family = metadata.get("family")
        if isinstance(family, str) and family:
            return family
        return extract_family(item.usage_type) or "general"


def _em_horas(item: BillingEnriched) -> bool:
    partes = (item.usage_unit or "").lower().split()
    return bool(partes) and partes[0] in _HOUR_UNITS


def _shape(item: BillingEnriched, atributos: dict) -> tuple[tuple[Decimal, Decimal] | None, bool]:
    """(vCPU, RAM) da instância e se é MEDIDO.

    Medido só quando o provedor informou os dois eixos. Com um só (a Azure dá
    vCPU e não dá RAM), completa pela estimativa e conta como estimado — melhor
    subestimar a confiança do que vender meia medição como fato.
    """
    vcpus = _dec(atributos.get("vcpus"))
    ram = _dec(atributos.get("memory_gib"))
    if vcpus and ram:
        return (vcpus, ram), True
    tipo = atributos.get("instance_type") or ""
    estimado = (
        parse_instance_shape(tipo)
        or parse_instance_shape(item.usage_type)
        or parse_azure_shape(tipo)
    )
    if vcpus and estimado is not None:
        return (vcpus, estimado.ram_gib / estimado.vcpus * vcpus), False
    if estimado is not None:
        return (estimado.vcpus, estimado.ram_gib), False
    return None, False


def _registrar_recurso(
    bucket: dict[str, Any], item: BillingEnriched, atributos: dict, *, horas: Decimal | None
) -> None:
    if not item.resource_id:
        return
    bucket["recursos"].add(item.resource_id)
    tipo = atributos.get("instance_type")
    if tipo:
        bucket["tipos"][tipo].add(item.resource_id)
    if horas is not None:
        bucket["horas_instancia"] += horas


def _inventario_do_pool(bucket: dict[str, Any]) -> InventarioDoPool | None:
    if not bucket["recursos"] and bucket["vcpu_med"] <= 0 and bucket["vcpu_est"] <= 0:
        return None
    if not bucket["recursos"] and not (bucket["vcpu_med"] > 0):
        # Só estimativa por nome, sem recurso: é o caminho agregado. Nada a dizer.
        return None
    return InventarioDoPool(
        recursos=frozenset(bucket["recursos"]),
        por_tipo=tuple(sorted((t, frozenset(ids)) for t, ids in bucket["tipos"].items())),
        horas_instancia=bucket["horas_instancia"],
        vcpu_horas_medidas=bucket["vcpu_med"],
        vcpu_horas_estimadas=bucket["vcpu_est"],
    )


def _medir_auxiliar(
    item: BillingEnriched,
    component_type: str,
    armazenamento: dict[str, dict[str, Any]],
    bancos: dict[str, dict[str, Any]],
) -> None:
    """Tamanho medido de armazenamento e inventário de bancos."""
    atributos = item.attributes or {}
    if component_type in _ARMAZENAMENTO and (item.usage_unit or "") == _GIB_MES:
        # Snapshot medido à parte: somado ao disco, viraria volume no Terraform.
        chave = "snapshots" if atributos.get("storage_kind") == "snapshot" else component_type
        destino = armazenamento[chave]
        destino["gb"] += item.usage_amount or _ZERO
        # Por categoria de ORIGEM: o derivador desconta do balde de disco o que
        # era snapshot e estima o resto — sem isso, ou o snapshot virava disco,
        # ou o balde inteiro sumia do pacote.
        destino["custo"][component_type] += item.cost
        if item.resource_id:
            destino["recursos"].add(item.resource_id)
        return
    if component_type != "database" or not item.resource_id:
        return
    banco = bancos.setdefault(
        item.resource_id,
        {
            "classe": None,
            "motor": None,
            "multi_az": False,
            "regiao": item.region,
            "horas": _ZERO,
            "custo": _ZERO,
        },
    )
    banco["custo"] += item.cost
    if atributos.get("capacity_unit") == "instance" and atributos.get("instance_type"):
        banco["classe"] = atributos["instance_type"]
        banco["horas"] += item.usage_amount or _ZERO
        banco["motor"] = atributos.get("database_engine") or atributos.get("cache_engine")
        implantacao = str(atributos.get("deployment_option") or "")
        banco["multi_az"] = banco["multi_az"] or "multi" in implantacao.casefold()


def _bancos_medidos(bancos: dict[str, dict[str, Any]]) -> tuple[BancoMedido, ...]:
    """Só o que tem classe medida: storage de banco sem instância não é banco."""
    medidos = [
        BancoMedido(
            resource_id=rid,
            classe=b["classe"],
            motor=b["motor"],
            multi_az=b["multi_az"],
            regiao=b["regiao"],
            horas=b["horas"],
            custo=b["custo"],
        )
        for rid, b in bancos.items()
        if b["classe"]
    ]
    return tuple(sorted(medidos, key=lambda b: (-b.custo, b.resource_id)))
