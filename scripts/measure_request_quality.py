#!/usr/bin/env python3
"""Mede a QUALIDADE DE CONTEÚDO do request enviado ao agente (Onda 6).

Não mede precisão numérica (isso é a reconciliação) — mede se a IA recebe
informação suficiente e correta para produzir um relatório de migração:

  1. Cobertura de DE-PARA **por custo** (não por contagem: RDS a $27k sem
     equivalente pesa mais que um serviço de $1).
  2. Serviços sem equivalente, ordenados por custo.
  3. Mapeamentos SUSPEITOS: service_type de origem != service_type do alvo
     (ex.: object_storage -> block_storage), que produzem conselho errado.
  4. Regressão: cobertura de análise + reconciliação.

Uso:
    python scripts/measure_request_quality.py                 # imprime
    python scripts/measure_request_quality.py --json out.json # armazena
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.pg_exchange_rate_repo import (
    PgExchangeRateRepository,
)
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)
from app.use_cases.analysis.run_analysis_pipeline import build_pipeline_llm_input_payload
from sqlalchemy import text

USER = UUID("00000000-0000-0000-0000-000000000001")

# Famílias de service_type que NÃO são intercambiáveis. Um mapeamento que cruza
# essas fronteiras é conselho errado (S3 object_storage -> Persistent Disk block).
_INCOMPATIBLE = (
    ("object_storage", "block_storage"),
    ("object_storage", "file_storage"),
    ("archive_cold_storage", "block_storage"),
    ("managed_relational_database", "virtual_server"),
    ("virtual_server", "object_storage"),
)


def _incompatible(src: str | None, tgt: str | None) -> bool:
    if not src or not tgt or src == tgt:
        return False
    return any(
        (src == a and tgt == b) or (src == b and tgt == a) for a, b in _INCOMPATIBLE
    )


def _squash(name: str) -> str:
    """Chave de comparação insensível a espaço/pontuação (AmazonRDS == Amazon RDS)."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


async def measure(report_id: UUID) -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        # Verdade curada: nome do serviço -> service_type oficial.
        catalog = {
            _squash(c.service_name): c
            for c in await PGServiceComparisonRepository(s).list_all()
        }
        uc = BuildMigrationContextUseCase(
            PgBillingReportRepository(s),
            PgBillingEnrichedRepository(s),
            PGServiceMetadataRepository(s),
            PGServiceComparisonRepository(s),
            sku_repo=PgCloudSkuRepository(s),
            fx_repo=PgExchangeRateRepository(s),
            price_repo=PgCloudPricePointRepository(s),
        )
        ctx = await uc.execute(
            BuildMigrationContextInput(user_id=USER, report_id=report_id)
        )

    services = [(c, sv) for c in ctx.categories for sv in c.services]
    total = sum((sv.total_cost for _, sv in services), Decimal("0"))

    with_eq = [(c, sv) for c, sv in services if sv.equivalents]
    cost_with_eq = sum((sv.total_cost for _, sv in with_eq), Decimal("0"))

    sem_equivalente = sorted(
        ({"servico": sv.service_name, "custo": round(float(sv.total_cost), 2)}
         for _, sv in services if not sv.equivalents),
        key=lambda d: -d["custo"],
    )

    suspeitos = []
    for _, sv in with_eq:
        for e in sv.equivalents:
            if _incompatible(sv.service_type, e.service_type):
                suspeitos.append({
                    "servico": sv.service_name,
                    "custo": round(float(sv.total_cost), 2),
                    "tipo_origem": sv.service_type,
                    "alvo": f"{e.provider}:{e.service_name}",
                    "tipo_alvo": e.service_type,
                })
                break

    # Divergência vs catálogo curado: o serviço EXISTE na base curada mas o
    # service_type usado difere → o casamento por nome falhou e o tipo veio do
    # classificador de SKU (que erra). Vai a zero quando a normalização casar.
    divergentes = []
    for _, sv in services:
        cat = catalog.get(_squash(sv.service_name))
        if cat is None:
            continue
        if sv.service_type != cat.service_type:
            divergentes.append({
                "servico": sv.service_name,
                "custo": round(float(sv.total_cost), 2),
                "tipo_usado": sv.service_type,
                "tipo_catalogo": cat.service_type,
            })
    divergentes.sort(key=lambda d: -d["custo"])
    custo_divergente = sum(Decimal(str(x["custo"])) for x in divergentes)

    custo_suspeito = sum(Decimal(str(x["custo"])) for x in suspeitos)
    payload, _ = build_pipeline_llm_input_payload(ctx)
    prem = ctx.premissas

    return {
        "provedor": ctx.source_provider,
        "periodo": ctx.billing_period,
        "gasto_total": round(float(ctx.total_spend), 2),
        "servicos_total": len(services),
        "servicos_com_depara": len(with_eq),
        "depara_cobertura_custo": round(float(cost_with_eq / total), 4) if total else 0.0,
        "sem_equivalente_top": sem_equivalente[:8],
        "mapeamentos_suspeitos": suspeitos,
        "custo_em_mapeamento_suspeito": round(float(custo_suspeito), 2),
        "pct_custo_suspeito": round(float(custo_suspeito / total), 4) if total else 0.0,
        "divergentes_vs_catalogo": divergentes[:8],
        "custo_divergente": round(float(custo_divergente), 2),
        "pct_custo_divergente": round(float(custo_divergente / total), 4) if total else 0.0,
        # regressão
        "cobertura_analise": prem.get("cobertura_analise"),
        "cobertura_classificacao": prem.get("cobertura_classificacao"),
        "reconciliacao": prem.get("reconciliacao", {}).get("resumo"),
        "conservacao_bate": prem.get("reconciliacao", {})
        .get("conservacao_custo", {})
        .get("bate"),
        "payload_tokens": len(json.dumps(payload, ensure_ascii=False)) // 4,
        "payload_top_keys": sorted(payload.keys()),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="grava o resultado neste arquivo")
    args = ap.parse_args()

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "select distinct on (provider) id, provider from billing_reports "
                    "where status='done' order by provider, created_at desc"
                )
            )
        ).all()

    out = {}
    for rid, provider in rows:
        out[provider] = await measure(rid)

    for provider, m in out.items():
        print("=" * 68)
        print(f"{provider.upper()}  {m['periodo']}  ${m['gasto_total']:,.2f}")
        print("=" * 68)
        print(f"  DE-PARA por custo ....... {m['depara_cobertura_custo']:.1%}"
              f"   ({m['servicos_com_depara']}/{m['servicos_total']} serviços)")
        print(f"  custo em mapa SUSPEITO .. {m['pct_custo_suspeito']:.1%}"
              f"   (${m['custo_em_mapeamento_suspeito']:,.2f})")
        print(f"  DIVERGENTE vs catálogo .. {m['pct_custo_divergente']:.1%}"
              f"   (${m['custo_divergente']:,.2f})")
        print(f"  cobertura de análise .... {(m['cobertura_analise'] or 0):.1%}")
        print(f"  reconciliação ........... {m['reconciliacao']}  conserva={m['conservacao_bate']}")
        print(f"  payload ................. ~{m['payload_tokens']} tokens")
        if m["sem_equivalente_top"]:
            print("  --- sem equivalente (top custo) ---")
            for x in m["sem_equivalente_top"][:5]:
                print(f"      {x['servico']:<46} ${x['custo']:>12,.2f}")
        if m["divergentes_vs_catalogo"]:
            print("  --- tipo DIVERGENTE do catálogo curado (casamento falhou) ---")
            for x in m["divergentes_vs_catalogo"][:5]:
                print(f"      {x['servico']:<26} usado={x['tipo_usado']!s:<16}"
                      f" catálogo={x['tipo_catalogo']:<20} ${x['custo']:,.2f}")
        if m["mapeamentos_suspeitos"]:
            print("  --- mapeamentos SUSPEITOS ---")
            for x in m["mapeamentos_suspeitos"][:5]:
                print(f"      {x['servico']:<26} {x['tipo_origem']} -> "
                      f"{x['tipo_alvo']} ({x['alvo']})  ${x['custo']:,.2f}")
        print()

    if args.json:
        Path(args.json).write_text(  # noqa: ASYNC240 - script CLI, não servidor
            json.dumps(out, indent=2, ensure_ascii=False)
        )
        print(f"resultado armazenado em {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
