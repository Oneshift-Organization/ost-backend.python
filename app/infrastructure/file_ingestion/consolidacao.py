"""Consolidação de linhas de export detalhado (RN6 do PLANO_CUR_BIGQUERY).

Um CUR horário tem de 10⁵ a 10⁷ linhas. O pipeline usa o mês: custo por recurso
e por tipo de uso, horas somadas. Guardar a linha horária custaria tempo,
memória e JSONB sem mudar nenhum número da análise.

A consolidação é **incremental** — o parser alimenta linha a linha, bloco a
bloco — então a memória fica limitada ao número de grupos, não ao de linhas.

Genérica: recebe linhas no formato canônico, sem saber de provedor. Cada parser
detalhado (CUR, BigQuery, Azure, OCI) traduz para esse formato e chama isto.

Exceção declarada: o ``raw_payload`` da linha consolidada guarda a CHAVE do
grupo, a identidade da primeira linha e a contagem — não uma linha original.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

# Campos que definem o grupo. Duas linhas com a mesma chave são a mesma coisa
# sendo cobrada em momentos diferentes; qualquer diferença aqui mantém as linhas
# separadas (operação distingue Windows de Linux; base de custo distingue
# linha amortizada de linha que ficou na base faturada por falta de coluna).
CHAVE: tuple[str, ...] = (
    "billing_period",
    "account_id",
    "region",
    "resource_id",
    "service",
    "usage_type",
    "operation",
    "resource_type",
    "charge_kind",
    "usage_unit",
    "currency",
    "cost_basis",
)

_ZERO = Decimal("0")


def _somar(atual: Decimal | None, valor: Decimal | None) -> Decimal | None:
    if valor is None:
        return atual
    return valor if atual is None else atual + valor


@dataclass
class _Grupo:
    primeira: dict[str, Any]
    cost: Decimal = _ZERO
    cost_billed: Decimal = _ZERO
    cost_discounted: Decimal = _ZERO
    usage_amount: Decimal | None = None
    on_demand: Decimal | None = None
    inicio: datetime | None = None
    fim: datetime | None = None
    dias: set[str] = field(default_factory=set)
    linhas: int = 0
    tipos: set[str] = field(default_factory=set)
    estimativa: bool = False
    correcao: bool = False
    tags: dict[str, str] = field(default_factory=dict)
    somas: dict[str, Decimal] = field(default_factory=dict)


class Consolidador:
    """Acumula linhas canônicas e entrega o DataFrame no schema do parser.

    Linha canônica (dict) que o parser entrega:

    - chave: os campos de ``CHAVE`` (``None`` é valor válido);
    - ``cost``, ``cost_billed``, ``cost_discounted``: ``Decimal``;
    - ``usage_amount``, ``on_demand``: ``Decimal | None``;
    - ``usage_start``, ``usage_end``: ``datetime | None``;
    - ``account_name``, ``service_raw``, ``is_estimate``, ``is_correction``;
    - ``tags``: dict; ``attributes``: dict de fatos medidos estáticos
      (vcpus, instance_type…) — vale o da primeira linha do grupo;
    - ``identity``: identidade da linha para deduplicar (``None`` = sem);
    - ``raw``: dict pequeno para rastrear a origem;
    - ``line_type``: tipo de linha original do provedor, para auditoria;
    - ``somas``: dict de ``Decimal`` que o provedor quer somado no grupo e
      exposto em ``attributes`` (crédito de CUD, custo antes dos créditos…).
      Genérico de propósito: o consolidador não sabe o que cada soma significa.
    """

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._grupos: dict[tuple, _Grupo] = {}
        self._vistas: set[bytes] = set()
        self.duplicadas = 0

    def adicionar(self, linha: dict[str, Any]) -> None:
        identidade = linha.get("identity")
        if identidade:
            # 16 bytes por linha vista: com 10⁶ linhas, ~50 MB em vez de guardar
            # o texto inteiro da identidade.
            digest = hashlib.blake2b(str(identidade).encode(), digest_size=16).digest()
            if digest in self._vistas:
                self.duplicadas += 1
                return
            self._vistas.add(digest)

        chave = tuple(linha.get(c) for c in CHAVE)
        grupo = self._grupos.get(chave)
        if grupo is None:
            grupo = _Grupo(primeira=linha)
            self._grupos[chave] = grupo

        grupo.cost += linha["cost"]
        grupo.cost_billed += linha.get("cost_billed") or _ZERO
        grupo.cost_discounted += linha.get("cost_discounted", linha["cost"])
        grupo.usage_amount = _somar(grupo.usage_amount, linha.get("usage_amount"))
        grupo.on_demand = _somar(grupo.on_demand, linha.get("on_demand"))
        for nome, valor in (linha.get("somas") or {}).items():
            if valor is not None:
                grupo.somas[nome] = grupo.somas.get(nome, _ZERO) + valor
        grupo.linhas += 1
        if linha.get("line_type"):
            grupo.tipos.add(linha["line_type"])
        grupo.estimativa = grupo.estimativa or bool(linha.get("is_estimate"))
        grupo.correcao = grupo.correcao or bool(linha.get("is_correction"))
        if not grupo.tags and linha.get("tags"):
            grupo.tags = linha["tags"]

        inicio, fim = linha.get("usage_start"), linha.get("usage_end")
        if inicio is not None:
            grupo.inicio = inicio if grupo.inicio is None else min(grupo.inicio, inicio)
            quantidade = linha.get("usage_amount")
            if quantidade is None or quantidade > 0:
                grupo.dias.add(inicio.date().isoformat())
        if fim is not None:
            grupo.fim = fim if grupo.fim is None else max(grupo.fim, fim)

    def __len__(self) -> int:
        return len(self._grupos)

    def dataframe(
        self,
        *,
        user_id: str,
        job_id: str,
        schema_version: str,
        source_file: str,
    ) -> pd.DataFrame:
        registros = [
            self._registro(
                chave,
                g,
                user_id=user_id,
                job_id=job_id,
                schema_version=schema_version,
                source_file=source_file,
            )
            for chave, g in self._grupos.items()
        ]
        return pd.DataFrame(registros)

    def _registro(
        self,
        chave: tuple,
        g: _Grupo,
        *,
        user_id: str,
        job_id: str,
        schema_version: str,
        source_file: str,
    ) -> dict[str, Any]:
        base = g.primeira
        chave_dict = dict(zip(CHAVE, chave, strict=True))
        atributos = dict(base.get("attributes") or {})
        atributos.update(
            {
                "source_lines": g.linhas,
                "active_days": len(g.dias),
                **({"first_usage": g.inicio.isoformat()} if g.inicio else {}),
                **({"last_usage": g.fim.isoformat()} if g.fim else {}),
                **({"on_demand_cost": str(g.on_demand)} if g.on_demand is not None else {}),
                **{nome: str(valor) for nome, valor in g.somas.items()},
            }
        )
        if chave_dict["cost_basis"]:
            atributos["cost_basis"] = chave_dict["cost_basis"]
        # Operação distingue o mesmo tipo de uso em motores/SOs diferentes
        # (`RunInstances:0002` = Windows; `CreateDBInstance:0014` = PostgreSQL).
        if chave_dict["operation"]:
            atributos["operation"] = chave_dict["operation"]
        raw = dict(base.get("raw") or {})
        raw.update(
            {
                "consolidacao": {k: v for k, v in chave_dict.items() if v is not None},
                "linhas_origem": g.linhas,
                **({"tipos_de_linha": sorted(g.tipos)} if g.tipos else {}),
            }
        )
        return {
            "user_id": user_id,
            "job_id": job_id,
            "provider": self._provider,
            "schema_version": schema_version,
            "account_id": chave_dict["account_id"] or "",
            "account_name": base.get("account_name"),
            "service": chave_dict["service"] or "unknown",
            "service_raw": base.get("service_raw") or chave_dict["service"] or "unknown",
            "resource_id": chave_dict["resource_id"],
            "resource_type": chave_dict["resource_type"],
            "region": chave_dict["region"],
            "usage_start": g.inicio,
            "usage_end": g.fim,
            "billing_period": chave_dict["billing_period"] or "0000-00",
            "cost": g.cost,
            "cost_discounted": g.cost_discounted,
            "currency": chave_dict["currency"] or "USD",
            "usage_amount": g.usage_amount,
            "usage_unit": chave_dict["usage_unit"],
            "tags": g.tags,
            "is_correction": g.correcao,
            "is_estimate": g.estimativa,
            "raw_payload": raw,
            "source_file": source_file,
            "line_hash": hashlib.sha256(
                json.dumps(chave_dict, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "usage_type": chave_dict["usage_type"],
            "charge_kind": chave_dict["charge_kind"],
            "cost_billed": g.cost_billed,
            "attributes": atributos,
        }
