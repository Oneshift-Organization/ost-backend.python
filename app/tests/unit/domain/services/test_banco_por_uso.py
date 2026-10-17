"""Banco pela vCPU-hora da instância e pelo motor (PLANO_PRECISAO_OCI P1.6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.banco_por_uso import (
    ACIMA_10GB,
    ARMAZENAMENTO,
    ATE_4GB,
    BACKUP,
    CACHE,
    DE_4_A_10GB,
    INSTANCIA,
    INSTANCIA_SEM_HORAS,
    MYSQL,
    OUTROS,
    POSTGRESQL,
    SEM_EQUIVALENTE,
    copias_do_storage,
    fator_ha,
    motor,
    parte_do_banco,
    vcpu_horas,
)

D = Decimal


@pytest.mark.parametrize(
    ("provedor", "uso", "horas", "unidade", "esperado"),
    [
        # db.t3.medium: 2 vCPU
        ("aws", "InstanceUsage:db.t3.medium", "535.747", None, "1071.494"),
        # Multi-AZ: a réplica síncrona é outra instância
        ("aws", "Multi-AZUsage:db.t4g.xlarge", "100", "hours", "800"),
        # Tamanho abreviado pela AWS
        ("aws", "InstanceUsage:db.r8g.12xl", "10", None, "480"),
        ("aws", "InstanceUsageIOOptimized:db.r8g.16xl", "1", "hours", "64"),
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
def test_vcpu_hora_da_instancia(
    provedor: str, uso: str, horas: str, unidade: str | None, esperado: str
) -> None:
    assert vcpu_horas(provedor, uso, D(horas), unidade) == D(esperado)


@pytest.mark.parametrize(
    ("provedor", "uso", "unidade"),
    [
        ("aws", "RDS:Multi-AZ-GP3-Storage", "gibibyte month"),
        ("aws", "RDS:ChargedBackupUsage", "Units"),
        ("aws", "USE1-Kafka.m5.large", "hours"),
        # Nó de cache não é instância de banco: vai pela memória (B1)
        ("aws", "USE1-NodeUsage:cache.m6g.xlarge", "hours"),
        # Taxa da reserva, em qualquer formato: as horas vêm no uso coberto
        ("aws", "HeavyUsage:db.r8g.12xl", "hours"),
        ("gcp", "Cloud SQL for PostgreSQL: Zonal - Small instance in Americas", "hours"),
        ("gcp", "Cloud SQL for PostgreSQL: Zonal - Standard storage in Americas", "gibibyte month"),
        ("oci", "Oracle Autonomous Transaction Processing - ECPU", "hours"),
    ],
)
def test_linha_que_nao_e_instancia_nao_mede(provedor: str, uso: str, unidade: str) -> None:
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
def test_motor(servico: str, uso: str, declarado: str | None, esperado: str | None) -> None:
    assert motor("aws", servico, uso, declarado) == esperado


@pytest.mark.parametrize(
    ("provedor", "uso", "unidade", "qtd", "esperado"),
    [
        # Cache pela memória de fábrica do nó (12,93 GiB no cache.m6g.xlarge)
        ("aws", "USE1-NodeUsage:cache.m6g.xlarge", "hours", "10", (CACHE, "129.30", ACIMA_10GB)),
        ("aws", "NodeUsage:cache.t4g.medium", "hours", "10", (CACHE, "30.90", ATE_4GB)),
        ("aws", "NodeUsage:cache.m7g.large", "hours", "1", (CACHE, "6.38", DE_4_A_10GB)),
        # Cobranças sem par no destino
        (
            "aws",
            "USE1-ExtendedSupportYr1_Yr2-NodeUsage:cache.m6g.xlarge",
            "hours",
            "10",
            (SEM_EQUIVALENTE, None, None),
        ),
        ("aws", "RDS:Multi-AZ-GP3-PIOPS", "IOPS-Mo", "3000", (SEM_EQUIVALENTE, None, None)),
        ("aws", "RDS:GP3-Throughput", "GiBps-mo", "100", (SEM_EQUIVALENTE, None, None)),
        ("aws", "CPUCredits:db.t3", "vCPU-Hours", "300", (SEM_EQUIVALENTE, None, None)),
        # Requisição de IO, mesmo rotulada como GB-mês (o mock rotula assim)
        (
            "aws",
            "Aurora:StorageIOUsage",
            "gibibyte month",
            "1191126",
            (SEM_EQUIVALENTE, None, None),
        ),
        # Armazenamento e backup em GB-mês
        ("aws", "RDS:GP3-Storage", "gibibyte month", "48.228", (ARMAZENAMENTO, "48.228", None)),
        (
            "aws",
            "Aurora:IO-OptimizedStorageUsage",
            "GB-Mo",
            "5524.5",
            (ARMAZENAMENTO, "5524.5", None),
        ),
        ("aws", "RDS:PIOPS-Storage", "GB-Mo", "100", (ARMAZENAMENTO, "100", None)),
        ("aws", "RDS:ChargedBackupUsage", "Units", "120", (BACKUP, "120", None)),
        ("aws", "Aurora:BackupUsage", "GB-Mo", "50", (BACKUP, "50", None)),
        # O que não se encaixa fica ao custo da origem
        ("aws", "Aurora:ServerlessV2Usage", "ACU-Hr", "48", (OUTROS, None, None)),
        # Azure, GCP e OCI
        (
            "azure",
            "Azure Database for MySQL | Storage Data Stored",
            "1 GB/Month",
            "100",
            (ARMAZENAMENTO, "100", None),
        ),
        ("azure", "Backup Storage LRS Data Stored", "1 GB/Month", "10", (BACKUP, "10", None)),
        ("azure", "Additional IOPS", "1/Month", "500", (SEM_EQUIVALENTE, None, None)),
        (
            "gcp",
            "Cloud SQL for PostgreSQL: Zonal - RAM in Americas",
            "gibibyte hour",
            "730",
            (INSTANCIA_SEM_HORAS, None, None),
        ),
        (
            "gcp",
            "Cloud SQL for PostgreSQL: Regional - Standard storage in Americas",
            "gibibyte month",
            "100",
            (ARMAZENAMENTO, "100", None),
        ),
        (
            "gcp",
            "Redis Capacity Basic M2 Americas",
            "gibibyte hour",
            "5110",
            (CACHE, "5110", DE_4_A_10GB),
        ),
        (
            "gcp",
            "Redis Capacity Standard M1 Americas",
            "gibibyte hour",
            "730",
            (CACHE, "1460", ATE_4GB),
        ),
        ("oci", "MySQL Database - ECPU", "hours", "1460", (INSTANCIA, "1460", None)),
        ("oci", "Database with PostgreSQL - X86 OCPU", "hours", "730", (INSTANCIA, "1460", None)),
        (
            "oci",
            "Oracle Autonomous Transaction Processing - ECPU",
            "hours",
            "100",
            (OUTROS, None, None),
        ),
    ],
)
def test_parte_de_cada_linha(
    provedor: str,
    uso: str,
    unidade: str,
    qtd: str,
    esperado: tuple[str, str | None, str | None],
) -> None:
    tipo, quantidade, faixa = esperado
    parte = parte_do_banco(provedor, None, uso, unidade, D(qtd))
    assert parte.tipo == tipo
    assert parte.quantidade == (D(quantidade) if quantidade else None)
    assert parte.faixa == faixa


def test_ha_marca_armazenamento_e_instancia() -> None:
    storage = parte_do_banco("aws", "AmazonRDS", "RDS:Multi-AZ-GP3-Storage", "GB-Mo", D(100))
    assert (storage.tipo, storage.ha) == (ARMAZENAMENTO, True)
    inst = parte_do_banco(
        "aws", "AmazonRDS", "Multi-AZUsage:db.m6i.large", "hours", D(10), motor_declarado="MySQL"
    )
    assert (inst.tipo, inst.quantidade, inst.ha, inst.motor) == (INSTANCIA, D(40), True, MYSQL)


def test_taxa_de_reserva_paga_a_instancia_sem_horas() -> None:
    db = parte_do_banco("aws", None, "HeavyUsage:db.r8g.12xl", None, D(2027), cobranca="commitment")
    assert (db.tipo, db.quantidade) == (INSTANCIA_SEM_HORAS, None)
    cache = parte_do_banco(
        "aws", None, "HeavyUsage:cache.t4g.medium", None, D(100), cobranca="commitment"
    )
    assert (cache.tipo, cache.quantidade, cache.faixa) == (CACHE, None, ATE_4GB)


def test_alta_disponibilidade_por_nuvem_e_motor() -> None:
    """OCI MySQL HeatWave em HA tem 3 instâncias, cada uma com storage próprio (docs
    oficiais); o storage otimizado do PostgreSQL da OCI é compartilhado (premissa)."""
    assert fator_ha("oci", MYSQL) == D("1.5")
    assert fator_ha("oci", POSTGRESQL) == D(1)
    assert fator_ha("aws", MYSQL) == D(1)
    assert copias_do_storage("oci", MYSQL, True) == D(3)
    assert copias_do_storage("oci", POSTGRESQL, True) == D(1)
    assert copias_do_storage("azure", POSTGRESQL, True) == D(2)
    assert copias_do_storage("oci", MYSQL, False) == D(1)
