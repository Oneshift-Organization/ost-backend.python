"""O que a análise de topologia devolve (RI5 a RI12 do PLANO_INVENTARIO_API).

Tudo imutável e ordenado: o resultado vira contrato de API, payload congelado
de análise e README de Terraform. Duas execuções com a mesma entrada têm que
produzir o mesmo objeto.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Natureza(StrEnum):
    """Por que uma unidade depende de outra (RI5)."""

    DADOS = "dados"
    ENCAMINHAMENTO = "encaminhamento"
    SERVICO = "servico"
    ADMINISTRATIVO = "administrativo"
    DECLARADA = "declarada"


class Acoplamento(StrEnum):
    FORTE = "forte"  # migram juntas
    FRACO = "fraco"  # ordem: a dependência antes
    NENHUM = "nenhum"  # não restringe (acesso administrativo)


class Severidade(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    INFORMATIVA = "informativa"


class Nivel(StrEnum):
    """Escala de criticidade (D2)."""

    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"


ORDEM_DO_NIVEL: Mapping[Nivel, int] = {Nivel.BAIXA: 1, Nivel.MEDIA: 2, Nivel.ALTA: 3}


@dataclass(frozen=True, slots=True)
class Evidencia:
    """Por que existe uma dependência: a regra, a relação ou a declaração."""

    origem: str  # "regra" | "relacao" | "declaracao"
    descricao: str
    grupo: str | None = None
    de_recurso: str | None = None
    para_recurso: str | None = None


@dataclass(frozen=True, slots=True)
class Unidade:
    id: str
    nome: str
    recursos: tuple[str, ...]
    aplicacao: str | None = None
    ambiente: str | None = None
    conta: str | None = None
    regiao: str | None = None
    tipos: Mapping[str, int] = field(default_factory=dict)
    # Tem recurso que não é banco nem cache: é uma aplicação, não só dado.
    aplicacao_dentro: bool = False


@dataclass(frozen=True, slots=True)
class Dependencia:
    de: str  # unidade que depende
    para: str  # unidade de que depende
    natureza: Natureza
    acoplamento: Acoplamento
    portas: tuple[str, ...] = ()
    evidencias: tuple[Evidencia, ...] = ()
    rebaixada: str | None = None  # motivo, quando o acoplamento forte virou fraco


@dataclass(frozen=True, slots=True)
class Criticidade:
    declarada: Nivel | None
    efetiva: Nivel | None
    origem: str  # "declarada", "tag:<k>=<v>", "herdada de <grupo>", "nao_declarada"


@dataclass(frozen=True, slots=True)
class GrupoDeMovimentacao:
    id: str
    nome: str
    unidades: tuple[str, ...]
    recursos: int
    criticidade: Criticidade
    raio_de_impacto: int = 0
    depende_de: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Onda:
    numero: int
    grupos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DependenciaEntreOndas:
    de_grupo: str
    para_grupo: str
    onda_de: int
    onda_para: int
    portas: tuple[str, ...]
    evidencias: tuple[Evidencia, ...]


@dataclass(frozen=True, slots=True)
class Risco:
    regra: str
    severidade: Severidade
    titulo: str
    detalhe: str
    recursos: tuple[str, ...] = ()
    total: int = 0


@dataclass(frozen=True, slots=True)
class Ocioso:
    recurso: str
    tipo: str
    motivo: str
    tamanho_gb: int | None = None


@dataclass(frozen=True, slots=True)
class Premissa:
    campo: str
    valor: str | None
    base: str


@dataclass(frozen=True, slots=True)
class AnaliseDeTopologia:
    unidades: tuple[Unidade, ...]
    dependencias: tuple[Dependencia, ...]
    grupos: tuple[GrupoDeMovimentacao, ...]
    ondas: tuple[Onda, ...]
    independentes: tuple[str, ...]
    ferramentas: tuple[str, ...]
    dependencias_entre_ondas: tuple[DependenciaEntreOndas, ...]
    riscos: tuple[Risco, ...]
    ociosos: tuple[Ocioso, ...]
    premissas: tuple[Premissa, ...]
    resumo: Mapping[str, object]
    fingerprint: str
    # recurso → unidade e unidade → grupo: o posicionamento e a correlação usam.
    unidade_de: Mapping[str, str] = field(default_factory=dict)
    grupo_de: Mapping[str, str] = field(default_factory=dict)
