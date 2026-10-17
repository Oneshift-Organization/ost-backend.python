"""Uma análise por billing na lista: a vigente e a reanálise em curso.

Refazer a análise (anexar inventário, reanalisar) cria um job novo. Antes, a lista
mostrava os dois, e o usuário via duas análises do mesmo billing, sem saber qual
valia. Agora o job novo é uma revisão: a lista mostra uma linha por billing.

- Vigente: a revisão concluída mais recente. Sem nenhuma concluída, a mais recente.
- Reanálise: a tentativa mais nova que a vigente, se ainda não concluiu (rodando ou
  falhou). Falhar não tira a vigente da tela: a anterior continua valendo.
- As revisões antigas ficam no banco, fora da lista (o link direto continua abrindo).

Módulo puro, sem I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.analysis_job import AnalysisStatus


@dataclass(frozen=True, slots=True)
class Revisao:
    job_id: UUID
    report_id: UUID
    # Texto, não o enum: um valor antigo no banco não pode derrubar a lista
    status: str
    criada_em: datetime
    ativa: bool = True


@dataclass(frozen=True, slots=True)
class AnaliseDoBilling:
    vigente: Revisao
    # Tentativa mais nova que a vigente que não concluiu: rodando ou falhou
    reanalise: Revisao | None
    # Para ordenar a lista pela atividade mais recente do billing
    ultima_atividade: datetime


def analises_por_billing(
    revisoes: Iterable[Revisao], *, incluir_depreciadas: bool = False
) -> list[AnaliseDoBilling]:
    """Uma entrada por billing, da atividade mais recente para a mais antiga."""
    por_billing: dict[UUID, list[Revisao]] = defaultdict(list)
    for revisao in revisoes:
        por_billing[revisao.report_id].append(revisao)

    saida: list[AnaliseDoBilling] = []
    for grupo in por_billing.values():
        candidatas = [r for r in grupo if r.ativa]
        if not candidatas:
            if not incluir_depreciadas:
                continue
            candidatas = grupo
        candidatas.sort(key=lambda r: r.criada_em, reverse=True)
        concluidas = [r for r in candidatas if r.status == AnalysisStatus.COMPLETED]
        vigente = concluidas[0] if concluidas else candidatas[0]
        mais_nova = candidatas[0]
        reanalise = mais_nova if mais_nova is not vigente else None
        saida.append(AnaliseDoBilling(vigente, reanalise, mais_nova.criada_em))
    saida.sort(key=lambda a: a.ultima_atividade, reverse=True)
    return saida
