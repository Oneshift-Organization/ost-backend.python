"""O seed da metadata sincroniza um banco já semeado (lote v4, V4.4).

Antes ele pulava quando a tabela tinha qualquer linha: nome corrigido e nota nova
nunca chegavam ao develop nem à produção.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.infrastructure.database.models.cloud_services_metadata import (
    CloudServiceMetadataModel,
)
from app.infrastructure.database.seed_service_metadata import sincronizar_metadata
from app.infrastructure.database.session import get_sessionmaker

pytestmark = pytest.mark.asyncio(loop_scope="session")

_PROVEDOR = "zzmeta"


@pytest_asyncio.fixture(loop_scope="session")
async def sessao(_db_ready):
    sm = get_sessionmaker()
    async with sm() as s:
        yield s
        await s.execute(
            text("delete from cloud_services_metadata where provider = :p"), {"p": _PROVEDOR}
        )
        await s.commit()


def _linha(nome: str, metadata: dict, docs: str | None = None) -> CloudServiceMetadataModel:
    return CloudServiceMetadataModel(
        provider=_PROVEDOR,
        category="storage",
        service_name=nome,
        service_metadata=metadata,
        docs_costs=docs,
    )


async def test_renomeia_preserva_as_outras_colunas_e_atualiza_a_nota(sessao) -> None:
    sessao.add(_linha("Nome Antigo", {"vendor_lock_in_level": "high"}, docs="https://docs"))
    sessao.add(_linha("Duplicado Antigo", {}))
    sessao.add(_linha("Duplicado Novo", {}))
    await sessao.commit()
    linhas = [
        (_PROVEDOR, "storage", "Nome Novo", {"vendor_lock_in_level": "low"}),
        (_PROVEDOR, "storage", "Duplicado Novo", {"vendor_lock_in_level": "low"}),
        (_PROVEDOR, "compute", "Inédito", {"vendor_lock_in_level": "medium"}),
    ]
    renomeados = {
        (_PROVEDOR, "Nome Antigo"): "Nome Novo",
        # o nome novo já existe: a linha antiga sai, sem violar a chave única
        (_PROVEDOR, "Duplicado Antigo"): "Duplicado Novo",
    }

    assert await sincronizar_metadata(sessao, linhas, renomeados) == 1
    await sessao.commit()

    gravadas = {
        m.service_name: m
        for m in (
            await sessao.execute(
                select(CloudServiceMetadataModel).where(
                    CloudServiceMetadataModel.provider == _PROVEDOR
                )
            )
        ).scalars()
    }
    assert set(gravadas) == {"Nome Novo", "Duplicado Novo", "Inédito"}
    assert gravadas["Nome Novo"].docs_costs == "https://docs"
    assert gravadas["Nome Novo"].service_metadata == {"vendor_lock_in_level": "low"}
    assert gravadas["Duplicado Novo"].service_metadata == {"vendor_lock_in_level": "low"}
    # Rodar de novo não muda nada
    assert await sincronizar_metadata(sessao, linhas, renomeados) == 0
