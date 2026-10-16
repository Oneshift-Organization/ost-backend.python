"""Nível de lock-in da origem pela melhor evidência disponível (lote v4: V4.4 e V4.5).

O catálogo dá uma nota por serviço ("RDS: médio"), seja Postgres ou Aurora. O
inventário mostra o que o cliente usa dentro dele. A classe (o tipo de serviço) só
serve de hipótese quando não há nem um nem outro. Regras decididas em 14/10 e 16/10:

1. **Catálogo e inventário se combinam.** A exclusividade do inventário (motor do
   banco, tipo nativo: `topologia/exclusividade.py`) sobe o nível livremente, porque
   achar um recurso proprietário é prova. Descer, ou afirmar "baixo" sem catálogo,
   exige que o inventário tenha visto quase todo o custo do serviço: não achar o
   recurso exclusivo só prova alguma coisa com cobertura alta.
2. **"Migra junto" não entra.** Dependência entre serviços (banco compartilhado,
   grupo grande) não é dependência da nuvem: tem fator próprio no score.
3. **A classe só vale sem catálogo e sem inventário**, e só quando os serviços
   curados do mesmo tipo concordam (`migration_expectation.learn_class_defaults`).
   É hipótese: sai provisória.
4. **Sem nada, desconhecido.** Ausência nunca vira lock-in baixo.

Inventário ligado ao custo pelo tipo do recurso, sem ID, é estimativa: pode subir o
nível, mas sai provisório, e não serve para descer.

Função pura, sem I/O e sem nome de nuvem.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

ORDEM = {"low": 0, "medium": 1, "high": 2}

# Fração do custo do serviço que o inventário precisa ter visto para que a
# ausência de recurso exclusivo valha como prova (regra 3 de 14/10).
COBERTURA_PARA_DESCER = Decimal("0.9")

CATALOGO = "catalogo"
INVENTARIO = "inventario"
CLASSE = "classe"


@dataclass(frozen=True, slots=True)
class LockInEfetivo:
    nivel: str | None  # low | medium | high; None = desconhecido
    fonte: str | None  # catalogo | inventario | classe
    provisorio: bool  # hipótese da classe ou inventário estimado
    motivo: str


def _nivel(valor: object) -> str | None:
    texto = str(valor).strip().lower() if valor else ""
    return texto if texto in ORDEM else None


def _fracao(valor: object) -> Decimal:
    if isinstance(valor, bool):
        return Decimal(0)
    try:
        fracao = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)
    return fracao if fracao.is_finite() and 0 <= fracao <= 1 else Decimal(0)


def lock_in_efetivo(
    catalogo: object,
    *,
    exclusividade: Mapping[str, Any] | None = None,
    cobertura: object = None,
    inventario_estimado: bool = False,
    classe: object = None,
) -> LockInEfetivo:
    """O nível da origem com a fonte declarada.

    `exclusividade` é a do inventário (`{nivel, motivo, ...}`) e `cobertura` a fração do
    custo do serviço com recurso achado nele (`topologia.detalhe.servicos[nome]`).
    """
    nivel_catalogo = _nivel(catalogo)
    if exclusividade is not None:
        nivel_inventario = _nivel(exclusividade.get("nivel"))
        prova_de_ausencia = _fracao(cobertura) >= COBERTURA_PARA_DESCER and not inventario_estimado
        if nivel_inventario is not None and _inventario_vale(
            nivel_catalogo, nivel_inventario, prova_de_ausencia=prova_de_ausencia
        ):
            return LockInEfetivo(
                nivel=nivel_inventario,
                fonte=INVENTARIO,
                provisorio=inventario_estimado,
                motivo=str(exclusividade.get("motivo") or "Recurso visto no inventário."),
            )
    if nivel_catalogo is not None:
        return LockInEfetivo(nivel_catalogo, CATALOGO, False, "Nota curada do serviço.")
    nivel_classe = _nivel(classe)
    if nivel_classe is not None:
        return LockInEfetivo(
            nivel_classe,
            CLASSE,
            True,
            "Hipótese pelo tipo de serviço: os serviços curados do mesmo tipo concordam.",
        )
    return LockInEfetivo(None, None, False, "Sem catálogo, inventário ou classe.")


def _inventario_vale(catalogo: str | None, inventario: str, *, prova_de_ausencia: bool) -> bool:
    if catalogo is None:
        # Achar recurso proprietário prova; afirmar "baixo" exige ter visto quase tudo.
        return ORDEM[inventario] > ORDEM["low"] or prova_de_ausencia
    if ORDEM[inventario] > ORDEM[catalogo]:
        return True
    return ORDEM[inventario] < ORDEM[catalogo] and prova_de_ausencia


def lock_in_da_origem(
    workload: Mapping[str, Any],
    inventario: Mapping[str, Any] | None = None,
    *,
    aceitar_tipo_inferido: bool = False,
) -> LockInEfetivo:
    """Lê do snapshot o que `lock_in_efetivo` precisa.

    - catálogo: `source_metadata.vendor_lock_in_level` (curado). Payload antigo, sem
      `source_metadata`, usa `migration.lock_in` quando ele não veio da classe;
    - classe: `migration.lock_in` quando `migration.fonte == "classe"`;
    - inventário: a entrada do serviço em `topologia.detalhe.servicos` (`exclusividade`,
      `cobertura`) e `cost_estimated`, como `technical_assessment.inventory_services`
      a monta. O `nivel` da entrada é o "migra junto" e não é lido aqui.

    Tipo inferido não tem evidência técnica, como em `technical_evidence`. A tela de
    acoplamento ainda mostra a hipótese dele (`aceitar_tipo_inferido`); o score não.
    """
    if workload.get("type_source") == "inferido" and not aceitar_tipo_inferido:
        return LockInEfetivo(None, None, False, "Tipo de serviço inferido, não confirmado.")
    migracao = workload.get("migration") or {}
    da_classe = migracao.get("fonte") == CLASSE
    if "source_metadata" in workload:
        catalogo = (workload.get("source_metadata") or {}).get("vendor_lock_in_level")
    else:
        catalogo = None if da_classe else migracao.get("lock_in")
    entrada = inventario or {}
    exclusividade = entrada.get("exclusividade")
    return lock_in_efetivo(
        catalogo,
        exclusividade=exclusividade if isinstance(exclusividade, Mapping) else None,
        cobertura=entrada.get("cobertura"),
        inventario_estimado=bool(entrada.get("cost_estimated")),
        classe=migracao.get("lock_in") if da_classe else None,
    )
