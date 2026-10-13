"""Exclusividade de um recurso pelos atributos do inventário (PLANO_LOCKIN_INVENTARIO B3).

O catálogo dá a nota do serviço ("RDS: médio"). O inventário diz o que o
cliente de fato usa dentro dele: um RDS PostgreSQL é motor aberto, um Aurora
tem armazenamento e recursos que só existem na AWS, um SQL Server leva licença
junto. A regra é dado, não código: padrão do campo → nível e motivo, a primeira
que casa vence, sem if por nuvem.

Ficam aqui, e não numa tabela do banco, porque são poucas, mudam com revisão de
código e não por operação. Função pura, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from app.domain.entities.topologia import Recurso


@dataclass(frozen=True, slots=True)
class RegraDeExclusividade:
    campo: str  # "tipo_nativo" ou o nome de um atributo (`motor`)
    padrao: str  # glob, comparado em minúsculas
    nivel: str  # low | medium | high
    motivo: str


_R = RegraDeExclusividade

# Do mais específico para o mais geral: a primeira que casa vence.
REGRAS: tuple[RegraDeExclusividade, ...] = (
    _R(
        "tipo_nativo",
        "aws::dynamodb::*",
        "high",
        "DynamoDB: API e modelo de dados próprios da AWS; migrar é reescrever o acesso a dados.",
    ),
    _R(
        "tipo_nativo",
        "spanner.googleapis.com/*",
        "high",
        "Spanner: banco distribuído próprio do GCP, sem equivalente direto nos outros.",
    ),
    _R(
        "tipo_nativo",
        "bigtableadmin.googleapis.com/*",
        "high",
        "Bigtable: API própria do GCP; nos outros, só por compatibilidade com HBase.",
    ),
    _R(
        "tipo_nativo",
        "microsoft.documentdb/*",
        "high",
        "Cosmos DB: APIs e níveis de consistência próprios da Azure.",
    ),
    _R(
        "motor",
        "aurora*",
        "medium",
        "Aurora: compatível com PostgreSQL/MySQL, mas armazenamento distribuído, "
        "Global Database e Backtrack só existem na AWS.",
    ),
    _R(
        "motor",
        "oracle*",
        "medium",
        "Oracle: migra, mas licença e edição mudam a conta e limitam o destino.",
    ),
    _R(
        "motor",
        "sqlserver*",
        "medium",
        "SQL Server: migra, mas licença e edição mudam a conta e limitam o destino.",
    ),
    _R("motor", "postgres*", "low", "PostgreSQL: motor aberto, gerenciado nos quatro provedores."),
    _R("motor", "mysql*", "low", "MySQL: motor aberto, gerenciado nos quatro provedores."),
    _R("motor", "mariadb*", "low", "MariaDB: motor aberto, compatível com o MySQL gerenciado."),
    _R("motor", "redis*", "low", "Redis: protocolo aberto, gerenciado nos quatro provedores."),
    _R(
        "motor", "valkey*", "low", "Valkey: compatível com Redis, gerenciado nos quatro provedores."
    ),
    _R("motor", "memcached*", "low", "Memcached: protocolo aberto, sem estado para migrar."),
)


def classificar(
    recurso: Recurso, regras: tuple[RegraDeExclusividade, ...] = REGRAS
) -> RegraDeExclusividade | None:
    for regra in regras:
        valor = (
            recurso.tipo_nativo
            if regra.campo == "tipo_nativo"
            else recurso.atributos.get(regra.campo)
        )
        if valor and fnmatchcase(str(valor).strip().lower(), regra.padrao):
            return regra
    return None
