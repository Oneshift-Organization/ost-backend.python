"""Quanto o escopo de uma migração custa no destino (PLANO_ESCOPO_DE_MIGRACAO, E2).

A análise cota o compute por **pool** (família e região), não por aplicação. Para
um escopo, cada recurso dele é cotado pelo que ele é, com o preço que a própria
análise já usou:

- **instância**: a taxa do pool da família e da região dela (custo estimado no
  destino ÷ custo de origem do pool). Dentro de um pool o preço por vCPU-hora é
  o mesmo, então a parte do custo é a parte da capacidade. Sem pool na região,
  a média da família; sem a família, a média de todos os pools;
- **disco, banco, cache e bucket**: a razão de preço da categoria (a mesma taxa
  que a análise usou para cotar a categoria inteira);
- **o resto** (balanceador, cluster gerenciado, filas…): sem cotação, fora da
  comparação e declarado.

Cada linha diz o método. Função pura; dinheiro em ``Decimal`` na moeda da análise.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.entities.topologia import TipoDeRecurso

_ZERO = Decimal(0)

POOL = "pool"
POOL_DA_FAMILIA = "pool_da_familia"
MEDIA_DOS_POOLS = "media_dos_pools"
CATEGORIA = "categoria"

# Disco magnético nos quatro provedores; o resto conta como SSD
_HDD = ("st1", "sc1", "standard", "pd-standard", "standard_lrs", "hdd")

CATEGORIA_DO_TIPO: Mapping[TipoDeRecurso, str] = {
    TipoDeRecurso.BANCO: "banco",
    TipoDeRecurso.CACHE: "banco",
    TipoDeRecurso.BUCKET: "objetos",
}


@dataclass(frozen=True, slots=True)
class LinhaDoEscopo:
    """Uma linha de custo de um recurso do escopo, já na moeda da análise."""

    servico: str
    custo: Decimal
    tipo: TipoDeRecurso | None
    familia: str | None = None
    regiao: str | None = None
    tipo_disco: str | None = None
    unidade: str | None = None  # para recortar as linhas de um escopo


@dataclass(frozen=True, slots=True)
class PoolCotado:
    familia: str
    regiao: str | None
    custo_origem: Decimal
    destino: Mapping[str, Decimal]  # provedor → custo mensal estimado


@dataclass(frozen=True, slots=True)
class ServicoCotado:
    servico: str
    custo: Decimal
    destino: Mapping[str, Decimal | None]  # None: sem cotação nesse provedor
    metodos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TotalNoDestino:
    provedor: str
    custo_estimado: Decimal
    custo_origem_cotado: Decimal  # a parte do escopo que tem cotação
    sem_cotacao: Decimal


@dataclass(frozen=True, slots=True)
class CotacaoDoEscopo:
    por_provedor: tuple[TotalNoDestino, ...]
    por_servico: tuple[ServicoCotado, ...]


def _taxa(destino: Decimal, origem: Decimal) -> Decimal | None:
    return destino / origem if origem > 0 else None


def taxas_dos_pools(
    pools: Iterable[PoolCotado], provedores: Iterable[str]
) -> tuple[
    dict[tuple[str, str | None], dict[str, Decimal]],
    dict[str, dict[str, Decimal]],
    dict[str, Decimal],
]:
    """Taxa por (família, região), por família e a média geral, por provedor.

    As médias são ponderadas pelo custo de origem: somam destino e origem antes
    de dividir.
    """
    provedores = tuple(provedores)
    exata: dict[tuple[str, str | None], dict[str, Decimal]] = {}
    fam_dest: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    fam_orig: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    geral_dest: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    geral_orig: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for p in pools:
        for prov in provedores:
            d = p.destino.get(prov)
            if d is None or p.custo_origem <= 0:
                continue
            if (t := _taxa(d, p.custo_origem)) is not None:
                exata.setdefault((p.familia, p.regiao), {})[prov] = t
            fam_dest[p.familia][prov] += d
            fam_orig[p.familia][prov] += p.custo_origem
            geral_dest[prov] += d
            geral_orig[prov] += p.custo_origem
    familia = {
        f: {prov: fam_dest[f][prov] / fam_orig[f][prov] for prov in fam_dest[f]} for f in fam_dest
    }
    geral = {prov: geral_dest[prov] / geral_orig[prov] for prov in geral_dest if geral_orig[prov]}
    return exata, familia, geral


def cotar_escopo(
    linhas: Iterable[LinhaDoEscopo],
    pools: Iterable[PoolCotado],
    taxas_de_categoria: Mapping[str, Mapping[str, Decimal]],
    provedores: Iterable[str],
) -> CotacaoDoEscopo:
    """``taxas_de_categoria``: provedor → categoria (banco, ssd, hdd, objetos) → razão."""
    provedores = tuple(provedores)
    exata, familia, geral = taxas_dos_pools(pools, provedores)

    def taxa(linha: LinhaDoEscopo, prov: str) -> tuple[Decimal | None, str | None]:
        if linha.tipo is TipoDeRecurso.INSTANCIA:
            if linha.familia:
                if (t := exata.get((linha.familia, linha.regiao), {}).get(prov)) is not None:
                    return t, POOL
                if (t := familia.get(linha.familia, {}).get(prov)) is not None:
                    return t, POOL_DA_FAMILIA
            if (t := geral.get(prov)) is not None:
                return t, MEDIA_DOS_POOLS
            return None, None
        categoria: str | None
        if linha.tipo is TipoDeRecurso.DISCO:
            hdd = (
                any(h == (linha.tipo_disco or "").lower() for h in _HDD)
                or "hdd" in (linha.tipo_disco or "").lower()
            )
            categoria = "hdd" if hdd else "ssd"
        else:
            categoria = CATEGORIA_DO_TIPO.get(linha.tipo) if linha.tipo else None
        if categoria and (t := taxas_de_categoria.get(prov, {}).get(categoria)) is not None:
            return t, CATEGORIA
        return None, None

    custo: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    destino: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    cotado: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    metodos: dict[str, set[str]] = defaultdict(set)
    for linha in linhas:
        custo[linha.servico] += linha.custo
        for prov in provedores:
            t, metodo = taxa(linha, prov)
            if t is None:
                continue
            destino[linha.servico][prov] += linha.custo * t
            cotado[linha.servico][prov] += linha.custo
            metodos[linha.servico].add(metodo)  # type: ignore[arg-type]

    por_servico = tuple(
        ServicoCotado(
            servico=s,
            custo=c,
            destino={
                prov: (destino[s][prov] if cotado[s][prov] > 0 else None) for prov in provedores
            },
            metodos=tuple(sorted(metodos[s])),
        )
        for s, c in sorted(custo.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    total = sum(custo.values(), _ZERO)
    por_provedor = tuple(
        TotalNoDestino(
            provedor=prov,
            custo_estimado=sum((destino[s][prov] for s in custo), _ZERO),
            custo_origem_cotado=sum((cotado[s][prov] for s in custo), _ZERO),
            sem_cotacao=total - sum((cotado[s][prov] for s in custo), _ZERO),
        )
        for prov in provedores
    )
    return CotacaoDoEscopo(por_provedor=por_provedor, por_servico=por_servico)
