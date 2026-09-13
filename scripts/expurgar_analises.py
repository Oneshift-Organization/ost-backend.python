#!/usr/bin/env python3
"""Apaga análises depreciadas há mais de um mês.

A segunda metade do `PUT /v1/analyses/{job_id}/disp-status`: o front marca como
depreciada, ela some da listagem, e só depois da carência sai do banco. Isso dá
ao usuário um mês para perceber que excluiu a errada.

Roda offline (cron), **nunca** no caminho da requisição. Sem `--confirmar` só
mostra o que faria — apagar é irreversível e leva o `llm_input_payload`, que é a
fonte do Terraform e de qualquer reanálise.

Uso:
    python scripts/expurgar_analises.py               # simulação
    python scripts/expurgar_analises.py --confirmar
    python scripts/expurgar_analises.py --dias 90 --confirmar
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from app.domain.entities.analysis_job import DispStatus, agora_utc
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.session import get_sessionmaker
from sqlalchemy import delete, select

_DIAS_PADRAO = 30


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=_DIAS_PADRAO)
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="sem isto, só lista o que seria apagado",
    )
    args = parser.parse_args()

    limite = agora_utc() - timedelta(days=args.dias)
    sm = get_sessionmaker()

    async with sm() as session:
        # `disp_status_updated_at` nulo significa que a análise nunca foi
        # depreciada por esta rota — não entra, mesmo que `disp_status` seja 0,
        # porque não há data de quando a carência começou.
        condicoes = (
            AnalysisJobModel.disp_status == DispStatus.DEPRECIADA,
            AnalysisJobModel.disp_status_updated_at.isnot(None),
            AnalysisJobModel.disp_status_updated_at < limite,
        )
        alvos = (
            await session.execute(
                select(
                    AnalysisJobModel.job_id,
                    AnalysisJobModel.report_id,
                    AnalysisJobModel.disp_status_updated_at,
                ).where(*condicoes)
            )
        ).all()

        if not alvos:
            print(f"nada a expurgar (depreciadas antes de {limite:%Y-%m-%d})")
            return

        for job_id, report_id, quando in alvos:
            print(f"  {job_id}  report={report_id}  depreciada em {quando:%Y-%m-%d}")

        if not args.confirmar:
            print(f"\n{len(alvos)} análises seriam apagadas. Use --confirmar.")
            return

        await session.execute(delete(AnalysisJobModel).where(*condicoes))
        await session.commit()
        print(f"\n{len(alvos)} análises apagadas.")


if __name__ == "__main__":
    asyncio.run(main())
