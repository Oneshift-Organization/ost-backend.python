"""Correlação de um inventário com um billing do mesmo provedor (RF7)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.entities.billing_report import JobStatus
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_custo_por_recurso import ILeitorDeCustoPorRecurso
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.atribuicao_por_tipo import atribuir_por_tipo
from app.domain.services.correlacao_inventario import Correlacao, correlacionar
from app.use_cases.inventory.consultas import exigir_processado, obter_do_usuario


class BillingNaoEncontradoError(NotFoundError):
    pass


class BillingNaoProntoError(ConflictError):
    pass


class ProvedorDiferenteError(ValidationError):
    """Inventário de uma nuvem, billing de outra: nada casaria, e o número de
    cobertura (zero) seria lido como "falta inventário"."""


class CorrelacionarInventarioUseCase:
    def __init__(
        self,
        inventarios: IInventoryRepository,
        billing: IBillingReportRepository,
        custos: ILeitorDeCustoPorRecurso,
    ) -> None:
        self._inventarios = inventarios
        self._billing = billing
        self._custos = custos

    async def execute(self, inventario_id: UUID, report_id: UUID, user_id: UUID) -> Correlacao:
        inventario = await obter_do_usuario(self._inventarios, inventario_id, user_id)
        exigir_processado(inventario)
        report = await self._billing.get_by_id(report_id, user_id)
        if report is None:
            raise BillingNaoEncontradoError(f"billing {report_id} não existe para este usuário")
        if report.status is not JobStatus.DONE:
            raise BillingNaoProntoError(f"o billing está em {report.status.value}; aguarde done")
        provedor_billing = report.provider.value if report.provider else None
        if provedor_billing and provedor_billing != inventario.provedor:
            raise ProvedorDiferenteError(
                f"inventário de {inventario.provedor}, billing de {provedor_billing}"
            )
        conteudo = await self._inventarios.carregar_conteudo(inventario)
        custos = atribuir_por_tipo(
            conteudo, await self._custos.custos_por_recurso(report_id, user_id)
        )
        return correlacionar(conteudo, custos, captura_estimada=inventario.capturado_em_estimado)


def _dinheiro(valor: Any) -> float:
    return float(round(valor, 2))


def serializar_correlacao(c: Correlacao) -> dict[str, Any]:
    def lacuna(x: Any) -> dict[str, Any]:
        return {
            "conta": x.conta,
            "regiao": x.regiao,
            "servico": x.servico,
            "recursos": x.recursos,
            "custo": _dinheiro(x.custo),
            "exemplos": list(x.exemplos),
        }

    a = c.alinhamento
    return {
        "moeda": c.moeda,
        "custo_da_base": _dinheiro(c.custo_da_base),
        "custo_com_recurso": _dinheiro(c.custo_com_recurso),
        "custo_encontrado": _dinheiro(c.custo_encontrado),
        "custo_estimado": _dinheiro(c.custo_estimado),
        "fracao_encontrada": (
            float(round(c.fracao_encontrada, 4)) if c.fracao_encontrada is not None else None
        ),
        "recursos_do_billing": c.recursos_do_billing,
        "recursos_encontrados": c.recursos_encontrados,
        "ambiguos": c.ambiguos,
        "lacunas_por_escopo": [lacuna(x) for x in c.lacunas_por_escopo],
        "lacunas_por_servico": [lacuna(x) for x in c.lacunas_por_servico],
        "sem_cobranca": dict(c.sem_cobranca),
        "ociosos": [
            {"recurso": o.recurso, "motivo": o.motivo, "custo": _dinheiro(o.custo)}
            for o in c.ociosos
        ],
        "alinhamento": {
            "capturado_em": a.capturado_em.isoformat() if a.capturado_em else None,
            "periodo_inicio": a.periodo_inicio.isoformat() if a.periodo_inicio else None,
            "periodo_fim": a.periodo_fim.isoformat() if a.periodo_fim else None,
            "desalinhado": a.desalinhado,
            "dias_fora": a.dias_fora,
            "estimado": a.estimado,
        },
        "riscos": [
            {
                "regra": r.regra,
                "severidade": r.severidade.value,
                "titulo": r.titulo,
                "detalhe": r.detalhe,
                "total": r.total,
            }
            for r in c.riscos
        ],
    }
