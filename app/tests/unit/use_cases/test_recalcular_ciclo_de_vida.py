"""Recálculo do ciclo de vida dos preços (PLANO_CICLO_DE_VIDA_DO_PRECO, P3.1/P3.2)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.repositories.i_price_lifecycle_repo import (
    ChaveDoPreco,
    IPriceLifecycleRepository,
    NovaObservacao,
    PontoDoCiclo,
)
from app.domain.services.ciclo_de_vida_do_preco import (
    Avaliacao,
    Estado,
    Fonte,
    Observacao,
    PesoDaFonte,
    RegraDoCiclo,
)
from app.use_cases.catalog.recalcular_ciclo_de_vida import (
    RecalcularCicloDeVidaInput,
    RecalcularCicloDeVidaUseCase,
)

AGORA = datetime(2026, 10, 16, tzinfo=UTC)
CHAVE: ChaveDoPreco = ("aws", "m7g.large", "us-east-1")


def _ponto(preco: str, dias: int, confianca: str = "official") -> PontoDoCiclo:
    return PontoDoCiclo(
        chave=CHAVE,
        service_type="virtual_server",
        service_name="m7g.large",
        preco=Decimal(preco),
        moeda="USD",
        unidade="instance_hour",
        confianca=confianca,
        capturado_em=AGORA - timedelta(days=dias),
    )


class _Repo(IPriceLifecycleRepository):
    def __init__(
        self,
        pontos: list[PontoDoCiclo],
        observacoes: dict[ChaveDoPreco, list[Observacao]] | None = None,
        *,
        com_regras: bool = True,
    ) -> None:
        self._pontos = pontos
        self._observacoes = observacoes or {}
        self._com_regras = com_regras
        self.novas: list[NovaObservacao] = []
        self.avaliacoes: dict[ChaveDoPreco, Avaliacao] = {}

    async def regras(self) -> list[tuple[str, str | None, RegraDoCiclo]]:
        return [("*", None, RegraDoCiclo(meia_vida_dias=Decimal(90)))] if self._com_regras else []

    async def pesos(self) -> dict[str, PesoDaFonte]:
        return {
            Fonte.OFICIAL: PesoDaFonte(Decimal(1), confirma=True),
            Fonte.REFERENCIA: PesoDaFonte(Decimal("0.7"), confirma=True),
            Fonte.AGENTE: PesoDaFonte(Decimal(0), confirma=False),
        }

    async def pontos(self) -> list[PontoDoCiclo]:
        return self._pontos

    async def observacoes(self) -> dict[ChaveDoPreco, list[Observacao]]:
        return self._observacoes

    async def registrar(self, novas: Sequence[NovaObservacao]) -> int:
        self.novas += novas
        return len(novas)

    async def atualizar(self, avaliacoes: Sequence[tuple[ChaveDoPreco, Avaliacao]]) -> int:
        self.avaliacoes.update(avaliacoes)
        return len(avaliacoes)


async def _rodar(repo: _Repo) -> None:
    await RecalcularCicloDeVidaUseCase(repo).execute(RecalcularCicloDeVidaInput(agora=AGORA))


@pytest.mark.asyncio
async def test_primeira_vez_grava_a_observacao_do_valor_atual() -> None:
    repo = _Repo([_ponto("0.0816", 0)])
    await _rodar(repo)
    assert [(n.observacao.fonte, n.observacao.preco, n.troca) for n in repo.novas] == [
        (Fonte.OFICIAL, Decimal("0.0816"), False)
    ]
    assert repo.avaliacoes[CHAVE].estado is Estado.VIVO


@pytest.mark.asyncio
async def test_valor_igual_nao_grava_de_novo() -> None:
    anterior = Observacao(Fonte.OFICIAL, Decimal("0.0816"), AGORA - timedelta(days=30))
    repo = _Repo([_ponto("0.0816", 0)], {CHAVE: [anterior]})
    await _rodar(repo)
    assert repo.novas == []


@pytest.mark.asyncio
async def test_valor_novo_vira_historico_e_sobe_a_versao() -> None:
    anterior = Observacao(Fonte.OFICIAL, Decimal("0.0816"), AGORA - timedelta(days=30))
    repo = _Repo([_ponto("0.0790", 0)], {CHAVE: [anterior]})
    await _rodar(repo)
    assert [(n.observacao.preco, n.troca) for n in repo.novas] == [(Decimal("0.0790"), True)]


@pytest.mark.asyncio
async def test_preco_sem_sync_envelhece_ate_adubo() -> None:
    repo = _Repo([_ponto("0.0816", 200)])
    await _rodar(repo)
    assert repo.avaliacoes[CHAVE].estado is Estado.ADUBO


@pytest.mark.asyncio
async def test_observacao_do_agente_nao_revive_o_preco() -> None:
    agente = Observacao(Fonte.AGENTE, Decimal("0.0700"), AGORA)
    repo = _Repo([_ponto("0.0816", 200)], {CHAVE: [agente]})
    await _rodar(repo)
    assert repo.avaliacoes[CHAVE].estado is Estado.ADUBO
    # O agente não é valor de tabela: não conta como histórico do valor
    assert [n.observacao.fonte for n in repo.novas] == [Fonte.OFICIAL]


@pytest.mark.asyncio
async def test_referencia_conta_da_data_em_que_foi_conferida() -> None:
    repo = _Repo([_ponto("0.090", 83, confianca="reference")])
    await _rodar(repo)
    # 0,7 x 0,5^(83/90): conferida em 25/07, já em decomposição
    assert repo.avaliacoes[CHAVE].estado is Estado.EM_DECOMPOSICAO
    assert repo.avaliacoes[CHAVE].ultima_fonte == Fonte.REFERENCIA


@pytest.mark.asyncio
async def test_sem_regras_nao_faz_nada() -> None:
    repo = _Repo([_ponto("0.0816", 0)], com_regras=False)
    await _rodar(repo)
    assert repo.novas == []
    assert repo.avaliacoes == {}
