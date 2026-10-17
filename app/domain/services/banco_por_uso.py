"""Banco gerenciado pela vCPU-hora da instância e pelo motor (PLANO_PRECISAO_OCI P1.6).

Antes, o custo inteiro de banco (instância, storage, IO, backup) era repreçado pela
razão de uma taxa de referência por vCPU-mês: OCI 30 contra AWS 62. A taxa da origem
era a de uma classe de uso geral, e a fatura da régua tinha uma db.t3.medium
(US$ 0,036 por vCPU-hora): a OCI saía pela metade do preço real.

Aqui a linha de instância vira vCPU-hora (horas x vCPU da classe, x 2 quando a
implantação tem réplica síncrona), e o destino a cota pela taxa oficial dele por
vCPU-hora, do motor da linha. O resto do custo de banco (storage, IO, backup) é
cotado à parte, pela razão entre as taxas de storage de banco.

Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.domain.services.instance_shape import parse_instance_shape

CATEGORIA = "database"
UNIDADE = "vcpu_hour"
# Storage de banco, para o custo sem vCPU-hora (storage, IO, backup)
CATEGORIA_STORAGE = "database_storage"
MYSQL = "mysql"
POSTGRESQL = "postgresql"
# Categoria de taxa de cada motor; sem motor conhecido, a de `database` (a maior)
CATEGORIA_DO_MOTOR = {MYSQL: "database_mysql", POSTGRESQL: "database_postgresql"}

_DOIS = Decimal(2)
# AWS: classe do RDS (db.) ou do ElastiCache (cache.) no UsageType de instância
_AWS_INSTANCIA = re.compile(
    r"(?:InstanceUsage|Multi-AZUsage|HeavyUsage|NodeUsage)[^:]*:(?:db|cache)\.([a-z0-9]+\.[a-z0-9]+)",
    re.IGNORECASE,
)
# A AWS abrevia tamanhos grandes na fatura: `db.r8g.12xl`
_XL_ABREVIADO = re.compile(r"(\d*)xl$")
# Azure: medidor por vCore-hora do servidor flexível
_AZURE_VCORE = re.compile(r"\bvcore\b", re.IGNORECASE)
# GCP: SKU de vCPU do Cloud SQL; regional é a alta disponibilidade, duas instâncias
_GCP_VCPU = re.compile(r"cloud sql.*-\s*vcpu", re.IGNORECASE)
_GCP_REGIONAL = re.compile(r"\bregional\b", re.IGNORECASE)


def vcpu_horas(
    provedor: str, uso: str | None, quantidade: Decimal | None, unidade: str | None
) -> Decimal | None:
    """vCPU-hora da linha de instância de banco, ou None quando a linha não é de
    instância (storage, IO, backup) ou não diz o tamanho."""
    if not uso or quantidade is None or quantidade <= 0:
        return None
    p = provedor.lower()
    if p == "aws":
        return _aws(uso, quantidade)
    if p == "azure" and _AZURE_VCORE.search(uso) and _em_horas(unidade):
        return quantidade
    if p == "gcp" and _GCP_VCPU.search(uso) and _em_horas(unidade):
        return quantidade * (_DOIS if _GCP_REGIONAL.search(uso) else Decimal(1))
    return None


def motor(provedor: str, servico: str | None, uso: str | None, declarado: str | None) -> str | None:
    """MySQL, PostgreSQL ou None. O atributo da fatura detalhada vence; sem ele, o nome
    do serviço ou do uso (Azure e GCP dizem o motor no nome)."""
    texto = " ".join(x for x in (declarado, servico, uso) if x).lower()
    if "postgres" in texto:
        return POSTGRESQL
    if "mysql" in texto or "mariadb" in texto:
        return MYSQL
    return None


def _aws(uso: str, horas: Decimal) -> Decimal | None:
    m = _AWS_INSTANCIA.search(uso)
    if m is None:
        return None
    classe = _XL_ABREVIADO.sub(r"\1xlarge", m.group(1).lower())
    forma = parse_instance_shape(classe)
    if forma is None:
        return None
    replicas = _DOIS if "multi-az" in uso.lower() else Decimal(1)
    return horas * forma.vcpus * replicas


def _em_horas(unidade: str | None) -> bool:
    return (unidade or "").strip().lower() in {"hours", "hour", "hrs", "1 hour", "h"}
