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
from app.domain.services.terraform_topologia import aplicar_topologia
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
from app.use_cases.analysis.topologia_no_payload import VERSAO as VERSAO_TOPOLOGIA

# Nomes fixos: o renderer referencia `google_compute_network.rede_principal.id`
# ao ligar subnet e banco. Se virarem configuráveis, as referências quebram.
REDE_PRINCIPAL = "rede_principal"
SUBNET_PRINCIPAL = "subnet_principal"


def montar_blueprint(
    payload_analise: dict[str, Any],
    entrada: TerraformGenerationIn,
) -> TerraformBlueprint:
    """Derivação + escolhas do usuário → blueprint pronto para renderizar."""
    validate_migration_strategy(payload_analise, entrada.target_provider.value)
    plano = derivar_plano(
        payload_analise,
        provedor_destino=entrada.target_provider.value,
        regiao=entrada.region,
        alta_disponibilidade=entrada.availability.high_availability,
    )
    if tem_topologia(payload_analise):
        plano = aplicar_topologia(plano, payload_analise, destino=entrada.target_provider.value)

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


def tem_topologia(payload: dict[str, Any]) -> bool:
    """A análise congelou uma topologia que este gerador sabe ler (RI14)."""
    topologia = payload.get("topologia")
    return isinstance(topologia, dict) and topologia.get("versao") == VERSAO_TOPOLOGIA


def validate_migration_strategy(payload: dict[str, Any], provider: str) -> None:
    """Do not provision a generic migration when saved evidence forbids that scenario."""
    decision = payload.get("decision_support") or {}
    governance = (payload.get("decision_context") or {}).get("governance") or []
    for evidence in governance:
        if evidence.get("provider") == provider and (
            evidence.get("region_approved") is False
            or evidence.get("compliance_approved") is False
        ):
            raise ValueError("Destino bloqueado por requisito declarado de região ou conformidade.")
    for item in decision.get("workload_strategies") or []:
        if item.get("provider") == provider and (
            item.get("strategy") in {"retain", "retire", "repurchase", "relocate"}
            or item.get("rule") == "7r.conflicting_declarations"
        ):
            raise ValueError(
                "Estratégia dos 7 Rs exige blueprint específico; "
                "o gerador atual não deve provisionar uma migração genérica."
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
    # Uma variável de versão por motor: com bancos medidos de motores diferentes,
    # uma variável só obrigaria o MySQL a receber a versão do PostgreSQL.
    versoes: dict[str, object] = {}
    for r in plano.recursos:
        if r.resource_type == "managed_database":
            nome = str(r.configuration.get("version_var") or "database_version")
            versoes.setdefault(nome, r.configuration.get("engine_measured"))
    for nome, motor in versoes.items():
        padrao = plano.variaveis_padrao.get(nome)
        if padrao is not None:
            descricao = (
                f"Versão do {motor}. O default é a versão medida no inventário da origem "
                "(a mais frequente); confira antes de aplicar."
            )
        elif motor:
            descricao = (
                f"Versão do {motor} (motor medido na origem). O billing informa o "
                "motor, não a versão."
            )
        else:
            descricao = (
                "Motor e versão do banco (ex.: POSTGRES_15, MYSQL_8_0). "
                "O billing informa o custo, não o motor."
            )
        variaveis.append(
            VariavelTerraform(name=nome, type="string", description=descricao, default=padrao)
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
