"""Orquestra a geração: lê a análise, deriva, renderiza, formata e empacota.

A ordem importa e está comentada em cada passo. O ponto que mais importa:
**lemos o `llm_input_payload` gravado no job**, não reconstruímos o
`MigrationContext`. Recalcular produziria números possivelmente diferentes dos
que o usuário aprovou na tela — preço, câmbio e catálogo mudam entre execuções —
e o Terraform descreveria uma infraestrutura que ninguém aprovou.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.infrastructure.terraform.archive import montar_zip
from app.infrastructure.terraform.fmt import formatar
from app.infrastructure.terraform.gcp_renderer import renderizar
from app.infrastructure.terraform.validate import validar
from app.schemas.terraform import (
    ResumoValidacao,
    TerraformBlueprint,
    TerraformGenerationIn,
)
from app.use_cases.terraform.build_blueprint import montar_blueprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResultadoGeracao:
    blueprint: TerraformBlueprint
    arquivos: dict[str, str]
    validacao: ResumoValidacao


def gerar_pacote(
    payload_analise: dict[str, Any],
    entrada: TerraformGenerationIn,
) -> ResultadoGeracao:
    """Função pura em tudo que importa: mesma entrada, mesmo pacote.

    A única parte não determinística é o `terraform fmt`, que só roda se o
    binário existir — e como os arquivos já saem no formato canônico, a passagem
    dele é conferência, não correção.
    """
    blueprint = montar_blueprint(payload_analise, entrada)
    premissas = payload_analise.get("premissas") or {}
    arquivos = renderizar(blueprint, procedencia=premissas)

    arquivos, validacao = formatar(arquivos)
    # `validate` vê o que o `fmt` não vê: schema do provider, atributo que não
    # existe, tipo errado. Desligado por default — ver `terraform/validate.py`.
    validacao = validar(arquivos, formatado=validacao.formatado)

    # Manifesto e validação entram DEPOIS do fmt: são JSON, não HCL, e passá-los
    # pelo formatador do Terraform não faz sentido.
    arquivos["manifest.json"] = _manifesto(blueprint, entrada, premissas)
    arquivos["validation.json"] = json.dumps(
        validacao.model_dump(), ensure_ascii=False, indent=2
    )

    return ResultadoGeracao(
        blueprint=blueprint, arquivos=arquivos, validacao=validacao
    )


def empacotar(resultado: ResultadoGeracao, *, nome: str) -> bytes:
    return montar_zip(resultado.arquivos, raiz=nome)


def _manifesto(
    blueprint: TerraformBlueprint,
    entrada: TerraformGenerationIn,
    premissas: dict[str, Any],
) -> str:
    """O que sustenta o pacote, legível por máquina.

    O README diz a mesma coisa em prosa. Este arquivo existe para quem for
    auditar depois: de qual análise saiu, com que preços, com que câmbio, e o
    que foi estimado em vez de calculado.
    """
    return json.dumps(
        {
            "contract_version": blueprint.contract_version,
            "gerado_por": "oneshift-backend",
            "analysis_job_id": str(entrada.analysis_job_id),
            "provider": blueprint.provider.value,
            "region": blueprint.region,
            "environment": blueprint.environment,
            "recursos": [
                {
                    "nome": r.logical_name,
                    "tipo": r.resource_type.value,
                }
                for r in blueprint.resources
            ],
            "premissas": [
                {
                    "campo": p.campo,
                    "categoria": p.categoria.value,
                    "valor": p.valor,
                    "base": p.base,
                }
                for p in blueprint.assumptions
            ],
            "nao_suportado": blueprint.unsupported_items,
            "avisos": blueprint.warnings,
            "procedencia_da_analise": {
                "fx": premissas.get("fx"),
                "confianca_analise": premissas.get("confianca_analise"),
                "fonte_precos": premissas.get("fonte_precos"),
            },
            # Dito explicitamente: o pacote não declara economia porque a
            # cotação usa região de referência, que pode não ser a escolhida.
            "custo_declarado": None,
        },
        ensure_ascii=False,
        indent=2,
    )
