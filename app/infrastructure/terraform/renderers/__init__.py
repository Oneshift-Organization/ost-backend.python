"""Despacha o blueprint para o adaptador da nuvem escolhida.

Um `resource_type` que o adaptador não conhece devolve ``None`` e não é
renderizado — a allowlist na prática. Um provedor sem adaptador nem chega aqui:
`ProvedorSuportado` o recusa em 422, na borda HTTP.
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.terraform.renderers.aws import AdaptadorAWS
from app.infrastructure.terraform.renderers.azure import AdaptadorAzure
from app.infrastructure.terraform.renderers.base import ARQUIVOS, AdaptadorNuvem, montar
from app.infrastructure.terraform.renderers.gcp import AdaptadorGCP
from app.infrastructure.terraform.renderers.oci import AdaptadorOCI
from app.schemas.terraform import ProvedorSuportado, TerraformBlueprint

_ADAPTADORES: dict[ProvedorSuportado, AdaptadorNuvem] = {
    ProvedorSuportado.GCP: AdaptadorGCP(),
    ProvedorSuportado.AWS: AdaptadorAWS(),
    ProvedorSuportado.AZURE: AdaptadorAzure(),
    ProvedorSuportado.OCI: AdaptadorOCI(),
}


def renderizar(
    blueprint: TerraformBlueprint,
    *,
    procedencia: dict[str, Any] | None = None,
    topologia: dict[str, Any] | None = None,
    moeda: str | None = None,
) -> dict[str, str]:
    """Blueprint → `{nome do arquivo: conteúdo}`.

    ``procedencia`` é o bloco `premissas` da análise (preços, câmbio, confiança).
    Vai para o README porque um `.tf` parece preciso: sem dizer de quando é o
    preço e de quando é o câmbio, o cliente lê uma foto antiga como estado atual.

    ``topologia`` é o bloco que a análise congelou do inventário (RI15.8): vira a
    seção "Topologia de origem" do README. ``moeda`` é a da análise, em que vêm
    os custos da cobertura.
    """
    adaptador = _ADAPTADORES.get(blueprint.provider)
    if adaptador is None:  # pragma: no cover - barrado antes, no schema
        raise ValueError(f"sem adaptador para {blueprint.provider}")
    return montar(adaptador, blueprint, procedencia, topologia, moeda)


def arquivos_esperados() -> tuple[str, ...]:
    return ARQUIVOS


def provedores_com_template() -> tuple[str, ...]:
    return tuple(sorted(p.value for p in _ADAPTADORES))
