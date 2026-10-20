"""Arquivos do envio → um inventário normalizado.

Detecção **pelo conteúdo** (como no billing): cada arquivo é oferecido aos
normalizadores na ordem de ``NORMALIZADORES``, e o primeiro que o reconhece fica
com ele. Arquivos do mesmo formato vão para o mesmo normalizador, que resolve
as referências entre eles no fim.

Formatos diferentes do mesmo provedor (um snapshot do Config e um canônico com
o que o Config não grava) são juntados. Provedores diferentes no mesmo envio
são recusados: uma análise tem uma nuvem de origem.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from typing import Any

from app.domain.entities.topologia import Inventario, Recurso
from app.infrastructure.inventory_ingestion.construtor import ordem_da_regra
from app.infrastructure.inventory_ingestion.leitura import (
    ArquivoLido,
    InventarioInvalidoError,
    LimiteExcedidoError,
    Limites,
    ler_arquivos,
)
from app.infrastructure.inventory_ingestion.normalizadores.aws_config import (
    NormalizadorAwsConfig,
)
from app.infrastructure.inventory_ingestion.normalizadores.azure_graph import (
    NormalizadorAzureGraph,
)
from app.infrastructure.inventory_ingestion.normalizadores.base import Normalizador
from app.infrastructure.inventory_ingestion.normalizadores.gcp_asset import (
    NormalizadorGcpAsset,
)
from app.infrastructure.inventory_ingestion.normalizadores.oneshift import (
    NormalizadorOneShift,
)

NORMALIZADORES: tuple[type[Normalizador], ...] = (
    NormalizadorOneShift,
    NormalizadorAwsConfig,
    NormalizadorGcpAsset,
    NormalizadorAzureGraph,
)

_ANTES_DE_TUDO = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ResultadoDaNormalizacao:
    inventario: Inventario
    # arquivo (ou membro de zip) → formato detectado
    formatos: dict[str, str]


def normalizar_arquivos(
    arquivos: list[tuple[str, Path]], limites: Limites | None = None
) -> ResultadoDaNormalizacao:
    lim = limites or Limites()
    ativos: dict[str, Normalizador] = {}
    formatos: dict[str, str] = {}
    for bruto in ler_arquivos(arquivos, lim):
        objeto, primeiro, lido = _amostra(bruto)
        if objeto is None and primeiro is None:
            raise InventarioInvalidoError(f"{lido.nome}: arquivo sem recursos")
        classe = next((n for n in NORMALIZADORES if n.reconhece(objeto, primeiro)), None)
        if classe is None:
            raise InventarioInvalidoError(
                f"{lido.nome}: formato de inventário não reconhecido. Aceitos: snapshot do "
                "AWS Config, export do Cloud Asset Inventory, saída do Azure Resource Graph "
                "e oneshift-inventory-v1"
            )
        normalizador = ativos.get(classe.formato)
        if normalizador is None:
            normalizador = ativos[classe.formato] = classe()
        normalizador.consumir(lido)
        formatos[lido.nome] = classe.formato
    if not ativos:
        raise InventarioInvalidoError("nenhum arquivo de inventário no envio")

    inventarios = [ativos[f].finalizar() for f in sorted(ativos)]
    provedores = sorted({i.provedor for i in inventarios})
    if len(provedores) > 1:
        raise InventarioInvalidoError(
            f"arquivos de nuvens diferentes no mesmo envio ({', '.join(provedores)}); "
            "envie um inventário por nuvem"
        )
    inventario = juntar(inventarios)
    if len(inventario.recursos) > lim.recursos:
        raise LimiteExcedidoError(
            f"o inventário tem {len(inventario.recursos)} recursos; o limite é {lim.recursos}"
        )
    return ResultadoDaNormalizacao(inventario=inventario, formatos=formatos)


def _amostra(
    lido: ArquivoLido,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, ArquivoLido]:
    if lido.objeto is not None:
        return lido.objeto, None, lido
    itens: Iterator[dict[str, Any]] = lido.itens or iter(())
    primeiro = next(itens, None)
    if primeiro is None:
        return None, None, lido
    return None, primeiro, replace(lido, itens=chain([primeiro], itens))


def juntar(inventarios: list[Inventario]) -> Inventario:
    """Vários inventários do mesmo provedor → um. Recurso repetido fica com a
    captura mais recente (em empate, o do formato que vem depois na ordem)."""
    if len(inventarios) == 1:
        return inventarios[0]
    recursos: dict[str, Recurso] = {}
    contagens: Counter[str] = Counter()
    for inv in inventarios:
        contagens.update(inv.contagens)
        for r in inv.recursos:
            atual = recursos.get(r.chave)
            if atual is None:
                recursos[r.chave] = r
                continue
            contagens["duplicado"] += 1
            novo = (r.capturado_em or _ANTES_DE_TUDO) >= (atual.capturado_em or _ANTES_DE_TUDO)
            vencedor = r if novo else atual
            recursos[r.chave] = replace(vencedor, apelidos=atual.apelidos | r.apelidos)
    relacoes = {
        rel
        for inv in inventarios
        for rel in inv.relacoes
        if rel.de in recursos and rel.para in recursos
    }
    regras = {regra for inv in inventarios for regra in inv.regras if regra.grupo in recursos}
    capturas = [i.capturado_em for i in inventarios if i.capturado_em]
    return Inventario(
        provedor=inventarios[0].provedor,
        recursos=tuple(recursos[c] for c in sorted(recursos)),
        relacoes=tuple(sorted(relacoes, key=lambda x: (x.de, x.para, x.tipo.value))),
        regras=tuple(sorted(regras, key=ordem_da_regra)),
        capturado_em=max(capturas) if capturas else None,
        contagens=dict(sorted(contagens.items())),
    )
