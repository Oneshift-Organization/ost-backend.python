from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.entities.inventario_medido import InventarioDoPool


@dataclass(frozen=True, slots=True)
class PoolKey:
    project_id: str
    machine_family: str
    region: str


@dataclass(frozen=True, slots=True)
class PoolCapacity:
    vcpus: Decimal
    ram_gib: Decimal


@dataclass(frozen=True, slots=True)
class PoolCosts:
    compute_base: Decimal
    os_licensing: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class WorkloadPool:
    pool_key: PoolKey
    capacity: PoolCapacity
    costs: PoolCosts
    hours_window: Decimal
    os_license_type: str | None = None
    gke_control_plane_cost: Decimal | None = None
    # Só com export detalhado; None no caminho agregado.
    inventario: InventarioDoPool | None = None

    @property
    def monthly_cost(self) -> Decimal:
        return self.costs.compute_base + self.costs.os_licensing


@dataclass(frozen=True, slots=True)
class FamilyPoolKey:
    """Chave de um pool agregado por família de máquina + região."""

    machine_family: str
    region: str

    def as_str(self) -> str:
        return f"{self.machine_family}/{self.region}"


@dataclass(frozen=True, slots=True)
class FamilyPool:
    """Pool agregado por (família, região) — unidade de comparação cross-cloud.

    Substitui o pool por projeto como entrada do matching: o dimensionamento
    precisa ser feito sobre a capacidade TOTAL da família, não projeto a
    projeto (senão hardware e custo ficam em escalas diferentes).
    ``source_projects`` mantém a rastreabilidade para drill-down.
    """

    pool_key: FamilyPoolKey
    capacity: PoolCapacity
    costs: PoolCosts
    hours_window: Decimal
    source_projects: tuple[str, ...] = ()
    inventario: InventarioDoPool | None = None

    @property
    def monthly_cost(self) -> Decimal:
        return self.costs.compute_base + self.costs.os_licensing


@dataclass(frozen=True, slots=True)
class AuxiliaryCosts:
    gke_control_plane: Decimal = Decimal("0")
    storage_ssd: Decimal = Decimal("0")
    storage_hdd: Decimal = Decimal("0")
    # Armazenamento de objetos (S3, Cloud Storage, Blob). Separado do bloco
    # porque a taxa é ~metade ($0,023/GB contra $0,045) e o recurso é outro:
    # um bucket não é um disco anexado a instância. Antes disto, `TimedStorage`
    # da AWS e `Cloud Storage` do GCP caíam em `storage_hdd` e eram
    # reprecificados com taxa de disco — errando o GB e a comparação.
    object_storage: Decimal = Decimal("0")
    network_egress: Decimal = Decimal("0")
    # O balde de rede separado por tipo (domain/services/rede_por_uso): o egress acima
    # fica só com o tráfego; balanceador, NAT, IP, VPN e tráfego entre zonas têm taxa
    # própria e são cotados pela quantidade medida. `network_other` (endpoint, DNS,
    # resolver, log) fica ao custo atual.
    inter_zone_transfer: Decimal = Decimal("0")
    load_balancer: Decimal = Decimal("0")
    load_balancer_data: Decimal = Decimal("0")
    nat_gateway: Decimal = Decimal("0")
    nat_gateway_data: Decimal = Decimal("0")
    public_ip: Decimal = Decimal("0")
    vpn: Decimal = Decimal("0")
    network_other: Decimal = Decimal("0")
    database: Decimal = Decimal("0")
    # Custo de linhas com papel de compute (core/ram) que NÃO contribuíram
    # capacidade — Savings Plans, Reserved Instances, Committed Use, Universal
    # Credits. Prepago/reserva por natureza: entra como custo, não como vCPU.
    # Segregá-lo é o que impede o preço/vCPU do pool de explodir (invariante:
    # custo só entra no pool se a capacidade correspondente entrou).
    commitment: Decimal = Decimal("0")
    # A parte do compromisso que paga horas de máquina virtual (Savings Plan de
    # compute, reserva de EC2, CUD de vCPU/RAM). Está contida em `commitment`.
    # A consolidação a tira da origem junto com os pools, porque o destino é
    # cotado sem compromisso (domain/services/compromisso_de_compute.py).
    commitment_compute: Decimal = Decimal("0")
    orphan: Decimal = Decimal("0")
    orphan_skus: tuple[str, ...] = ()
    # Categoria → região da linha → custo ("" = linha sem região). A cotação das
    # categorias usa a taxa da zona de cada parte (P1); a soma é o total da categoria.
    por_regiao: Mapping[str, Mapping[str, Decimal]] = field(default_factory=dict)
    # Categoria cotada pela quantidade → região → quantidade na unidade dela, e o custo
    # das linhas que trouxeram essa quantidade (o resto usa o preço efetivo delas)
    quantidade_por_regiao: Mapping[str, Mapping[str, Decimal]] = field(default_factory=dict)
    medido_por_regiao: Mapping[str, Mapping[str, Decimal]] = field(default_factory=dict)
    # Categoria → serviço da fatura → custo. Reparte a cotação da categoria entre os
    # serviços (o detalhe do AWSELB com o preço das linhas dele, PLANO_PRECO_NAO_COMPUTE R2)
    por_servico: Mapping[str, Mapping[str, Decimal]] = field(default_factory=dict)

    @property
    def any_positive(self) -> bool:
        return any(
            (
                self.gke_control_plane > 0,
                self.storage_ssd > 0,
                self.storage_hdd > 0,
                self.object_storage > 0,
                self.network_egress > 0,
                self.inter_zone_transfer > 0,
                self.load_balancer > 0,
                self.load_balancer_data > 0,
                self.nat_gateway > 0,
                self.nat_gateway_data > 0,
                self.public_ip > 0,
                self.vpn > 0,
                self.network_other > 0,
                self.database > 0,
                self.commitment > 0,
                self.orphan > 0,
            )
        )
