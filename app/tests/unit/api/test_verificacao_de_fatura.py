"""`POST /v1/billing-file-checks`: a crítica do arquivo antes do upload."""

from __future__ import annotations

from pathlib import Path

from app.api.v1.routes.billing_checks import verificar_arquivo

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "billing"


def test_cost_explorer_e_recusado_com_a_explicacao() -> None:
    r = verificar_arquivo(_FIXTURES / "aws_ce_daily_by_service.tsv")
    assert r.aceita is False
    assert r.formato == "AWS_CE_DAILY_BY_SERVICE"
    assert r.problemas[0].nivel == "critico"
    assert r.como_exportar and "CUR 2.0" in r.como_exportar


def test_cur_com_coluna_de_id_e_aceito_sem_critica() -> None:
    r = verificar_arquivo(_FIXTURES / "aws_cur2_sample.csv")
    assert (r.aceita, r.formato, r.com_id_de_recurso) == (True, "AWS_CUR2.0", True)
    assert r.problemas == []


def test_arquivo_que_nao_e_fatura_e_recusado(tmp_path: Path) -> None:
    arq = tmp_path / "notas.csv"
    arq.write_text("nome,idade\nana,30\n")
    r = verificar_arquivo(arq)
    assert r.aceita is False
    assert r.problemas[0].codigo == "formato_nao_reconhecido"
