from datetime import datetime, timedelta
from uuid import uuid4

from app.domain.entities.analysis_job import AnalysisStatus
from app.domain.services.analise_vigente import Revisao, analises_por_billing

_T0 = datetime(2026, 10, 17, 9, 0)
_BILLING = uuid4()


def _rev(minutos: int, status: AnalysisStatus, *, billing=_BILLING, ativa=True) -> Revisao:
    return Revisao(uuid4(), billing, status, _T0 + timedelta(minutes=minutos), ativa)


def test_duas_concluidas_do_mesmo_billing_viram_uma_linha() -> None:
    antiga = _rev(0, AnalysisStatus.COMPLETED)
    com_inventario = _rev(30, AnalysisStatus.COMPLETED)
    [linha] = analises_por_billing([antiga, com_inventario])
    assert linha.vigente == com_inventario
    assert linha.reanalise is None


def test_reanalise_rodando_mantem_a_anterior_na_tela() -> None:
    concluida = _rev(0, AnalysisStatus.COMPLETED)
    rodando = _rev(30, AnalysisStatus.PROCESSING)
    [linha] = analises_por_billing([concluida, rodando])
    assert linha.vigente == concluida
    assert linha.reanalise == rodando
    assert linha.ultima_atividade == rodando.criada_em


def test_reanalise_que_falhou_nao_derruba_a_vigente() -> None:
    concluida = _rev(0, AnalysisStatus.COMPLETED)
    falhou = _rev(30, AnalysisStatus.FAILED)
    [linha] = analises_por_billing([concluida, falhou])
    assert linha.vigente == concluida
    assert linha.reanalise == falhou


def test_sem_nenhuma_concluida_mostra_a_mais_recente() -> None:
    falhou = _rev(0, AnalysisStatus.FAILED)
    rodando = _rev(30, AnalysisStatus.PENDING)
    [linha] = analises_por_billing([falhou, rodando])
    assert linha.vigente == rodando
    assert linha.reanalise is None


def test_tentativa_anterior_a_vigente_nao_e_reanalise() -> None:
    falhou_antes = _rev(0, AnalysisStatus.FAILED)
    concluida = _rev(30, AnalysisStatus.COMPLETED)
    [linha] = analises_por_billing([falhou_antes, concluida])
    assert linha.vigente == concluida
    assert linha.reanalise is None


def test_billings_diferentes_e_ordem_pela_atividade() -> None:
    outro = uuid4()
    a = _rev(0, AnalysisStatus.COMPLETED)
    b = _rev(10, AnalysisStatus.COMPLETED, billing=outro)
    a_rodando = _rev(20, AnalysisStatus.PROCESSING)
    linhas = analises_por_billing([a, b, a_rodando])
    assert [linha.vigente for linha in linhas] == [a, b]


def test_depreciadas_saem_e_voltam_na_lixeira() -> None:
    depreciada = _rev(0, AnalysisStatus.COMPLETED, ativa=False)
    assert analises_por_billing([depreciada]) == []
    [linha] = analises_por_billing([depreciada], incluir_depreciadas=True)
    assert linha.vigente == depreciada


def test_revisao_depreciada_nao_ocupa_o_lugar_da_ativa() -> None:
    # Dado antigo: só uma das revisões depreciada (antes a exclusão era por análise)
    ativa = _rev(0, AnalysisStatus.COMPLETED)
    depreciada = _rev(30, AnalysisStatus.COMPLETED, ativa=False)
    [linha] = analises_por_billing([ativa, depreciada], incluir_depreciadas=True)
    assert linha.vigente == ativa
