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
    _BASE_MAXIMA,
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
    # A imagem de boot é específica de cada nuvem (`boot_image` no GCP, `ami_id`
    # na AWS, `image_ocid` na OCI) e vem do adaptador. Declará-la aqui punha uma
    # variável de GCP dentro de um pacote de AWS.
    variaveis: list[VariavelTerraform] = []
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
    usados = {v.name for v in variaveis}
    for premissa in plano.premissas:
        if premissa.categoria == "sem_base" and premissa.campo.endswith(".machine_type"):
            variaveis.append(
                VariavelTerraform(
                    name=_nome_de_variavel(premissa.campo, usados),
                    type="string",
                    description=premissa.base[:300],
                )
            )
    return variaveis


def _nome_de_variavel(campo: str, usados: set[str]) -> str:
    """`<recurso>.machine_type` → nome de variável dentro do limite.

    O nome do recurso já pode ter 57 caracteres; somar `_machine_type` passava
    do teto de 60 e derrubava a geração inteira por causa de um pool com nome
    comprido. Truncar sozinho criaria colisão entre dois recursos de prefixo
    igual, daí o sufixo numérico.
    """
    bruto = campo.replace(".", "_")
    nome = bruto[:60]
    if nome not in usados:
        usados.add(nome)
        return nome
    for n in range(2, 1000):
        sufixo = f"_{n}"
        candidato = bruto[: 60 - len(sufixo)] + sufixo
        if candidato not in usados:
            usados.add(candidato)
            return candidato
    raise ValueError(f"não foi possível nomear variável para {campo!r}")


def _premissa(p: Any) -> Premissa:
    """Trunca a base em vez de deixar o schema derrubar a geração.

    Já aconteceu: a base acumula o raciocínio e passou de 400 caracteres num
    pool coberto por compromisso, estourando a validação e matando o pacote
    inteiro. Uma explicação cortada é ruim; perder o `.tf` por causa dela é pior.
    """
    base = p.base if len(p.base) <= _BASE_MAXIMA else p.base[: _BASE_MAXIMA - 1] + "…"
    return Premissa(
        campo=p.campo,
        categoria=Categoria(p.categoria),
        valor=p.valor,
        base=base,
    )
