"""Confiança da análise derivada de insumo medido, não de impressão.

O problema que isto resolve: `confianca_preco: low` já ia no payload e ninguém
fazia nada com ele. O nível de confiança da análise vinha do LLM — ou seja, era
opinião de um modelo sobre a qualidade dos dados que ele mesmo recebeu.

Aqui vira conta. A pergunta é objetiva: **que fração do dinheiro cotado veio de
preço oficial?** No bill AWS de hoje isso dá **0,21** — $47k de $229k. Nenhuma
narrativa pode declarar alta confiança sobre isso, e agora não declara.

O número é pior do que parecia à primeira vista: eu estimara 0,58 olhando só os
pools de compute, e as categorias não-compute (storage, egress, database) somam
$147k a mais, todas em `reference`. Foi a própria métrica que revelou isso.

Função pura, sem I/O: entra número, sai nível.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Preço vindo da API oficial do provedor. Qualquer outro valor (catálogo seed,
# estimativa por razão de categoria) não conta como confiável para este cálculo.
CONFIANCA_OFICIAL = "official"

# Rótulos que aparecem em produção, do mais para o menos confiável:
#   official   preço da API do provedor
#   reference  taxa publicada pelo provedor (seed_category_rates), honestamente
#              rotulada como não-oficial; o category_rate_client faz upgrade
#              para `official` quando consegue buscar da API
#   low        catálogo seed (cloud_sku_catalog) — aproximação
# Só `official` conta como confiável. `reference` é melhor que `low`, mas não é
# preço coletado — tratá-lo como confiável inflaria a métrica exatamente onde
# ela precisa ser conservadora.

# Limiares da razão de custo confiável. Escolhidos para que "high" signifique
# "quase tudo é preço oficial" — não "a maior parte". Uma análise com 1/3 do
# dinheiro em preço seed não é de alta confiança, mesmo sendo majoritariamente
# oficial.
_LIMIAR_ALTA = Decimal("0.90")
_LIMIAR_MEDIA = Decimal("0.60")

_ORDEM = ("high", "medium", "low")


@dataclass(frozen=True, slots=True)
class ConfiancaAnalise:
    """Nível + os números que o produziram.

    A razão vai junto de propósito: um nível sozinho é tão opaco quanto a
    opinião que ele substitui. Com a razão, quem lê pode discordar do limiar em
    vez de discordar do resultado.
    """

    nivel: str
    razao_confiavel: float
    custo_confiavel: float
    custo_total: float
    por_rotulo: dict[str, float]

    def as_ledger(self) -> dict[str, object]:
        """Serializa para o bloco ``premissas`` do payload.

        `por_rotulo` vai junto porque a razão sozinha engana para o lado
        pessimista. No bill AWS ela dá 0,21, mas dois terços do que não é
        `official` é `reference` — taxa publicada pelo provedor, bem melhor que
        catálogo seed. Sem a quebra, quem lê conclui que quase tudo é chute.
        """
        return {
            "nivel": self.nivel,
            "razao_custo_confiavel": round(self.razao_confiavel, 4),
            "custo_com_preco_oficial": round(self.custo_confiavel, 2),
            "custo_total_cotado": round(self.custo_total, 2),
            "custo_por_rotulo": {k: round(v, 2) for k, v in sorted(self.por_rotulo.items())},
        }


def _nivel_por_razao(razao: Decimal) -> str:
    if razao >= _LIMIAR_ALTA:
        return "high"
    if razao >= _LIMIAR_MEDIA:
        return "medium"
    return "low"


def rebaixar(nivel: str, degraus: int = 1) -> str:
    """Desce ``degraus`` na escala. `low` é o piso.

    Existe para a contestação do agente (plano v2 §4.2): quando ele sinaliza um
    preço implausível com severidade alta, o backend **não troca o número** —
    trocaria reprodutibilidade por palpite — mas rebaixa a confiança do
    resultado. É assim que a percepção do modelo entra sem contaminar o cálculo.
    """
    if degraus <= 0:
        return nivel if nivel in _ORDEM else "low"
    try:
        atual = _ORDEM.index(nivel)
    except ValueError:
        return "low"
    return _ORDEM[min(atual + degraus, len(_ORDEM) - 1)]


def avaliar_confianca(
    custos_por_confianca: dict[str, Decimal],
) -> ConfiancaAnalise:
    """Calcula a confiança a partir do custo cotado agrupado por confiança.

    ``custos_por_confianca`` mapeia o rótulo de confiança do preço para o valor
    monetário cotado sob ele — por exemplo
    ``{"official": Decimal("47259"), "low": Decimal("34143")}``.

    Sem nenhum custo cotado, o resultado é `low`: não sabemos nada, e não saber
    nada nunca é alta confiança. Devolver `high` para um conjunto vazio seria o
    tipo de bug que passa despercebido porque o caminho feliz nunca o exercita.
    """
    total = sum(custos_por_confianca.values(), Decimal("0"))
    confiavel = sum(
        (v for k, v in custos_por_confianca.items() if _e_oficial(k)),
        Decimal("0"),
    )

    por_rotulo = {k: float(v) for k, v in custos_por_confianca.items()}

    if total <= 0:
        return ConfiancaAnalise(
            nivel="low",
            razao_confiavel=0.0,
            custo_confiavel=0.0,
            custo_total=0.0,
            por_rotulo=por_rotulo,
        )

    razao = confiavel / total
    return ConfiancaAnalise(
        nivel=_nivel_por_razao(razao),
        razao_confiavel=float(razao),
        custo_confiavel=float(confiavel),
        custo_total=float(total),
        por_rotulo=por_rotulo,
    )


def _e_oficial(rotulo: str) -> bool:
    """`high (mock)` existe no matcher e **não** é preço oficial.

    O rótulo engana: tem "high" no nome, mas descreve confiança do *matching*
    técnico com preço de mock. Comparar por prefixo aqui contaria preço
    inventado como confiável — exatamente o erro que este módulo existe para
    evitar.
    """
    return rotulo.strip().lower() == CONFIANCA_OFICIAL
