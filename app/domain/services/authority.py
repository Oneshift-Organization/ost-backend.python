"""Autoridade por **tipo de afirmação**, não por lista de campos.

## Por que não basta a lista fixa

`use_cases/analysis/backend_authority.py` sobrescreve três campos nomeados.
Funciona, mas tem dois defeitos: campo novo no schema nasce sem dono (alguém
precisa lembrar de atualizar a lista), e o critério "quem manda neste campo" fica
implícito no código em vez de declarado.

Pior: a lista trata igual coisas epistemicamente diferentes. Custo calculado e
preço de catálogo aparecem lado a lado, e nós tratávamos os dois como verdade do
backend. Mas 79% do dinheiro cotado no bill AWS vem de preço não-oficial —
sobrescrever o número do agente com um número nosso mais velho é trocar ruído
por viés.

## As três categorias

**DERIVAÇÃO** — segue necessariamente da entrada. Soma, delta, percentual,
agregação. Existe uma resposta certa e ela é reprodutível.
→ O backend sobrescreve **sempre**. O agente nunca faz conta.

**ESTIMATIVA** — depende do mundo externo e envelhece. Preço de SKU, câmbio.
Existe resposta certa, mas talvez não a tenhamos.
→ O backend mantém o valor (precisa ser reprodutível) e **anexa proveniência**.
O agente contesta via `sugestoes`, e a contestação rebaixa a confiança sem
trocar o número.

**JULGAMENTO** — não há fato da questão. Risco, complexidade, sequenciamento.
→ Passa **intocado**. O backend nunca finge que sabe: um número inventado por
nós é pior que um do agente, porque tem cara de medida.

E uma quarta, que é o caso interessante:

**SEM_BASE** — o schema pede, ninguém consegue calcular. `payback_period_months`
exige custo de migração, que não modelamos.
→ Não proibir: **exigir a premissa e marcar o tipo**. Um número declarado como
estimativa, com a premissa exposta, é honesto. O mesmo número num campo
indistinguível dos calculados é mentira por omissão.

Função pura, sem I/O. Ninguém a chama ainda — o legado segue com
`backend_authority` inalterado (ver `tests/integration/test_legado_caracterizacao.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Categoria(StrEnum):
    DERIVACAO = "derivacao"
    ESTIMATIVA = "estimativa"
    JULGAMENTO = "julgamento"
    SEM_BASE = "sem_base"


# Classificação por caminho de campo. `*` casa qualquer índice de lista.
#
# O caminho é usado em vez do nome simples porque o mesmo nome muda de dono
# conforme o contexto: `confidence_level` dentro de `provider_comparison` é
# derivado da cobertura (backend), mas dentro de `agent_views.finops` é o quanto
# o modelo confia na própria leitura (julgamento).
_CLASSIFICACAO: dict[str, Categoria] = {
    # ── derivação: aritmética sobre o que já temos ──────────────────────
    "recommended_provider": Categoria.DERIVACAO,
    "provider_comparison.*.provider": Categoria.DERIVACAO,
    "provider_comparison.*.estimated_monthly_cost": Categoria.DERIVACAO,
    "provider_comparison.*.estimated_monthly_savings": Categoria.DERIVACAO,
    "provider_comparison.*.confidence_level": Categoria.DERIVACAO,
    "savings_summary.total_monthly_savings_usd": Categoria.DERIVACAO,
    "savings_summary.percentage_reduction": Categoria.DERIVACAO,
    "savings_summary.confidence_level": Categoria.DERIVACAO,
    "cross_cloud_equivalents.*.workload": Categoria.DERIVACAO,
    "cross_cloud_equivalents.*.source_provider": Categoria.DERIVACAO,
    "cross_cloud_equivalents.*.target_provider": Categoria.DERIVACAO,
    "cross_cloud_equivalents.*.target_service": Categoria.DERIVACAO,
    "cross_cloud_equivalents.*.cost_delta": Categoria.DERIVACAO,
    "viability_score": Categoria.DERIVACAO,
    "analysis_confidence": Categoria.DERIVACAO,
    # ── estimativa: preço, que envelhece ────────────────────────────────
    "cross_cloud_equivalents.*.estimated_monthly_cost": Categoria.ESTIMATIVA,
    # ── julgamento: o trabalho do agente ────────────────────────────────
    "recommendation": Categoria.JULGAMENTO,
    "recommendation_rationale": Categoria.JULGAMENTO,
    "strategic_notes": Categoria.JULGAMENTO,
    "action_plan": Categoria.JULGAMENTO,
    "flags": Categoria.JULGAMENTO,
    "agent_views": Categoria.JULGAMENTO,
    "provider_comparison.*.strengths": Categoria.JULGAMENTO,
    "provider_comparison.*.weaknesses": Categoria.JULGAMENTO,
    "provider_comparison.*.migration_complexity": Categoria.JULGAMENTO,
    "provider_comparison.*.portability": Categoria.JULGAMENTO,
    "cross_cloud_equivalents.*.migration_risk": Categoria.JULGAMENTO,
    "cross_cloud_equivalents.*.requires_refactoring": Categoria.JULGAMENTO,
    "cross_cloud_equivalents.*.vendor_lock_in_level": Categoria.JULGAMENTO,
    "cross_cloud_equivalents.*.equivalence_type": Categoria.JULGAMENTO,
    # ── derivação a partir de premissa declarada ────────────────────────
    # O agente declara o custo de migração; o backend divide pela economia
    # (`agent_result._derivar_payback`, PLANO_PRECO_REGIONAL D11).
    "payback.meses": Categoria.DERIVACAO,
    # ── sem base: exige premissa declarada ──────────────────────────────
    # Sem a premissa do agente, continua sem base e sai nulo.
    "savings_summary.payback_period_months": Categoria.SEM_BASE,
    "oneshift_scores.technical_score": Categoria.SEM_BASE,
    "oneshift_scores.enterprise_score": Categoria.SEM_BASE,
    "provider_comparison.*.technical_score": Categoria.SEM_BASE,
    "provider_comparison.*.enterprise_score": Categoria.SEM_BASE,
}

# Default para caminho não mapeado. JULGAMENTO de propósito: campo novo que
# ninguém classificou é texto do agente até prova em contrário. O contrário —
# assumir DERIVACAO — faria o backend sobrescrever com `None` algo que ele não
# sabe calcular, apagando conteúdo em silêncio.
_PADRAO = Categoria.JULGAMENTO


def classificar(caminho: str) -> Categoria:
    """Categoria de um caminho de campo. Índices viram `*`."""
    return _CLASSIFICACAO.get(_normalizar(caminho), _PADRAO)


def _normalizar(caminho: str) -> str:
    """`provider_comparison.0.provider` → `provider_comparison.*.provider`."""
    return ".".join("*" if p.isdigit() else p for p in caminho.split("."))


@dataclass(frozen=True, slots=True)
class Substituicao:
    """Registro de uma sobrescrita, para auditoria.

    Guardar o valor do agente importa: sem ele não há como medir o quanto o
    modelo erra, nem justificar depois por que um número mudou na tela.
    """

    caminho: str
    valor_agente: Any
    valor_backend: Any


@dataclass(frozen=True, slots=True)
class ResultadoAutoridade:
    resposta: dict[str, Any]
    substituicoes: tuple[Substituicao, ...]

    def as_ledger(self) -> dict[str, object]:
        return {
            "campos_sobrescritos": len(self.substituicoes),
            "detalhe": [
                {
                    "campo": s.caminho,
                    "agente": s.valor_agente,
                    "backend": s.valor_backend,
                }
                for s in self.substituicoes
            ],
        }


def aplicar_autoridade(
    resposta_agente: Mapping[str, Any],
    verdade_backend: Mapping[str, Any],
) -> ResultadoAutoridade:
    """Sobrescreve o que é derivação; preserva o resto.

    ``verdade_backend`` é um mapa **plano** de caminho → valor calculado, por
    exemplo ``{"recommended_provider": "oci",
    "provider_comparison.0.estimated_monthly_cost": 2394.08}``.

    Só caminhos presentes em `verdade_backend` **e** classificados como
    `DERIVACAO` são sobrescritos. Estimativa, julgamento e sem-base passam
    intactos — mesmo que o backend tenha um valor para eles.

    A assimetria é o ponto: ter um número não autoriza a impor. Para preço, o
    nosso pode estar mais velho que o conhecimento do agente; para julgamento,
    não temos o que dizer.
    """
    saida: dict[str, Any] = _copia_profunda(resposta_agente)
    feitas: list[Substituicao] = []

    for caminho, valor_backend in verdade_backend.items():
        if classificar(caminho) is not Categoria.DERIVACAO:
            continue
        atual = _ler(saida, caminho)
        if atual == valor_backend:
            continue  # já bate: não é substituição, é concordância
        if _escrever(saida, caminho, valor_backend):
            feitas.append(Substituicao(caminho, atual, valor_backend))

    return ResultadoAutoridade(resposta=saida, substituicoes=tuple(feitas))


# ── acesso por caminho ───────────────────────────────────────────────────


def _copia_profunda(valor: Any) -> Any:
    if isinstance(valor, Mapping):
        return {k: _copia_profunda(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_copia_profunda(v) for v in valor]
    return valor


_AUSENTE = object()


def _descer(atual: Any, parte: str) -> Any:
    """Um passo na navegação. `_AUSENTE` quando o caminho não existe.

    Sentinela em vez de `None` porque `None` é valor legítimo no payload — um
    campo que existe e vale nulo não pode ser confundido com campo inexistente.
    """
    if isinstance(atual, list):
        indice = int(parte) if parte.isdigit() else -1
        return atual[indice] if 0 <= indice < len(atual) else _AUSENTE
    if isinstance(atual, Mapping):
        return atual.get(parte, _AUSENTE)
    return _AUSENTE


def _ler(raiz: Any, caminho: str) -> Any:
    atual: Any = raiz
    for parte in caminho.split("."):
        atual = _descer(atual, parte)
        if atual is _AUSENTE:
            return None
    return atual


def _escrever(raiz: Any, caminho: str, valor: Any) -> bool:
    """Escreve no caminho. Devolve False se ele não existe.

    **Não cria** estrutura ausente de propósito: se o agente não mandou
    `provider_comparison[3]`, inventar o índice produziria um item órfão, sem
    a narrativa que o acompanha. Campo que o backend quer impor e o agente não
    produziu é assunto de emissão (Bloco C do plano), não de autoridade.
    """
    *meio, ultimo = caminho.split(".")

    alvo: Any = raiz
    for parte in meio:
        alvo = _descer(alvo, parte)
        if alvo is _AUSENTE:
            return False

    if _descer(alvo, ultimo) is _AUSENTE:
        return False

    if isinstance(alvo, list):
        alvo[int(ultimo)] = valor
        return True
    if isinstance(alvo, dict):
        alvo[ultimo] = valor
        return True
    return False
