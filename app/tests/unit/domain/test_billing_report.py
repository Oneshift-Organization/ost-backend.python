from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ValidationError
from app.domain.entities.billing_report import ALIAS_MAXIMO, BillingReport, JobStatus
from app.domain.value_objects.cloud_provider import CloudProvider


def test_create_defaults_to_queued():
    r = BillingReport(user_id=uuid4())
    assert r.status is JobStatus.QUEUED
    assert r.row_count == 0
    assert isinstance(r.id, UUID)


def test_rejects_non_uuid_user_id():
    with pytest.raises(ValidationError):
        BillingReport(user_id="not-uuid")  # type: ignore[arg-type]


def test_state_machine_queued_to_processing_to_done():
    r = BillingReport(user_id=uuid4())
    r.mark_processing()
    assert r.status is JobStatus.PROCESSING
    r.mark_done(row_count=42, provider=CloudProvider.AWS, schema_version="AWS_CUR2.0")
    assert r.status is JobStatus.DONE
    assert r.row_count == 42
    assert r.provider is CloudProvider.AWS


def test_cannot_skip_processing_step():
    r = BillingReport(user_id=uuid4())
    with pytest.raises(ValidationError):
        r.mark_done(row_count=1, provider=CloudProvider.AWS, schema_version="x")


def test_failed_is_terminal_marker():
    r = BillingReport(user_id=uuid4())
    r.mark_failed("disk full")
    assert r.status is JobStatus.FAILED
    assert r.error_message == "disk full"


# ── nome do billing (alias, M9) ─────────────────────────────────────────


def test_sem_alias_o_nome_e_o_do_arquivo():
    r = BillingReport(user_id=uuid4(), source_filename="aws_cur.csv")
    assert r.nome_exibicao == "aws_cur.csv"
    assert BillingReport(user_id=uuid4()).nome_exibicao is None


@pytest.mark.parametrize(
    ("dado", "alias"),
    [
        ("  Fatura   AWS\tde março ", "Fatura AWS de março"),
        ("", None),
        ("   ", None),
        (None, None),
        ("x" * ALIAS_MAXIMO, "x" * ALIAS_MAXIMO),
    ],
)
def test_renomear_normaliza_e_vazio_volta_ao_arquivo(dado, alias):
    r = BillingReport(user_id=uuid4(), source_filename="aws_cur.csv", alias="antigo")
    r.renomear(dado)
    assert r.alias == alias
    assert r.nome_exibicao == (alias or "aws_cur.csv")


@pytest.mark.parametrize("dado", ["x" * (ALIAS_MAXIMO + 1), "nome\x00oculto", "linha\x1bescape"])
def test_renomear_recusa_longo_e_caractere_de_controle(dado):
    r = BillingReport(user_id=uuid4(), alias="antigo")
    with pytest.raises(ValidationError):
        r.renomear(dado)
    assert r.alias == "antigo"
