"""Subredes espelhadas e repartição das VMs (RI15.2 e RI15.5), por propriedade."""

from __future__ import annotations

import ipaddress
import random

import pytest

from app.domain.services.terraform_topologia.posicionamento import Fatia, repartir
from app.domain.services.terraform_topologia.rede import (
    SubredeDeOrigem,
    alocar,
    sobreposicoes,
)

_BARRA_24 = 24
_MENOR_SUBREDE = 28  # o menor bloco que as quatro nuvens aceitam


def _origem(i: int, prefixo: int, zona: str | None = None, publica: bool = False):
    return SubredeDeOrigem(
        chave=f"subrede-{i:03d}",
        nome=f"s{i}",
        cidr=f"172.16.{i}.0/{prefixo}" if prefixo >= _BARRA_24 else f"172.{16 + i}.0.0/{prefixo}",
        publica=publica,
        zona=zona,
    )


@pytest.mark.parametrize("semente", range(40))
def test_alocacao_fica_dentro_da_rede_sem_sobrepor(semente: int) -> None:
    sorteio = random.Random(semente)
    rede = f"10.{sorteio.randint(0, 200)}.0.0/{sorteio.choice([16, 18, 20, 22, 23])}"
    origens = [
        _origem(
            i,
            sorteio.choice([16, 20, 22, 24, 26, 28, 29]),
            sorteio.choice([None, "x-1a", "x-1b", "x-1d"]),
        )
        for i in range(sorteio.randint(1, 40))
    ]
    a = alocar(rede, origens)
    rede_ip = ipaddress.ip_network(rede)
    padrao = next(rede_ip.subnets(new_prefix=24))
    blocos = [ipaddress.ip_network(s.cidr) for s in a.subredes]
    assert all(b.subnet_of(rede_ip) for b in blocos)
    assert not any(b.overlaps(padrao) for b in blocos)
    for i, b in enumerate(blocos):
        assert not any(b.overlaps(c) for c in blocos[i + 1 :]), (b, blocos)
        assert b.prefixlen <= _MENOR_SUBREDE
    # Ou espelhou todas, ou nenhuma (subrede única, com aviso).
    assert len(blocos) in (0, len(origens))
    assert bool(a.avisos) is (not blocos)
    # A mesma entrada em outra ordem dá o mesmo resultado.
    embaralhadas = list(origens)
    sorteio.shuffle(embaralhadas)
    assert alocar(rede, embaralhadas) == a


def test_estrategias_em_ordem() -> None:
    oito = [_origem(i, 20, f"us-east-1{'abc'[i % 3]}") for i in range(8)]
    assert alocar("10.50.0.0/16", oito).estrategia == "prefixo da origem"
    assert alocar("10.50.0.0/22", oito).estrategia == "todas /26"
    unica = alocar("10.50.0.0/24", oito)
    assert (unica.estrategia, unica.subredes) == ("subrede única", ())
    # A subrede de origem nunca passa de metade da rede: uma /16 numa /17 vira /18.
    [s] = alocar("10.50.0.0/17", [_origem(0, 16)]).subredes
    assert s.cidr == "10.50.64.0/18"


def test_zona_herdada_e_rodizio_para_subrede_regional() -> None:
    origens = [
        _origem(0, 24, "us-east-1b"),
        _origem(1, 24, "us-east-1d"),  # letra fora de a, b, c vira a
        _origem(2, 24),
        _origem(3, 24),
        _origem(4, 24),
        _origem(5, 24),
    ]
    zonas = {
        s.origem.chave: (s.zona, s.zona_da_origem) for s in alocar("10.0.0.0/16", origens).subredes
    }
    assert zonas["subrede-000"] == (1, True)
    assert zonas["subrede-001"] == (0, True)
    assert [zonas[f"subrede-{i:03d}"][0] for i in (2, 3, 4, 5)] == [0, 1, 2, 0]


def test_sobreposicao_com_a_origem() -> None:
    assert sobreposicoes("10.10.0.0/16", ["10.10.4.0/24", "172.16.0.0/12", "lixo"]) == [
        "10.10.4.0/24"
    ]


# ── repartição ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("semente", range(40))
def test_repartir_soma_o_total_e_da_ao_menos_uma_quando_cabe(semente: int) -> None:
    sorteio = random.Random(semente)
    fatias = [
        Fatia(f"s{i}", sorteio.choice([0.0, 1.0, 2.0, 8.0, 64.0]), sorteio.randint(1, 20))
        for i in range(sorteio.randint(1, 12))
    ]
    total = sorteio.randint(0, 40)
    saida = repartir(total, fatias)
    assert sum(f.vms for f in saida) == total
    if total >= len(fatias):
        assert all(f.vms >= 1 for f in saida)
    else:
        assert all(f.vms in (0, 1) for f in saida)
    assert repartir(total, list(reversed(fatias))) == list(reversed(saida))


def test_repartir_pelo_maior_resto() -> None:
    fatias = [Fatia("a", 154, 20), Fatia("b", 124, 16), Fatia("c", 88, 12), Fatia("d", 60, 7)]
    # Uma para cada; as 15 restantes: cotas 5,42 / 4,37 / 3,10 / 2,11; a sobra vai para a.
    assert [f.vms for f in repartir(19, fatias)] == [7, 5, 4, 3]
    # Menos VMs que subredes: as maiores recebem uma.
    assert [f.vms for f in repartir(2, fatias)] == [1, 1, 0, 0]
