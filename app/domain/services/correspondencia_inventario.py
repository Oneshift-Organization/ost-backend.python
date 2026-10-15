"""O inventário é da mesma conta da fatura? (PLANO_ESCOPO_DE_MIGRACAO, E4)

Inventário de outra conta não quebra nada na topologia (ela é só do inventário),
mas tudo que cruza os dois sai errado: aplicações com custo zero, serviços da
fatura que "não existem" no plano, cobertura de 0%. Melhor dizer isso de uma vez
do que mostrar números vazios.

Cinco situações:

- ``ok``: o inventário cobre o custo da fatura (a correlação de sempre);
- ``parcial``: cobre só parte; as lacunas por conta e região dizem o que falta;
- ``estimado``: a fatura não traz o ID de cada recurso, mas traz o tipo de uso, e
  o custo foi ligado aos recursos do inventário pelo tipo (atribuicao_por_tipo);
- ``sem_id_de_recurso``: a fatura não traz o ID e nada pôde ser ligado pelo tipo.
  As aplicações e o que migra junto vêm só do inventário, sem custo;
- ``sem_correspondencia``: a fatura tem ID de recurso e nenhum está no
  inventário: é de outra conta (ou de outra época). A análise recusa.

Função pura, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.entities.topologia import Inventario
from app.domain.services.correlacao_inventario import (
    COBERTURA_MINIMA,
    Correlacao,
    CustosDoBilling,
)

OK = "ok"
PARCIAL = "parcial"
ESTIMADO = "estimado"
SEM_ID_DE_RECURSO = "sem_id_de_recurso"
SEM_CORRESPONDENCIA = "sem_correspondencia"

_MAX_CONTAS = 5
_FATURA_DETALHADA = (
    'a fatura detalhada com o ID de cada recurso (na AWS, o CUR 2.0 com "Include resource '
    'IDs"; no GCP, o export detalhado do BigQuery; na Azure, o Cost Details; na OCI, o '
    "relatório de custos)"
)
# Onde "conta" quer dizer o mesmo na fatura e no inventário: o número da conta da AWS e a
# subscription da Azure. No GCP a fatura traz a conta de faturamento e o inventário o projeto;
# na OCI a fatura traz o tenant. Comparar contas lá daria "outra conta" para tudo.
_CONTA_COMPARAVEL = frozenset({"aws", "azure"})


@dataclass(frozen=True, slots=True)
class Correspondencia:
    status: str
    fracao_encontrada: Decimal | None
    custo_com_recurso: Decimal
    custo_encontrado: Decimal
    contas_da_fatura: tuple[str, ...]
    contas_do_inventario: tuple[str, ...]
    # Contas da fatura com custo e sem nenhum recurso no inventário
    contas_sem_inventario: tuple[str, ...]
    mensagem: str
    # Parte do gasto ligada aos recursos pelo tipo, não pelo ID
    custo_estimado: Decimal = Decimal(0)
    fracao_estimada: Decimal | None = None

    @property
    def liga_custo(self) -> bool:
        """Dá para dizer quanto cada aplicação custa e planejar por serviço."""
        return self.status in (OK, PARCIAL, ESTIMADO)


def _lista(contas: tuple[str, ...]) -> str:
    if not contas:
        return "nenhuma identificada"
    if len(contas) > _MAX_CONTAS:
        return f"{', '.join(contas[:_MAX_CONTAS])} e mais {len(contas) - _MAX_CONTAS}"
    return ", ".join(contas)


def avaliar_correspondencia(
    inventario: Inventario, custos: CustosDoBilling, correlacao: Correlacao
) -> Correspondencia:
    contas_fatura = tuple(sorted({x.conta for x in custos.por_recurso if x.conta}))
    contas_inventario = tuple(sorted({r.conta for r in inventario.recursos if r.conta}))
    comparavel = inventario.provedor.lower() in _CONTA_COMPARAVEL
    # Só onde "conta" é a mesma coisa dos dois lados; no GCP sairia a conta de faturamento
    sem_inventario = (
        tuple(
            sorted(
                {lac.conta for lac in correlacao.lacunas_por_escopo if lac.conta}
                - set(contas_inventario)
            )
        )
        if comparavel
        else ()
    )

    def com(status: str, mensagem: str) -> Correspondencia:
        return Correspondencia(
            status=status,
            fracao_encontrada=correlacao.fracao_encontrada,
            custo_com_recurso=correlacao.custo_com_recurso,
            custo_encontrado=correlacao.custo_encontrado,
            contas_da_fatura=contas_fatura,
            contas_do_inventario=contas_inventario,
            contas_sem_inventario=sem_inventario,
            mensagem=mensagem,
            custo_estimado=correlacao.custo_estimado,
            fracao_estimada=(
                correlacao.custo_estimado / custos.custo_da_base
                if custos.custo_da_base > 0
                else None
            ),
        )

    if correlacao.custo_com_recurso <= 0:
        if correlacao.custo_estimado > 0:
            parte = (
                f" ({correlacao.custo_estimado / custos.custo_da_base:.0%} do gasto)"
                if custos.custo_da_base > 0
                else ""
            )
            return com(
                ESTIMADO,
                (
                    "A fatura não traz o ID de cada recurso. O custo de cada aplicação foi "
                    "estimado pelo tipo do recurso: a máquina pelo tipo, o banco pela classe, o "
                    f"cluster, o NAT e o balanceador{parte}. Para o custo medido de cada recurso, "
                    f"envie {_FATURA_DETALHADA}."
                ),
            )
        return com(
            SEM_ID_DE_RECURSO,
            (
                "A fatura não traz o ID de cada recurso, e nenhuma linha dela pôde ser ligada a um "
                "recurso do inventário pelo tipo. As aplicações e o que migra junto vêm do "
                "inventário, sem o custo de cada uma. Para o custo por aplicação, envie "
                f"{_FATURA_DETALHADA}."
            ),
        )

    em_comum = set(contas_fatura) & set(contas_inventario)
    contas_distintas = (
        comparavel and bool(contas_fatura) and bool(contas_inventario) and not em_comum
    )
    # Contas sem nenhuma em comum: o que casou foi coincidência de nome (bucket, banco com o
    # mesmo identificador em contas diferentes), não o mesmo recurso
    if (
        correlacao.recursos_encontrados == 0 and correlacao.custo_estimado <= 0
    ) or contas_distintas:
        mensagem = (
            f"Nenhum recurso da fatura está no inventário. A fatura é das contas "
            f"{_lista(contas_fatura)}; o inventário, das contas {_lista(contas_inventario)}. "
            "Envie o inventário das mesmas contas da fatura."
            if not em_comum
            else "Nenhum recurso da fatura está no inventário, embora as contas coincidam: o "
            "inventário pode ser de outra época ou de outras regiões. Exporte de novo, do mesmo "
            "período da fatura."
        )
        return com(SEM_CORRESPONDENCIA, mensagem)

    fracao = correlacao.fracao_encontrada or Decimal(0)
    # Máquina trocada pelo autoscaling: o ID não está no inventário, o tipo dela está
    pelo_tipo = (
        f" {correlacao.custo_estimado_com_id / correlacao.custo_com_recurso:.0%} dele pelo tipo da "
        "máquina ou do disco, de recursos que já não existem no inventário (trocados pelo "
        "autoscaling)."
        if correlacao.custo_estimado_com_id > 0
        else ""
    )
    if fracao < COBERTURA_MINIMA:
        faltam = f" Faltam as contas {_lista(sem_inventario)}." if sem_inventario else ""
        return com(
            PARCIAL,
            (
                f"O inventário cobre {fracao:.0%} do custo com recurso identificado: as aplicações "
                f"e o plano mostram só essa parte.{faltam}{pelo_tipo}"
            ),
        )
    return com(OK, f"O inventário cobre {fracao:.0%} do custo com recurso identificado.{pelo_tipo}")
