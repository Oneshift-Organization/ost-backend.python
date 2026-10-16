"""Recalcula o ciclo de vida dos preços (PLANO_CICLO_DE_VIDA_DO_PRECO, P3.1 e P3.2).

Roda depois de cada sync e agendado (o decaimento anda com o relógio, mesmo sem
sync). Para cada ponto de preço:

- o próprio ponto é uma observação: `official` vale como fonte oficial, o resto
  como referência, na data de captura (a referência leva a data em que o valor foi
  conferido, não a do seed);
- se o valor do ponto é diferente da última observação de valor, grava uma
  observação nova: é o histórico, e todo valor atual fica rastreável até ela;
- estado e score saem das observações que confirmam. As do agente ficam gravadas,
  mas não entram: o agente observa e não confirma.

Não muda número de análise: o estado é rótulo ao lado do preço.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from app.domain.repositories.i_price_lifecycle_repo import (
    ChaveDoPreco,
    IPriceLifecycleRepository,
    NovaObservacao,
)
from app.domain.services.ciclo_de_vida_do_preco import (
    Avaliacao,
    Fonte,
    Observacao,
    avaliar,
    regra_para,
)

# Fontes que trazem o valor de tabela do preço; as outras (billing, agente) são
# observações sobre ele
_DE_VALOR = frozenset({Fonte.OFICIAL, Fonte.REFERENCIA})


@dataclass(frozen=True, slots=True)
class RecalcularCicloDeVidaInput:
    agora: datetime


@dataclass(frozen=True, slots=True)
class RecalcularCicloDeVidaResult:
    por_estado: dict[str, int]
    observacoes_novas: int


class RecalcularCicloDeVidaUseCase:
    def __init__(self, repo: IPriceLifecycleRepository) -> None:
        self._repo = repo

    async def execute(self, entrada: RecalcularCicloDeVidaInput) -> RecalcularCicloDeVidaResult:
        regras = await self._repo.regras()
        pesos = await self._repo.pesos()
        if not regras or not pesos:
            # Ambiente sem a migração das regras: nada a recalcular, nada a quebrar
            return RecalcularCicloDeVidaResult({}, 0)
        gravadas = await self._repo.observacoes()
        novas: list[NovaObservacao] = []
        avaliacoes: list[tuple[ChaveDoPreco, Avaliacao]] = []
        for ponto in await self._repo.pontos():
            fonte = Fonte.OFICIAL if ponto.confianca == "official" else Fonte.REFERENCIA
            propria = Observacao(fonte=fonte, preco=ponto.preco, observado_em=ponto.capturado_em)
            do_ponto = gravadas.get(ponto.chave, [])
            de_valor = [o for o in do_ponto if o.fonte in _DE_VALOR]
            ultima = max(de_valor, key=lambda o: o.observado_em, default=None)
            if ultima is None or ultima.preco != ponto.preco:
                novas.append(
                    NovaObservacao(
                        ponto.chave, propria, ponto.moeda, ponto.unidade, troca=ultima is not None
                    )
                )
            regra = regra_para(regras, ponto.service_type, ponto.service_name)
            avaliacoes.append(
                (ponto.chave, avaliar([propria, *do_ponto], regra, pesos, entrada.agora))
            )
        await self._repo.registrar(novas)
        await self._repo.atualizar(avaliacoes)
        return RecalcularCicloDeVidaResult(
            por_estado=dict(Counter(a.estado.value for _, a in avaliacoes)),
            observacoes_novas=len(novas),
        )
