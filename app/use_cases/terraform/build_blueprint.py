"""Monta o `TerraformBlueprint` a partir da derivação e das escolhas do usuário.

A derivação (`domain/services/terraform_plan`) cuida do que sai da análise. Aqui
entra o que **só o usuário sabe**: rede, região, ambiente, nome. E as variáveis
que representam o que ninguém sabe.

Sem IA. O modelo entra depois, e só para melhorar nomes e texto — ver
`PLANO_TERRAFORM.md` §5.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from app.domain.services.terraform_plan import PlanoDerivado, derivar_plano
from app.schemas.terraform import (
    Categoria,
    Premissa,
    ProvedorSuportado,
    RecursoTerraform,
    TerraformBlueprint,
    TerraformGenerationIn,
    TipoRecurso,
    VariavelTerraform,
)

# Nomes fixos: o renderer referencia `google_compute_network.rede_principal.id`
# ao ligar subnet e banco. Se virarem configuráveis, as referências quebram.
REDE_PRINCIPAL = "rede_principal"
SUBNET_PRINCIPAL = "subnet_principal"


def montar_blueprint(
    payload_analise: dict[str, Any],
    entrada: TerraformGenerationIn,
) -> TerraformBlueprint:
    """Derivação + escolhas do usuário → blueprint pronto para renderizar."""
    plano = derivar_plano(
        payload_analise,
        provedor_destino=entrada.target_provider.value,
        regiao=entrada.region,
        alta_disponibilidade=entrada.availability.high_availability,
    )

    recursos = [*_rede(entrada), *_recursos_derivados(plano)]

    return TerraformBlueprint(
        provider=ProvedorSuportado(entrada.target_provider),
        region=entrada.region,
        project_name=entrada.project_name,
        environment=entrada.environment,
        resources=recursos,
        variables=_variaveis(plano),
        outputs=[],
        assumptions=[_premissa(p) for p in plano.premissas],
        unsupported_items=plano.nao_suportado,
        warnings=plano.avisos,
    )


def _rede(entrada: TerraformGenerationIn) -> list[RecursoTerraform]:
    """VPC e subnet vêm inteiramente da requisição — o billing não tem rede.

    A subnet recebe a primeira faixa /24 do CIDR informado, e não o CIDR inteiro:
    ocupar tudo numa subnet só impediria o cliente de criar qualquer outra sem
    reescrever o plano.
    """
    rede = ipaddress.ip_network(entrada.network.cidr)
    primeira = next(rede.subnets(new_prefix=24), rede)

    return [
        RecursoTerraform(
            logical_name=REDE_PRINCIPAL,
            resource_type=TipoRecurso.NETWORK,
            configuration={"cidr": str(rede)},
        ),
        RecursoTerraform(
            logical_name=SUBNET_PRINCIPAL,
            resource_type=TipoRecurso.SUBNET,
            depends_on=[REDE_PRINCIPAL],
            configuration={
                "cidr": str(primeira),
                "public_access": entrada.network.public_access,
            },
        ),
    ]


def _recursos_derivados(plano: PlanoDerivado) -> list[RecursoTerraform]:
    return [
        RecursoTerraform(
            logical_name=r.logical_name,
            resource_type=TipoRecurso(r.resource_type),
            depends_on=list(r.depends_on),
            configuration=dict(r.configuration),
        )
        for r in plano.recursos
    ]


def _variaveis(plano: PlanoDerivado) -> list[VariavelTerraform]:
    """O que a análise não soube responder vira variável **sem default**.

    Sem default o `terraform plan` para e pergunta. Com um default nosso, o
    cliente aplicaria um palpite achando que era resultado da análise — que é
    exatamente o que este desenho inteiro existe para evitar.
    """
    variaveis = [
        VariavelTerraform(
            name="boot_image",
            type="string",
            description=(
                "Imagem de boot das VMs. O billing não informa o sistema "
                "operacional em uso."
            ),
            default="debian-cloud/debian-12",
        )
    ]
    if any(r.resource_type == "managed_database" for r in plano.recursos):
        variaveis.append(
            VariavelTerraform(
                name="database_version",
                type="string",
                description=(
                    "Motor e versão do banco (ex.: POSTGRES_15, MYSQL_8_0). "
                    "O billing informa o custo, não o motor."
                ),
            )
        )
    for premissa in plano.premissas:
        if premissa.categoria == "sem_base" and premissa.campo.endswith(".machine_type"):
            variaveis.append(
                VariavelTerraform(
                    name=premissa.campo.replace(".", "_"),
                    type="string",
                    description=premissa.base[:300],
                )
            )
    return variaveis


def _premissa(p: Any) -> Premissa:
    return Premissa(
        campo=p.campo,
        categoria=Categoria(p.categoria),
        valor=p.valor,
        base=p.base,
    )
