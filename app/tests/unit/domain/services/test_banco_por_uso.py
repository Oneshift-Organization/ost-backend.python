"""Banco pela vCPU-hora da instância e pelo motor (PLANO_PRECISAO_OCI P1.6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.banco_por_uso import MYSQL, POSTGRESQL, motor, vcpu_horas

D = Decimal


@pytest.mark.parametrize(
    ("provedor", "uso", "horas", "unidade", "esperado"),
    [
        # db.t3.medium: 2 vCPU
        ("aws", "InstanceUsage:db.t3.medium", "535.747", None, "1071.494"),
        # Multi-AZ: a réplica síncrona é outra instância
        ("aws", "Multi-AZUsage:db.t4g.xlarge", "100", "hours", "800"),
        # Reserva e tamanho abreviado pela AWS
        ("aws", "HeavyUsage:db.r8g.12xl", "10", None, "480"),
        ("aws", "InstanceUsageIOOptimized:db.r8g.16xl", "1", "hours", "64"),
        # ElastiCache
        ("aws", "USE1-NodeUsage:cache.m6g.xlarge", "10", "hours", "40"),
        # Azure: o medidor já é vCore-hora
        (
            "azure",
            "Azure Database for PostgreSQL | Flexible Server General Purpose Dsv3 Series"
            " Compute | vCore",
            "1460",
            "hours",
            "1460",
        ),
        # GCP: vCPU-hora; regional (alta disponibilidade) dobra
        ("gcp", "Cloud SQL for MySQL: Zonal - vCPU in Americas", "730", "hour", "730"),
        ("gcp", "Cloud SQL for PostgreSQL: Regional - vCPU in Americas", "730", "hour", "1460"),
    ],
)
def test_vcpu_hora_da_instancia(provedor, uso, horas, unidade, esperado) -> None:
    assert vcpu_horas(provedor, uso, D(horas), unidade) == D(esperado)


@pytest.mark.parametrize(
    ("provedor", "uso", "unidade"),
    [
        ("aws", "RDS:Multi-AZ-GP3-Storage", "gibibyte month"),
        ("aws", "RDS:ChargedBackupUsage", "Units"),
        ("aws", "USE1-Kafka.m5.large", "hours"),
        ("gcp", "Cloud SQL for PostgreSQL: Zonal - Small instance in Americas", "hours"),
        ("gcp", "Cloud SQL for PostgreSQL: Zonal - Standard storage in Americas", "gibibyte month"),
        ("oci", "Oracle Autonomous Transaction Processing - ECPU", "hours"),
    ],
)
def test_linha_que_nao_e_instancia_nao_mede(provedor, uso, unidade) -> None:
    assert vcpu_horas(provedor, uso, D("100"), unidade) is None


def test_sem_quantidade_nao_mede() -> None:
    assert vcpu_horas("aws", "InstanceUsage:db.t3.medium", None, None) is None
    assert vcpu_horas("aws", "InstanceUsage:db.t3.medium", D(0), None) is None


@pytest.mark.parametrize(
    ("servico", "uso", "declarado", "esperado"),
    [
        ("AmazonRDS", "InstanceUsage:db.t3.medium", "PostgreSQL", POSTGRESQL),
        ("AmazonRDS", "InstanceUsage:db.t3.medium", "Aurora MySQL", MYSQL),
        ("AmazonRDS", "InstanceUsage:db.t3.medium", "MariaDB", MYSQL),
        ("AmazonRDS", "InstanceUsage:db.t3.medium", None, None),
        ("Azure Database for MySQL", "vCore", None, MYSQL),
        ("Cloud SQL", "Cloud SQL for PostgreSQL: Zonal - vCPU", None, POSTGRESQL),
        ("AmazonElastiCache", "NodeUsage:cache.m6g.xlarge", "Redis", None),
    ],
)
def test_motor(servico, uso, declarado, esperado) -> None:
    assert motor("aws", servico, uso, declarado) == esperado
