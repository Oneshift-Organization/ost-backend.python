"""Carrega cloud_services_mapping.csv na tabela cloud_services_comparation.

Uso:
    python -m app.infrastructure.database.seed_from_csv

Substitui todos os dados existentes (TRUNCATE + INSERT).
"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from sqlalchemy import delete, select

from app.infrastructure.database.models.cloud_services_comparation import (
    CloudServiceComparisonModel,
)
from app.infrastructure.database.session import get_sessionmaker

_CSV_PATH = Path(__file__).resolve().parent / "models" / "cloud_services_mapping.csv"


async def seed() -> None:
    if not _CSV_PATH.exists():
        print(f"CSV não encontrado: {_CSV_PATH}")
        return

    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("CSV vazio.")
        return

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        existing = await session.execute(
            select(CloudServiceComparisonModel).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            await session.execute(delete(CloudServiceComparisonModel))
            await session.flush()

        for r in rows:
            session.add(
                CloudServiceComparisonModel(
                    category=r["category"],
                    service_type=r["service_type"],
                    provider=r["provider"],
                    service_name=r["service_name"],
                    doc_origin=r.get("doc_origin"),
                )
            )
        await session.commit()

    print(f"Importados {len(rows)} registros de cloud_services_mapping.csv")


if __name__ == "__main__":
    asyncio.run(seed())
