"""Linha classificada errado corrigida pelo uso, na agregação (lote v4, V4.3).

A classificação vem do enriquecimento (regras de SKU, catálogo). Alguns casos saíam
errados de um jeito que mudava o número:

- **reserva de RDS e ElastiCache** (`HeavyUsage:db.*`, `HeavyUsage:cache.*`): parte
  caía em compute (`core`), virava compromisso e não era recotada; ela paga banco e
  cache, então vai para a categoria de banco, como as outras reservas de banco;
- **MSK** (`Kafka.*`): broker e disco saíam como banco relacional e eram recotados
  pela taxa de banco. Não há taxa de streaming: fica ao custo atual;
- **Source Repository**, SKU "Storage": saía como disco HDD; é armazenamento de
  objetos, como o do Artifact Registry.

Corrigir aqui, e não só nas regras de SKU, vale para faturas já processadas sem
reenriquecer. Tabela de dados por nuvem, sem ``if``. Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Sem cotação: o agregador manda para o balde não classificado, ao custo atual
SEM_COTACAO = "unclassified"


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(texto, re.IGNORECASE)


# Provedor → (padrão em "serviço | uso", tipo de componente novo); o primeiro que casa
REGRAS: Mapping[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "aws": (
        (_r(r"heavyusage:(db|cache)\."), "database"),
        (_r(r"(kafka|msk).*\|.*kafka\."), SEM_COTACAO),
    ),
    "gcp": ((_r(r"source repositor[^|]*\| *storage$"), "object_storage"),),
}


def reclassificar(provedor: str, servico: str | None, uso: str | None, tipo: str) -> str:
    """O tipo de componente corrigido, ou o mesmo quando nenhuma regra casa."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, novo in REGRAS.get(provedor.lower(), ()):
        if padrao.search(texto):
            return novo
    return tipo
