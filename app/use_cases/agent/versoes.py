"""Versões que acompanham um snapshot do agente (PLANO_SNAPSHOT_AGENTE, D13).

O envio aplica a autoridade **atual** sobre um payload **congelado**. Se a
política de decisão, a topologia ou as regras de autoridade mudarem entre a
preparação e o envio, o resultado seria uma reinterpretação silenciosa do que o
agente leu. As versões ficam gravadas no snapshot e entram no fingerprint; no
envio, qualquer divergência recusa com `SNAPSHOT_DESATUALIZADO`.

`AUTHORITY_VERSION` não se atualiza sozinha. Quem a protege é
`test_versoes_agente.py`, que guarda o hash do código das regras de autoridade
e falha quando ele muda sem a versão subir.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.schemas.decision_support import DecisionSupport
from app.use_cases.analysis.topologia_no_payload import VERSAO as VERSAO_TOPOLOGIA

# A forma da representação entregue ao agente. Não entra na comparação do envio:
# o envio corrige contra o payload completo, que não depende dela.
CONTRACT_VERSION = "agent-snapshot-v1"

# As regras que o envio aplica sobre o snapshot. Suba quando a mesma entrada
# passar a produzir outra análise corrigida.
AUTHORITY_VERSION = "agent-authority-v1"


def politica_de_decisao_atual() -> str:
    return str(DecisionSupport.model_fields["policy_version"].default)


def versoes_do_payload(payload: Mapping[str, Any]) -> dict[str, str | None]:
    """As versões de um payload recém-montado."""
    decisao = payload.get("decision_support")
    topologia = payload.get("topologia")
    return {
        "contract": CONTRACT_VERSION,
        "decision_policy": (
            str(decisao.get("policy_version")) if isinstance(decisao, Mapping) else None
        ),
        "topology": str(topologia.get("versao")) if isinstance(topologia, Mapping) else None,
        "authority": AUTHORITY_VERSION,
    }


def divergencias(versoes: Mapping[str, Any]) -> list[str]:
    """Campos em que o snapshot difere do código atual (vazio = pode corrigir)."""
    diferentes = []
    if versoes.get("authority") != AUTHORITY_VERSION:
        diferentes.append("authority")
    politica = versoes.get("decision_policy")
    if politica is not None and politica != politica_de_decisao_atual():
        diferentes.append("decision_policy")
    topologia = versoes.get("topology")
    if topologia is not None and topologia != VERSAO_TOPOLOGIA:
        diferentes.append("topology")
    return diferentes
