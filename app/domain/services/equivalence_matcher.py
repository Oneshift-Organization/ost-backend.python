from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_price_point_repo import ICloudPricePointRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.services.perfil_de_familia import Perfil, aceita, mesma_arquitetura, perfil
from app.domain.services.regioes import RegiaoDePreco, escolher_regiao_de_preco

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
# Folga ao arredondar instâncias: 2,04 GiB medidos num SKU de 2 GiB é a mesma
# máquina (GiB x GB, horas parciais), não duas.
_FOLGA_DE_ARREDONDAMENTO = Decimal("0.02")


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
    # Proveniência do preço. Sem isto, "official" não quer dizer nada: pode ser
    # de ontem ou de três meses atrás, e quem lê não tem como saber. É o que
    # permite ao consumidor (tela ou agente) distinguir preço fresco de preço
    # velho — e contestar o segundo.
    price_source: str | None = None
    price_captured_at: datetime | None = None
    # Onde o preço vale. `aproximada` quando não havia preço da região pedida e
    # a cotação usou outra: o rótulo cai de `official` para `reference`, porque
    # preço oficial de outra região não é oficial para esta.
    price_region: str | None = None
    price_region_approximate: bool = False
    # Ciclo de vida do preço (PLANO_CICLO_DE_VIDA_DO_PRECO): só rótulo, não muda número
    price_lifecycle_state: str | None = None
    price_lifecycle_score: Decimal | None = None


@dataclass(frozen=True)
class _Candidatos:
    skus: list[CloudSku]
    confianca: str
    origem: str | None
    capturado_em: datetime | None
    regiao: RegiaoDePreco | None = None
    # O pior ponto dos candidatos, como a captura mais antiga acima
    estado: str | None = None
    score: Decimal | None = None


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
    # O destino não tinha candidato da arquitetura da origem que cobrisse o
    # pool, e o escolhido é da outra (x86 ↔ ARM): migrar exige replataforma.
    architecture_mismatch: bool = False


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

    def __init__(
        self,
        sku_repo: ICloudSkuRepository,
        price_repo: ICloudPricePointRepository | None = None,
    ) -> None:
        self._sku_repo = sku_repo
        self._price_repo = price_repo

    async def _candidates(
        self,
        provider: str,
        service_type: str,
        default_confidence: str,
        regioes: Sequence[str | None] | None = None,
    ) -> _Candidatos:
        """Candidatos + confiança + **proveniência** do preço.

        Price points oficiais primeiro, catálogo seed como fallback. É o seam
        que troca preço ~30% errado por preço real sem mudar o packing.

        Com ``regioes`` (as preferidas, em ordem), consulta só uma região: a
        escolhida por `regioes.escolher_regiao_de_preco`. Sem isso, os preços de
        todas as regiões entravam juntos e o packer escolhia o mais barato entre
        elas, que é sempre o dos EUA.

        A proveniência é devolvida junto porque é aqui — e só aqui — que se sabe
        de onde o preço veio. Quando há vários price points, a captura reportada
        é a **mais antiga**: num conjunto de candidatos, o mais velho é que
        limita a confiança.
        """
        if self._price_repo is not None:
            escolha: RegiaoDePreco | None = None
            if regioes is None:
                points = await self._price_repo.find(provider=provider, service_type=service_type)
            else:
                escolha = escolher_regiao_de_preco(
                    provider,
                    regioes,
                    await self._price_repo.regioes_com_preco(provider, service_type),
                    service_type=service_type,
                )
                points = (
                    await self._price_repo.find(
                        provider=provider, service_type=service_type, region=escolha.regiao
                    )
                    if escolha is not None
                    else []
                )
            if points:
                capturas = [p.captured_at for p in points if p.captured_at is not None]
                fontes = {p.source for p in points if p.source}
                com_score = [p for p in points if p.confianca_score is not None]
                pior = min(com_score, key=lambda p: p.confianca_score or 0, default=None)
                return _Candidatos(
                    [_pricepoint_to_sku(p) for p in points],
                    "reference" if escolha is not None and escolha.aproximada else "official",
                    sorted(fontes)[0] if fontes else None,
                    min(capturas) if capturas else None,
                    escolha,
                    estado=pior.estado if pior else None,
                    score=pior.confianca_score if pior else None,
                )
        catalog = await self._sku_repo.find_equivalents(
            provider=provider, service_type=service_type, limit=_CANDIDATE_LIMIT
        )
        # Catálogo seed não tem data de captura — é seed, não coleta. Dizer a
        # origem já basta para o consumidor saber que não é preço oficial.
        return _Candidatos(catalog, default_confidence, "seed_catalog", None)

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
        regioes: Mapping[str, Sequence[str | None]] | None = None,
        perfil_origem: Perfil | None = None,
    ) -> list[EquivalentOption]:
        """Return the best-value covering mix per target provider for a pool.

        ``regioes`` maps each target provider to the regions to quote, in order
        of preference (see ``domain/services/regioes``). Without it, prices of
        every synced region compete and the cheapest region wins.

        ``perfil_origem`` (see ``domain/services/perfil_de_familia``) filters
        the candidates: burstable only replaces burstable, and the same
        architecture is tried first. A mix of the other architecture is used
        only when none of the same covers the pool, and comes flagged.

        ``monthly_cost`` MUST already be expressed in the analysis currency
        (see ``domain/services/currency.py``) — this method does no conversion
        and would otherwise produce a currency-mixed ``cost_delta``.

        Selection is a small bin-packing: every candidate SKU is scored by the
        **monthly cost of the capacity-hours the pool used** (see
        ``_best_packing``), penalised for over-provisioning. Picking the
        cheapest SKU per unit (the previous behaviour) produced absurd mixes
        like 14x1vCPU/15GB for a 13vCPU/52GiB pool — technically covering,
        financially cheap, architecturally wrong.
        """
        options: list[EquivalentOption] = []

        for provider in target_providers:
            # Candidatos: price points reais primeiro (confiança por-provedor),
            # senão catálogo seed. O packer precisa deles sem filtro de min_vcpus
            # — ele considera SKUs pequenos multiplicados e grandes que cobrem só.
            cands = await self._candidates(
                provider,
                service_type,
                pricing_confidence,
                None if regioes is None else regioes.get(provider, ()),
            )

            best, arquitetura_diferente = _packing_por_perfil(
                vcpus, ram_gib, cands.skus, perfil_origem
            )
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
                        pricing_confidence=cands.confianca,
                        price_source=cands.origem,
                        price_captured_at=cands.capturado_em,
                        price_region=cands.regiao.regiao if cands.regiao else None,
                        price_region_approximate=bool(cands.regiao and cands.regiao.aproximada),
                        price_lifecycle_state=cands.estado,
                        price_lifecycle_score=cands.score,
                    ),
                    architecture_mismatch=arquitetura_diferente,
                )
            )

        return options


def _packing_por_perfil(
    vcpus: Decimal,
    ram_gib: Decimal,
    candidates: list[CloudSku],
    origem: Perfil | None,
) -> tuple[tuple[CloudSku, int, Decimal] | None, bool]:
    """Melhor mix respeitando o perfil da origem; e se trocou de arquitetura.

    Burstable fora quando a origem não é. Mesma arquitetura primeiro; a outra
    só quando nenhum mix da mesma cobre o pool.
    """
    if origem is None:
        return _best_packing(vcpus, ram_gib, candidates), False
    aceitos = [c for c in candidates if aceita(origem, perfil(c.provider, c.sku_id))]
    mesma = [c for c in aceitos if mesma_arquitetura(origem, perfil(c.provider, c.sku_id))]
    best = _packing_pela_base(vcpus, ram_gib, mesma, origem)
    if best is not None:
        return best, False
    outra = [c for c in aceitos if c not in mesma]
    best = _packing_pela_base(vcpus, ram_gib, outra, origem)
    return best, best is not None


def _packing_pela_base(
    vcpus: Decimal,
    ram_gib: Decimal,
    candidates: list[CloudSku],
    origem: Perfil,
) -> tuple[CloudSku, int, Decimal] | None:
    """Origem burstable: o destino sem burstable recebe a linha de base, não a vCPU
    nominal (uma t4g.xlarge entrega 40% de 4 vCPU sem crédito); o destino burstable
    recebe a nominal, porque também tem crédito. Vence o mais barato dos dois. A
    memória não muda: ela não é burstable."""
    if not origem.burstable or origem.base_cpu is None:
        return _best_packing(vcpus, ram_gib, candidates)
    com_credito = [c for c in candidates if (p := perfil(c.provider, c.sku_id)) and p.burstable]
    sem_credito = [c for c in candidates if c not in com_credito]
    opcoes = [
        x
        for x in (
            _best_packing(vcpus, ram_gib, com_credito),
            _best_packing(vcpus * origem.base_cpu, ram_gib, sem_credito),
        )
        if x is not None
    ]
    return min(opcoes, key=lambda x: x[2]) if opcoes else None


def _pricepoint_to_sku(p: CloudPricePoint) -> CloudSku:
    """Adapta um price point real ao candidato que o packer entende.

    O preço é por instância-hora (mesma semântica dos candidatos do catálogo),
    então ``_best_packing`` multiplica por 730h e pela fração ocupada."""
    return CloudSku(
        sku_id=p.sku_key,
        provider=p.provider,
        service_name=p.service_name,
        currency=p.currency,
        service_type=p.service_type,
        category="compute",
        region=p.region,
        price_per_unit=p.price,
        vcpus=p.vcpus,
        ram_gb=p.ram_gib,
    )


def _instances_needed(vcpus: Decimal, ram_gib: Decimal, cand: CloudSku) -> int:
    """Instâncias a provisionar: a fração ocupada, arredondada para cima.

    É o dimensionamento (o que o Terraform cria), não a base do preço.
    """
    fracao = _fracao_de_instancia(vcpus, ram_gib, cand)
    return max(1, math.ceil(fracao - _FOLGA_DE_ARREDONDAMENTO))


def _fracao_de_instancia(vcpus: Decimal, ram_gib: Decimal, cand: CloudSku) -> Decimal:
    """Quantas instâncias do candidato a capacidade medida ocupa, sem arredondar.

    A capacidade do pool é média do mês (vCPU-hora ÷ 730): 0,13 vCPU é uma
    máquina pequena ligada parte do tempo, ou de núcleo compartilhado. A
    dimensão que mais pesa decide (vCPU ou RAM).
    """
    ratios: list[Decimal] = []
    if cand.vcpus and cand.vcpus > 0 and vcpus > 0:
        ratios.append(vcpus / cand.vcpus)
    if cand.ram_gb and cand.ram_gb > 0 and ram_gib > 0:
        ratios.append(ram_gib / cand.ram_gb)
    return max(ratios) if ratios else Decimal("1")


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

    O custo é o da **capacidade-hora medida**: preço x 730 h x a fração de
    instância que o pool ocupa. A origem é cobrada pelas horas que rodou; cotar
    o destino como máquinas inteiras ligadas o mês todo punha um pool de 0,13
    vCPU (US$ 7) num c6a.large de US$ 86. ``instancias`` continua sendo o
    dimensionamento, arredondado para cima.

    Score = custo_mensal x (1 + λ x desperdício). O desperdício soma o excesso
    relativo de vCPU e de RAM das instâncias provisionadas, então um mix que
    entrega 4x a RAM necessária, ou uma máquina grande para um pool pequeno, é
    penalizado mesmo saindo barato. Retorna None se nenhum candidato cobre a
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
    best_key: tuple[Decimal, int, str] | None = None

    for cand, instances in priced:
        if instances > cap:
            continue

        fracao = _fracao_de_instancia(vcpus, ram_gib, cand)
        monthly = (cand.price_per_unit or Decimal("0")) * _HOURS_PER_MONTH * fracao
        waste = _waste_ratio((cand.vcpus or Decimal("0")) * instances, vcpus) + _waste_ratio(
            (cand.ram_gb or Decimal("0")) * instances, ram_gib
        )
        score = monthly * (Decimal("1") + _WASTE_PENALTY * waste)

        # Desempate explícito. Provedores costumam ter SKUs com preço/vCPU
        # idêntico em tamanhos diferentes (16vCPU x2 e 4vCPU x8 cobrem 32 vCPU
        # pelo MESMO custo e com o MESMO desperdício), então o score empata.
        # Sem critério, o vencedor era quem viesse primeiro do banco — decisão
        # incidental e não-reproduzível. Menos instâncias é objetivamente
        # melhor para o mesmo custo: menos máquinas para provisionar, atualizar
        # e monitorar. `sku_id` fecha a ordem total.
        chave = (score, instances, cand.sku_id)
        if best_key is None or chave < best_key:
            best_key = chave
            best = (cand, instances, monthly)

    return best
