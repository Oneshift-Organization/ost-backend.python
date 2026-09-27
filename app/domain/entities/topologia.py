"""Modelo canônico do inventário de infraestrutura (PLANO_INVENTARIO_API, RI1 a RI4).

Cada formato de origem (snapshot do AWS Config, export do Cloud Asset
Inventory, consulta do Azure Resource Graph, formato canônico) é traduzido para
isto pelo normalizador dele. Daqui em diante ninguém sabe de qual nuvem veio:
topologia, correlação com billing e Terraform leem só este modelo.

O que o provedor não diz não é inventado. Referência para fora do inventário é
descartada e contada, tipo desconhecido vira ``outro`` e é contado pelo nome
nativo. As contagens vão para ``Inventario.contagens`` e aparecem na resposta:
o cliente vê o que ficou de fora e por quê.

Módulo puro, sem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TipoDeRecurso(StrEnum):
    # Papel "unidade": migra como coisa própria.
    INSTANCIA = "instancia"
    BANCO = "banco"
    CACHE = "cache"
    BALANCEADOR = "balanceador"
    BUCKET = "bucket"
    CLUSTER_KUBERNETES = "cluster_kubernetes"
    # Papel "anexo": migra com o dono.
    DISCO = "disco"
    INTERFACE_REDE = "interface_rede"
    IP_PUBLICO = "ip_publico"
    # Backup: listado, não migra como recurso.
    SNAPSHOT = "snapshot"
    # Fundação: existe no destino antes de qualquer onda.
    REDE = "rede"
    SUBREDE = "subrede"
    GRUPO_SEGURANCA = "grupo_seguranca"
    GATEWAY = "gateway"
    # O resto. Serve de conector (cluster de banco junta as instâncias dele),
    # mas não é unidade.
    OUTRO = "outro"


UNIDADE: frozenset[TipoDeRecurso] = frozenset(
    {
        TipoDeRecurso.INSTANCIA,
        TipoDeRecurso.BANCO,
        TipoDeRecurso.CACHE,
        TipoDeRecurso.BALANCEADOR,
        TipoDeRecurso.BUCKET,
        TipoDeRecurso.CLUSTER_KUBERNETES,
    }
)
ANEXO: frozenset[TipoDeRecurso] = frozenset(
    {TipoDeRecurso.DISCO, TipoDeRecurso.INTERFACE_REDE, TipoDeRecurso.IP_PUBLICO}
)
FUNDACAO: frozenset[TipoDeRecurso] = frozenset(
    {
        TipoDeRecurso.REDE,
        TipoDeRecurso.SUBREDE,
        TipoDeRecurso.GRUPO_SEGURANCA,
        TipoDeRecurso.GATEWAY,
    }
)
# Acesso a estes é acoplamento forte: consulta de banco entre nuvens é latência
# em cada chamada.
DADOS: frozenset[TipoDeRecurso] = frozenset({TipoDeRecurso.BANCO, TipoDeRecurso.CACHE})


class TipoDeRelacao(StrEnum):
    """Sempre lida como ``de → para`` (RI3)."""

    CONTIDO_EM = "contido_em"
    PROTEGIDO_POR = "protegido_por"
    ANEXADO_A = "anexado_a"
    MEMBRO_DE = "membro_de"
    ENCAMINHA_PARA = "encaminha_para"
    SNAPSHOT_DE = "snapshot_de"


class EstadoDoRecurso(StrEnum):
    ATIVO = "ativo"
    PARADO = "parado"
    # Disco ou IP que existe e não está ligado a nada: é pago e não serve a
    # ninguém. Candidato a descarte (RI12), decisão do cliente.
    DISPONIVEL = "disponivel"
    DESCONHECIDO = "desconhecido"


class TipoDeSeletor(StrEnum):
    CIDR = "cidr"
    GRUPO = "grupo"
    # A internet inteira: `0.0.0.0/0`, `::/0`, `*` e `Internet` da Azure.
    QUALQUER = "qualquer"
    # Rótulo do provedor sem faixa conhecida (`VirtualNetwork`, prefix list).
    SERVICO = "servico"


class Direcao(StrEnum):
    ENTRADA = "entrada"
    SAIDA = "saida"


class Acao(StrEnum):
    PERMITIR = "permitir"
    NEGAR = "negar"


# Protocolo canônico. Número de protocolo IP fora destes fica como texto
# (`"50"` para ESP) e não é traduzido para o destino.
PROTOCOLO_TODOS = "todos"
PROTOCOLOS_CONHECIDOS: frozenset[str] = frozenset({"tcp", "udp", "icmp", PROTOCOLO_TODOS})

PORTA_MAXIMA = 65535


@dataclass(frozen=True, slots=True)
class Seletor:
    tipo: TipoDeSeletor
    valor: str | None = None


@dataclass(frozen=True, slots=True)
class RegraDeRede:
    """Uma regra de firewall, já no vocabulário canônico (RI4).

    ``grupo`` é a chave do grupo a que a regra se aplica. ``destino`` estreita o
    alvo dentro do grupo (ASG de destino da Azure); ausente, vale para todos os
    membros. Portas nulas = todas.
    """

    grupo: str
    direcao: Direcao
    acao: Acao
    protocolo: str
    origem: Seletor
    porta_inicio: int | None = None
    porta_fim: int | None = None
    destino: Seletor | None = None
    prioridade: int | None = None
    descricao: str | None = None

    @property
    def todas_as_portas(self) -> bool:
        if self.protocolo == PROTOCOLO_TODOS:
            return True
        if self.porta_inicio is None and self.porta_fim is None:
            return self.protocolo in {"tcp", "udp"}
        return (self.porta_inicio or 0) <= 0 and (self.porta_fim or 0) >= PORTA_MAXIMA

    def cobre_porta(self, porta: int) -> bool:
        """A regra libera essa porta TCP/UDP? ICMP não tem porta."""
        if self.protocolo == PROTOCOLO_TODOS:
            return True
        if self.protocolo not in {"tcp", "udp"}:
            return False
        if self.porta_inicio is None and self.porta_fim is None:
            return True
        inicio = self.porta_inicio if self.porta_inicio is not None else 0
        fim = self.porta_fim if self.porta_fim is not None else PORTA_MAXIMA
        return inicio <= porta <= fim


@dataclass(frozen=True, slots=True)
class Recurso:
    """Um recurso da origem.

    ``chave`` é a identidade canônica (RI1): ARN na AWS, nome do asset no GCP,
    id do ARM em minúsculas na Azure. ``apelidos`` são as outras formas pelas
    quais o billing pode citar o mesmo recurso (RI13), já em minúsculas.
    """

    chave: str
    tipo: TipoDeRecurso
    tipo_nativo: str
    nome: str | None = None
    conta: str | None = None
    regiao: str | None = None
    zona: str | None = None
    estado: EstadoDoRecurso = EstadoDoRecurso.DESCONHECIDO
    atributos: Mapping[str, Any] = field(default_factory=dict)
    tags: Mapping[str, str] = field(default_factory=dict)
    apelidos: frozenset[str] = frozenset()
    capturado_em: datetime | None = None


@dataclass(frozen=True, slots=True)
class Relacao:
    de: str
    para: str
    tipo: TipoDeRelacao


@dataclass(frozen=True, slots=True)
class Inventario:
    """O inventário normalizado de um provedor.

    ``contagens`` guarda o que foi descartado ou não modelado, com o motivo na
    própria chave (``tipo_nao_modelado:AWS::IAM::Role``,
    ``referencia_externa:subrede``). Não é telemetria: vai para a resposta.
    """

    provedor: str
    recursos: tuple[Recurso, ...] = ()
    relacoes: tuple[Relacao, ...] = ()
    regras: tuple[RegraDeRede, ...] = ()
    capturado_em: datetime | None = None
    contagens: Mapping[str, int] = field(default_factory=dict)
