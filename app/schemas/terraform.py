"""Contratos da geração de Terraform (`/v1/terraform-generations`).

Separado de `schemas/analysis.py` pelo mesmo motivo que `schemas/agent.py`: a
análise tem contrato próprio e não deve ser arrastada por mudanças aqui.

O que **não** existe na entrada é tão importante quanto o que existe: nenhuma
credencial de nuvem, nenhum número de custo, nenhum shape de máquina. O primeiro
porque a plataforma não provisiona; os outros dois porque o backend já os tem, e
aceitá-los do cliente abriria caminho para um `.tf` que descreve infraestrutura
diferente da que foi analisada.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Nome de recurso em Terraform aceita letras, dígitos, `_` e `-`, começando por
# letra ou `_`. Validamos na borda para o template nunca precisar escapar nada.
_NOME_TERRAFORM = re.compile(r"^[a-z_][a-z0-9_-]*$")

# Um /24 dá 256 endereços e não sobra espaço para dividir em subnets.
_PREFIXO_MAXIMO = 24
_IPV4 = 4

# Teto da explicação de uma premissa. Ver `Premissa.base`.
_BASE_MAXIMA = 800

CONTRATO_ENTRADA = "oneshift-terraform-input-v1"
CONTRATO_BLUEPRINT = "oneshift-terraform-v1"


class ProvedorSuportado(StrEnum):
    """Provedores com template escrito.

    Só GCP no MVP. O enum existe para o 422 dizer *qual* provedor falta em vez
    de devolver um erro genérico, e para a lista crescer sem tocar nas rotas.
    """

    GCP = "gcp"


class TipoRecurso(StrEnum):
    """Allowlist de recursos. O que não está aqui não é renderizado.

    Fecha o principal risco de deixar um modelo compor infraestrutura: pedir um
    recurso que ninguém revisou. Um tipo fora da lista vira `unsupported_items`,
    não um bloco `.tf`.
    """

    NETWORK = "network"
    SUBNET = "subnet"
    COMPUTE = "compute"
    OBJECT_STORAGE = "object_storage"
    MANAGED_DATABASE = "managed_database"


class Categoria(StrEnum):
    """De onde veio cada valor do blueprint.

    Espelha `domain/services/authority.Categoria`. Um `.tf` é um artefato que
    *parece* preciso: sem marcar o que foi derivado, o que foi estimado e o que
    ninguém sabe, o cliente lê estimativa como fato e aplica na infra dele.
    """

    DERIVACAO = "derivacao"      # sai da conta; existe uma resposta certa
    ESTIMATIVA = "estimativa"    # sai de uma premissa declarada
    SEM_BASE = "sem_base"        # ninguém sabe — vira variável em branco
    ESCOLHA_USUARIO = "escolha_usuario"


class ModoRede(StrEnum):
    CRIAR = "create"
    EXISTENTE = "existing"


# ── entrada ─────────────────────────────────────────────────────────────


class RedeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: ModoRede = ModoRede.CRIAR
    cidr: str = "10.0.0.0/16"
    public_access: bool = False

    @field_validator("cidr")
    @classmethod
    def _cidr_valido(cls, v: str) -> str:
        """Rede é `SEM_BASE`: o bill não contém topologia, então este valor vem
        inteiro do usuário e é a única defesa contra um `.tf` que não aplica."""
        rede = ipaddress.ip_network(v, strict=True)
        if rede.version != _IPV4:
            raise ValueError("apenas IPv4 no MVP")
        if rede.prefixlen > _PREFIXO_MAXIMO:
            raise ValueError(f"{v} não comporta subnets (use /24 ou maior)")
        return str(rede)


class DisponibilidadeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    high_availability: bool = False


class TerraformGenerationIn(BaseModel):
    """O que o frontend envia.

    Não reenvia o billing: `analysis_job_id` é a chave para tudo que o backend
    já calculou. O que vem daqui é só o que a análise **não pode** deduzir.
    """

    model_config = ConfigDict(extra="ignore")

    analysis_job_id: UUID
    project_name: str = Field(min_length=3, max_length=40)
    environment: Literal["dev", "staging", "prod"] = "dev"
    target_provider: ProvedorSuportado
    region: str = Field(min_length=2, max_length=40)
    network: RedeIn = Field(default_factory=RedeIn)
    availability: DisponibilidadeIn = Field(default_factory=DisponibilidadeIn)
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("project_name")
    @classmethod
    def _nome_seguro(cls, v: str) -> str:
        """Vai para dentro de identificadores no `.tf` e para o nome do ZIP."""
        v = v.strip().lower()
        if not _NOME_TERRAFORM.match(v):
            raise ValueError(
                "use minúsculas, dígitos, '-' ou '_', começando por letra"
            )
        return v

    @field_validator("region")
    @classmethod
    def _regiao_plausivel(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", v):
            raise ValueError("região inválida")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_seguras(cls, v: dict[str, str]) -> dict[str, str]:
        for chave in v:
            if not _NOME_TERRAFORM.match(chave):
                raise ValueError(f"chave de tag inválida: {chave!r}")
        return v


# ── blueprint ───────────────────────────────────────────────────────────


class Premissa(BaseModel):
    """Um valor do blueprint com a sua procedência.

    `base` é obrigatória quando a categoria não é derivação — é a mesma regra do
    `payback` na superfície de agente: um número com a premissa exposta é
    contestável; o mesmo número num campo indistinguível dos calculados é
    mentira por omissão.
    """

    model_config = ConfigDict(extra="ignore")

    campo: str = Field(max_length=120)
    categoria: Categoria
    valor: str | None = Field(default=None, max_length=200)
    # 800 e não 400: a base acumula o raciocínio inteiro — como o valor foi
    # obtido, qual a confiança do preço que o sustenta, a idade dele e as
    # ressalvas do pool. Com 400 a geração **caía** num pool coberto por
    # compromisso, que é justamente o caso que mais precisa de explicação.
    base: str = Field(max_length=_BASE_MAXIMA)


class RecursoTerraform(BaseModel):
    model_config = ConfigDict(extra="ignore")

    logical_name: str = Field(max_length=60)
    resource_type: TipoRecurso
    depends_on: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("logical_name")
    @classmethod
    def _nome_seguro(cls, v: str) -> str:
        v = v.strip().lower()
        if not _NOME_TERRAFORM.match(v):
            raise ValueError(f"nome de recurso inválido: {v!r}")
        return v


class VariavelTerraform(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=60)
    type: Literal["string", "number", "bool", "list(string)"] = "string"
    description: str = Field(max_length=300)
    default: str | None = None
    # Variável sem default é o que o `SEM_BASE` vira no `.tf`: o cliente é
    # obrigado a preencher em vez de herdar um palpite nosso.
    sensitive: bool = False

    @field_validator("name")
    @classmethod
    def _nome_seguro(cls, v: str) -> str:
        v = v.strip().lower()
        if not _NOME_TERRAFORM.match(v):
            raise ValueError(f"nome de variável inválido: {v!r}")
        return v


class TerraformBlueprint(BaseModel):
    """A infraestrutura descrita, antes de virar HCL.

    Note o que falta: nenhum campo de custo ou de economia. O `.tf` descreve o
    que provisionar, não quanto custa — e a cotação hoje usa uma região fixa por
    provedor, então um número aqui seria de outra região que não a escolhida.
    """

    model_config = ConfigDict(extra="ignore")

    contract_version: Literal["oneshift-terraform-v1"] = CONTRATO_BLUEPRINT
    provider: ProvedorSuportado
    region: str
    project_name: str
    environment: str = "dev"
    resources: list[RecursoTerraform] = Field(default_factory=list)
    variables: list[VariavelTerraform] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    assumptions: list[Premissa] = Field(default_factory=list)
    unsupported_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── saída ───────────────────────────────────────────────────────────────


class StatusGeracao(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (StatusGeracao.COMPLETED, StatusGeracao.FAILED)


class ResumoValidacao(BaseModel):
    model_config = ConfigDict(extra="ignore")

    formatado: bool = False
    validado: bool = False
    erros: list[str] = Field(default_factory=list)
    # `validate` exige `terraform init`, que baixa provider da HashiCorp. Fora do
    # MVP por decisão de infra — `fmt` é formatador puro e não precisa de rede.
    motivo_sem_validacao: str | None = None


class TerraformGenerationOut(BaseModel):
    """Estado da geração. `assumptions` e `unsupported_items` vão para a tela —
    é o que impede o pacote de ser lido como afirmação."""

    model_config = ConfigDict(extra="ignore")

    generation_id: UUID
    analysis_job_id: UUID
    report_id: UUID | None = None
    status: StatusGeracao
    target_provider: str
    region: str
    project_name: str
    validation: ResumoValidacao | None = None
    assumptions: list[Premissa] = Field(default_factory=list)
    unsupported_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    available_files: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
