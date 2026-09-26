"""Consultas sobre o inventário que todas as regras usam (RI4).

A mais importante é ``membros``: quem um grupo de segurança protege de fato.
Na AWS o grupo protege a interface; na Azure, a NIC ou a subrede inteira; no
GCP, a VM pela tag. Aqui tudo vira "recursos de papel unidade", que é o que as
dependências e os riscos comparam.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from functools import cached_property

from app.domain.entities.topologia import (
    UNIDADE,
    Inventario,
    Recurso,
    TipoDeRecurso,
    TipoDeRelacao,
)

_Rede = ipaddress.IPv4Network | ipaddress.IPv6Network
_Endereco = ipaddress.IPv4Address | ipaddress.IPv6Address


class Indice:
    def __init__(self, inventario: Inventario) -> None:
        self.inventario = inventario
        self.recursos: dict[str, Recurso] = {r.chave: r for r in inventario.recursos}
        self._saida: dict[tuple[str, TipoDeRelacao], list[str]] = defaultdict(list)
        self._entrada: dict[tuple[str, TipoDeRelacao], list[str]] = defaultdict(list)
        for rel in inventario.relacoes:
            self._saida[(rel.de, rel.tipo)].append(rel.para)
            self._entrada[(rel.para, rel.tipo)].append(rel.de)
        # Listas ordenadas: nenhuma consulta pode depender da ordem do arquivo.
        for lista in (*self._saida.values(), *self._entrada.values()):
            lista.sort()

    def para(self, chave: str, tipo: TipoDeRelacao) -> list[str]:
        """``chave --tipo--> ?``"""
        return self._saida.get((chave, tipo), [])

    def de(self, chave: str, tipo: TipoDeRelacao) -> list[str]:
        """``? --tipo--> chave``"""
        return self._entrada.get((chave, tipo), [])

    def tipo(self, chave: str) -> TipoDeRecurso | None:
        r = self.recursos.get(chave)
        return r.tipo if r else None

    # ── anexos ──────────────────────────────────────────────────────────

    def donos(self, chave: str) -> list[str]:
        """Recurso de papel unidade a que um anexo pertence (NIC → VM)."""
        return [d for d in self.para(chave, TipoDeRelacao.ANEXADO_A) if self.tipo(d) in UNIDADE]

    def _unidades_de(self, chave: str) -> set[str]:
        """O recurso, se for de papel unidade; senão o dono dele."""
        t = self.tipo(chave)
        if t in UNIDADE:
            return {chave}
        if t is TipoDeRecurso.INTERFACE_REDE:
            return set(self.donos(chave))
        return set()

    def interfaces(self, chave: str) -> list[str]:
        return [
            i
            for i in self.de(chave, TipoDeRelacao.ANEXADO_A)
            if self.tipo(i) is TipoDeRecurso.INTERFACE_REDE
        ]

    # ── posicionamento ──────────────────────────────────────────────────

    def subredes_de(self, chave: str) -> set[str]:
        """Subredes do recurso: diretas e pela interface."""
        diretas = {
            s
            for s in self.para(chave, TipoDeRelacao.CONTIDO_EM)
            if self.tipo(s) is TipoDeRecurso.SUBREDE
        }
        for i in self.interfaces(chave):
            diretas |= {
                s
                for s in self.para(i, TipoDeRelacao.CONTIDO_EM)
                if self.tipo(s) is TipoDeRecurso.SUBREDE
            }
        return diretas

    @cached_property
    def recursos_por_subrede(self) -> dict[str, frozenset[str]]:
        """Subrede → recursos de papel unidade dentro dela (direto ou pela NIC)."""
        mapa: dict[str, set[str]] = defaultdict(set)
        for chave, r in self.recursos.items():
            if r.tipo in UNIDADE:
                for s in self.subredes_de(chave):
                    mapa[s].add(chave)
        return {s: frozenset(m) for s, m in mapa.items()}

    @cached_property
    def redes_das_subredes(self) -> dict[str, _Rede]:
        redes: dict[str, _Rede] = {}
        for chave, r in self.recursos.items():
            if r.tipo is TipoDeRecurso.SUBREDE:
                rede = _rede(r.atributos.get("cidr"))
                if rede is not None:
                    redes[chave] = rede
        return redes

    def ips(self, chave: str) -> set[_Endereco]:
        """IPs privados do recurso e das interfaces dele."""
        valores = (
            [self.recursos[chave].atributos.get("ip_privado")] if chave in self.recursos else []
        )
        valores += [self.recursos[i].atributos.get("ip_privado") for i in self.interfaces(chave)]
        ips: set[_Endereco] = set()
        for v in valores:
            try:
                if v:
                    ips.add(ipaddress.ip_address(str(v)))
            except ValueError:
                continue
        return ips

    # ── grupos ──────────────────────────────────────────────────────────

    @cached_property
    def _membros(self) -> dict[str, frozenset[str]]:
        membros: dict[str, set[str]] = defaultdict(set)
        for chave, r in self.recursos.items():
            if r.tipo is not TipoDeRecurso.GRUPO_SEGURANCA:
                continue
            for protegido in self.de(chave, TipoDeRelacao.PROTEGIDO_POR):
                if self.tipo(protegido) is TipoDeRecurso.SUBREDE:
                    membros[chave] |= self.recursos_por_subrede.get(protegido, frozenset())
                else:
                    membros[chave] |= self._unidades_de(protegido)
        return {g: frozenset(m) for g, m in membros.items()}

    def membros(self, grupo: str) -> frozenset[str]:
        """Recursos de papel unidade que o grupo protege de fato (RI4)."""
        return self._membros.get(grupo, frozenset())

    @cached_property
    def grupos_de(self) -> dict[str, frozenset[str]]:
        """Recurso de papel unidade → grupos que o protegem."""
        inverso: dict[str, set[str]] = defaultdict(set)
        for grupo, membros in self._membros.items():
            for m in membros:
                inverso[m].add(grupo)
        return {r: frozenset(g) for r, g in inverso.items()}


def _rede(valor: object) -> _Rede | None:
    try:
        return ipaddress.ip_network(str(valor), strict=False) if valor else None
    except ValueError:
        return None


def rede(valor: object) -> _Rede | None:
    return _rede(valor)
