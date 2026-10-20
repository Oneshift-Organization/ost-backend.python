"""Qual compromisso paga máquina virtual (PLANO_EMPACOTAMENTO, F2)."""

from __future__ import annotations

import pytest

from app.domain.services.compromisso_de_compute import paga_maquina_virtual


@pytest.mark.parametrize(
    ("usage_type", "servico", "esperado"),
    [
        # Bill AWS real (Cost Explorer).
        ("ComputeSP:3yrNoUpfront", "Savings Plans for AWS Compute usage", True),
        ("EC2SP:r7g.1yrNoUpfront", "Savings Plans for AWS Compute usage", True),
        ("USE2-HeavyUsage:m5.large", "AmazonEC2", True),
        # Reserva de banco e de cache paga outro serviço.
        ("HeavyUsage:db.r8g.12xl", "AmazonRDS", False),
        ("HeavyUsage:cache.t4g.medium", "AmazonElastiCache", False),
        # CUD de vCPU/RAM do GCP e reserva de VM da Azure.
        ("Commitment v1: Cpu in Americas for 1 Year", "Compute Engine", True),
        (None, "Virtual Machines", True),
        # Sem regra que case, não.
        ("TimedStorage-ByteHrs", "AmazonS3", False),
        (None, None, False),
    ],
)
def test_regras(usage_type: str | None, servico: str | None, esperado: bool) -> None:
    assert paga_maquina_virtual(usage_type, servico) is esperado
