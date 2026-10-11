"""A varredura de jobs órfãos precisa rodar mais de uma vez.

Em dev uma análise ficou "processando" por horas. O deploy trocou o pod no meio
da execução; o pod novo já tinha subido e rodado a varredura do startup quando o
job ainda era recente, e nada mais verificava depois disso.
"""

from __future__ import annotations

import asyncio

import pytest

from app import main


@pytest.mark.asyncio
async def test_varredura_se_repete_enquanto_o_processo_vive(monkeypatch) -> None:
    passadas = 0

    async def uma_passada() -> None:
        nonlocal passadas
        passadas += 1

    async def sem_espera(_segundos: float) -> None:
        if passadas >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "_varrer_jobs_travados", uma_passada)
    monkeypatch.setattr(main.asyncio, "sleep", sem_espera)

    with pytest.raises(asyncio.CancelledError):
        await main._varredura_periodica()

    assert passadas == 3


@pytest.mark.asyncio
async def test_falha_num_reaper_nao_impede_o_outro_nem_levanta(monkeypatch) -> None:
    """Se a varredura levantasse, o laço periódico morreria em silêncio."""
    chamadas: list[str] = []

    async def analises_falham() -> int:
        chamadas.append("analises")
        raise RuntimeError("banco fora")

    async def geracoes_ok() -> int:
        chamadas.append("geracoes")
        return 0

    monkeypatch.setattr(main, "_expirar_jobs_travados", analises_falham)
    monkeypatch.setattr(main, "_expirar_geracoes_travadas", geracoes_ok)

    await main._varrer_jobs_travados()

    assert chamadas == ["analises", "geracoes"]
