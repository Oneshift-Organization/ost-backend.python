"""Subredes espelhadas com endereçamento novo (RI15.2 e RI15.3).

Cada subrede de origem com recurso posicionado vira uma subrede de destino
**dentro do CIDR da requisição**. Reusar os CIDRs da origem quebraria a VPN da
migração: faixas sobrepostas não roteiam. A subrede padrão (a primeira `/24`,
como sempre) fica reservada, para o que não tem posicionamento.

A alocação é determinística e tenta, em ordem, até uma caber inteira:

1. o prefixo de cada subrede de origem, nunca maior que metade da rede;
2. todas `/24`;
3. todas do mesmo tamanho, de `/25` a `/28` (o menor que as quatro nuvens
   aceitam);
4. nenhuma: tudo na subrede padrão, com aviso.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_BITS = 32
_PREFIXO_PADRAO = 24
_MENOR_SUBREDE = 28
_LETRAS = "abc"


@dataclass(frozen=True, slots=True)
class SubredeDeOrigem:
    chave: str
    nome: str | None
    cidr: str | None
    publica: bool
    zona: str | None


@dataclass(frozen=True, slots=True)
class SubredeAlocada:
    origem: SubredeDeOrigem
    cidr: str
    # Índice da zona no destino: 0, 1, 2 (a, b, c). Na AWS vira
    # `element(data.aws_availability_zones.disponiveis.names, i)`.
    zona: int
    zona_da_origem: bool


@dataclass(frozen=True, slots=True)
class Alocacao:
    subredes: tuple[SubredeAlocada, ...]
    estrategia: str
    avisos: tuple[str, ...]


def de_payload(subredes: Iterable[Mapping[str, Any]]) -> list[SubredeDeOrigem]:
    return [
        SubredeDeOrigem(
            chave=str(s["chave"]),
            nome=str(s["nome"]) if s.get("nome") else None,
            cidr=str(s["cidr"]) if s.get("cidr") else None,
            publica=bool(s.get("publica")),
            zona=str(s["zona"]) if s.get("zona") else None,
        )
        for s in subredes
        if s.get("chave")
    ]


def _prefixo(s: SubredeDeOrigem) -> int:
    try:
        return ipaddress.ip_network(s.cidr or "", strict=False).prefixlen
    except ValueError:
        return _PREFIXO_PADRAO


def _encaixar(
    rede: ipaddress.IPv4Network,
    prefixos: list[int],
    ocupadas: list[ipaddress.IPv4Network],
) -> list[ipaddress.IPv4Network] | None:
    """Primeiro bloco livre, em ordem de endereço, para cada prefixo pedido.

    Em inteiros e pulando direto para depois de cada conflito: iterar bloco a
    bloco uma `/16` em `/28` são 4.096 candidatos por subrede.
    """
    usadas = [(int(b.network_address), int(b.broadcast_address)) for b in ocupadas]
    inicio, fim_da_rede = int(rede.network_address), int(rede.broadcast_address)
    saida = []
    for prefixo in prefixos:
        tamanho = 1 << (_BITS - prefixo)
        pos = inicio
        achado = None
        while pos + tamanho - 1 <= fim_da_rede:
            conflito = next((u for u in usadas if u[0] <= pos + tamanho - 1 and pos <= u[1]), None)
            if conflito is None:
                achado = (pos, pos + tamanho - 1)
                break
            pos = -(-(conflito[1] + 1) // tamanho) * tamanho  # próximo alinhado
        if achado is None:
            return None
        usadas.append(achado)
        saida.append(ipaddress.IPv4Network((achado[0], prefixo)))
    return saida


def _zona(origem: SubredeDeOrigem) -> int | None:
    """Letra da zona de origem (`us-east-1b` → 1); outra letra vira `a`."""
    if not origem.zona:
        return None
    letra = origem.zona[-1].lower()
    return _LETRAS.index(letra) if letra in _LETRAS else 0


def alocar(cidr_da_rede: str, origens: Iterable[SubredeDeOrigem]) -> Alocacao:
    rede = ipaddress.IPv4Network(cidr_da_rede)  # o schema da requisição só aceita IPv4
    padrao = next(rede.subnets(new_prefix=_PREFIXO_PADRAO), rede)
    ordem = sorted(origens, key=lambda s: (_prefixo(s), s.chave))
    if not ordem:
        return Alocacao((), "nenhuma", ())
    metade = rede.prefixlen + 1
    tentativas: list[tuple[str, list[int]]] = [
        ("prefixo da origem", [min(max(_prefixo(s), metade), _MENOR_SUBREDE) for s in ordem]),
        ("todas /24", [_PREFIXO_PADRAO] * len(ordem)),
        *(
            (f"todas /{q}", [q] * len(ordem))
            for q in range(_PREFIXO_PADRAO + 1, _MENOR_SUBREDE + 1)
        ),
    ]
    for estrategia, prefixos in tentativas:
        if any(p < metade or p > _MENOR_SUBREDE for p in prefixos):
            continue
        blocos = _encaixar(rede, prefixos, [padrao])
        if blocos is not None:
            return Alocacao(_com_zonas(ordem, blocos), estrategia, ())
    return Alocacao(
        (),
        "subrede única",
        (
            f"As {len(ordem)} subredes de origem não cabem em {rede} junto da subrede padrão: "
            "tudo foi para a subrede padrão. Peça uma rede maior para espelhar a origem.",
        ),
    )


def _com_zonas(
    ordem: list[SubredeDeOrigem], blocos: list[ipaddress.IPv4Network]
) -> tuple[SubredeAlocada, ...]:
    """Zona herdada da origem; subrede regional (GCP, Azure, OCI) em rodízio."""
    saida = []
    rodizio = 0
    for origem, bloco in zip(ordem, blocos, strict=True):
        zona = _zona(origem)
        if zona is None:
            saida.append(SubredeAlocada(origem, str(bloco), rodizio % len(_LETRAS), False))
            rodizio += 1
        else:
            saida.append(SubredeAlocada(origem, str(bloco), zona, True))
    return tuple(saida)


def sobreposicoes(cidr_da_rede: str, cidrs_de_origem: Iterable[str]) -> list[str]:
    """CIDRs de origem que a rede pedida sobrepõe: a VPN não roteia entre os dois."""
    rede = ipaddress.ip_network(cidr_da_rede)
    saida = []
    for c in cidrs_de_origem:
        try:
            if rede.overlaps(ipaddress.ip_network(c, strict=False)):
                saida.append(c)
        except (ValueError, TypeError):
            continue
    return sorted(set(saida))
