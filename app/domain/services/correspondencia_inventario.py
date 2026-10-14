"""O inventário é da mesma conta da fatura? (PLANO_ESCOPO_DE_MIGRACAO, E4)

Inventário de outra conta não quebra nada na topologia (ela é só do inventário),
mas tudo que cruza os dois sai errado: aplicações com custo zero, serviços da
fatura que "não existem" no plano, cobertura de 0%. Melhor dizer isso de uma vez
do que mostrar números vazios.

Quatro situações:

- ``ok``: o inventário cobre o custo da fatura (a correlação de sempre);
- ``parcial``: cobre só parte; as lacunas por conta e região dizem o que falta;
- ``sem_id_de_recurso``: a fatura é resumida (Cost Explorer, Cost Table) e não
  traz o ID de cada recurso: nada liga recurso a serviço, de nenhuma conta. As
  aplicações e o que migra junto vêm só do inventário, sem custo;
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
SEM_ID_DE_RECURSO = "sem_id_de_recurso"
SEM_CORRESPONDENCIA = "sem_correspondencia"

_MAX_CONTAS = 5
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

    @property
    def liga_custo(self) -> bool:
        """Dá para dizer quanto cada aplicação custa e planejar por serviço."""
        return self.status in (OK, PARCIAL)


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
        )

    if correlacao.recursos_do_billing == 0 or correlacao.custo_com_recurso <= 0:
        return com(
            SEM_ID_DE_RECURSO,
            (
                "A fatura enviada não traz o ID de cada recurso (fatura resumida, como a do Cost "
                "Explorer): as aplicações e o que migra junto vêm só do inventário, sem o custo de "
                "cada uma, e não dá para planejar por serviço. Para isso, envie a fatura detalhada "
                "(CUR, BigQuery, Cost Details ou o relatório de custos da OCI)."
            ),
        )

    em_comum = set(contas_fatura) & set(contas_inventario)
    contas_distintas = (
        comparavel and bool(contas_fatura) and bool(contas_inventario) and not em_comum
    )
    # Contas sem nenhuma em comum: o que casou foi coincidência de nome (bucket, banco com o
    # mesmo identificador em contas diferentes), não o mesmo recurso
    if correlacao.recursos_encontrados == 0 or contas_distintas:
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
    if fracao < COBERTURA_MINIMA:
        faltam = f" Faltam as contas {_lista(sem_inventario)}." if sem_inventario else ""
        return com(
            PARCIAL,
            (
                f"O inventário cobre {fracao:.0%} do custo com recurso identificado: as aplicações "
                f"e o plano mostram só essa parte.{faltam}"
            ),
        )
    return com(OK, f"O inventário cobre {fracao:.0%} do custo com recurso identificado.")
