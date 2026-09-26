"""Fatos medidos que só os exports detalhados trazem (RN9 do PLANO_CUR_BIGQUERY).

O billing agregado diz quanto se gastou com uma família de máquina; o export
detalhado diz QUAIS máquinas existiram, de que tipo, por quantas horas, e o
tamanho real de discos e bancos. Isto guarda esses fatos sem misturá-los com o
que é estimativa — o consumidor precisa saber a diferença.

Tudo opcional: bill agregado não tem nada disto, e o pipeline segue igual.
Módulo puro, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class InventarioDoPool:
    """Recursos de compute por trás da capacidade de um pool.

    ``vcpu_horas_medidas`` vem de shape informado pelo provedor (mapa
    ``product`` da AWS, ``system_labels`` do GCP); ``vcpu_horas_estimadas``, do
    shape deduzido pelo nome do tipo. A razão entre os dois é o que diz se a
    capacidade do pool é fato ou estimativa.
    """

    recursos: frozenset[str] = frozenset()
    por_tipo: tuple[tuple[str, frozenset[str]], ...] = ()
    horas_instancia: Decimal = _ZERO
    vcpu_horas_medidas: Decimal = _ZERO
    vcpu_horas_estimadas: Decimal = _ZERO

    @property
    def instancias(self) -> int:
        return len(self.recursos)

    def tipos(self) -> dict[str, int]:
        """Instâncias distintas por tipo de origem, maior contagem primeiro."""
        contagem = {tipo: len(ids) for tipo, ids in self.por_tipo}
        return dict(sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0])))

    def fracao_tempo_ativo(self, horas_periodo: Decimal) -> Decimal | None:
        """Horas ligadas / (instâncias x horas do período). Fato de billing —
        quanto tempo a máquina ficou ligada —, NÃO utilização de CPU."""
        if not self.recursos or horas_periodo <= 0 or self.horas_instancia <= 0:
            return None
        return min(Decimal("1"), self.horas_instancia / (self.instancias * horas_periodo))

    def fonte_capacidade(self) -> str | None:
        medida, estimada = self.vcpu_horas_medidas, self.vcpu_horas_estimadas
        if medida <= 0 and estimada <= 0:
            return None
        if estimada <= 0:
            return "medida"
        if medida <= 0:
            return "estimada"
        return "mista"

    def juntar(self, outro: InventarioDoPool) -> InventarioDoPool:
        tipos: dict[str, frozenset[str]] = dict(self.por_tipo)
        for tipo, ids in outro.por_tipo:
            tipos[tipo] = tipos.get(tipo, frozenset()) | ids
        return InventarioDoPool(
            recursos=self.recursos | outro.recursos,
            por_tipo=tuple(sorted(tipos.items())),
            horas_instancia=self.horas_instancia + outro.horas_instancia,
            vcpu_horas_medidas=self.vcpu_horas_medidas + outro.vcpu_horas_medidas,
            vcpu_horas_estimadas=self.vcpu_horas_estimadas + outro.vcpu_horas_estimadas,
        )


@dataclass(frozen=True, slots=True)
class ArmazenamentoMedido:
    """GB-mês somado de uma categoria. Num mês inteiro, é o tamanho médio
    provisionado; GB e GiB tratados como equivalentes (~7% de aproximação)."""

    gb: Decimal = _ZERO
    recursos: frozenset[str] = frozenset()
    # Custo das linhas medidas, por componente de origem (storage_hdd…).
    custo_por_categoria: dict[str, Decimal] = field(default_factory=dict)

    @property
    def quantidade(self) -> int:
        return len(self.recursos)


@dataclass(frozen=True, slots=True)
class BancoMedido:
    """Uma instância de banco (RDS, Cloud SQL, ElastiCache…) vista no billing."""

    resource_id: str
    classe: str
    motor: str | None
    multi_az: bool
    regiao: str | None
    horas: Decimal
    custo: Decimal


@dataclass(frozen=True, slots=True)
class InventarioMedido:
    """O que o export detalhado permitiu medir, fora dos pools de compute."""

    horas_periodo: Decimal
    armazenamento: dict[str, ArmazenamentoMedido] = field(default_factory=dict)
    bancos: tuple[BancoMedido, ...] = ()
