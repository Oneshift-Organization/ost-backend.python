"""Diagnóstico de entrada: o que cada combinação de arquivos permite (PLANO_CENARIOS_DE_ENTRADA)."""

from __future__ import annotations

from app.domain.services.diagnostico_de_entrada import (
    ALERTA,
    CRITICO,
    INFORMATIVO,
    diagnosticar_analise,
    diagnosticar_arquivo,
    diagnosticar_payload,
    problemas_do_arquivo,
)


def _topologia(**cobertura: object) -> dict:
    return {"provedor": "aws", "resumo": {"cobertura": cobertura, "riscos": []}}


def _codigos(d) -> list[tuple[str, str]]:
    return [(p.nivel, p.codigo) for p in d.problemas]


def test_cost_explorer_e_critico_e_explica_o_caminho_certo() -> None:
    d = diagnosticar_arquivo("AWS_CE_DAILY_BY_SERVICE")
    assert d.nivel == CRITICO
    [p] = d.problemas
    assert p.codigo == "cost_explorer"
    assert "Include resource IDs" in p.texto
    # Tom de orientação, não de erro (feedback de 16/10)
    assert "não serve" not in p.titulo + p.texto


def test_cost_explorer_nao_soma_alertas_de_inventario() -> None:
    d = diagnosticar_analise(
        formato="AWS_CE_DAILY_BY_SERVICE",
        provedor_da_fatura="aws",
        topologia=None,
        cobertura_classificacao=0.012,
    )
    assert _codigos(d) == [(CRITICO, "cost_explorer")]


def test_fatura_sem_id_sem_inventario_so_informa_e_pede_o_inventario() -> None:
    d = diagnosticar_analise(
        formato="AWS_DETAILED_BILLING",
        provedor_da_fatura="aws",
        topologia=None,
        cobertura_classificacao=0.99,
    )
    assert d.nivel == ALERTA
    assert _codigos(d) == [(ALERTA, "sem_inventario"), (INFORMATIVO, "fatura_sem_id")]


def test_fatura_sem_id_com_inventario_avisa_que_o_custo_e_estimado() -> None:
    d = diagnosticar_analise(
        formato="AWS_DETAILED_BILLING",
        provedor_da_fatura="aws",
        topologia=_topologia(custo_com_recurso=0, fracao_encontrada=None, custo_estimado=320),
        cobertura_classificacao=0.99,
    )
    assert _codigos(d) == [(ALERTA, "custo_estimado")]


def test_cur_com_id_e_inventario_completo_nao_tem_aviso() -> None:
    d = diagnosticar_analise(
        formato="AWS_CUR2.0",
        provedor_da_fatura="aws",
        topologia=_topologia(custo_com_recurso=900, fracao_encontrada=0.97),
        cobertura_classificacao=0.98,
    )
    assert d.nivel is None
    assert d.problemas == ()


def test_cur_sem_coluna_de_id_diz_qual_opcao_marcar() -> None:
    [p] = problemas_do_arquivo("AWS_CUR2.0", com_id=False)
    assert p.codigo == "fatura_sem_id"
    assert "Include resource IDs" in p.texto


def test_inventario_parcial_e_classificacao_baixa() -> None:
    topologia = _topologia(
        custo_com_recurso=900,
        fracao_encontrada=0.55,
        lacunas=[{"conta": "333333333333", "regiao": "us-east-1", "custo": 400}],
    )
    d = diagnosticar_analise(
        formato="AWS_CUR2.0",
        provedor_da_fatura="aws",
        topologia=topologia,
        cobertura_classificacao=0.3,
    )
    assert d.nivel == CRITICO
    assert _codigos(d) == [(CRITICO, "classificacao_baixa"), (ALERTA, "inventario_parcial")]
    assert "333333333333" in d.problemas[1].texto


def test_inventario_de_outra_nuvem_e_critico() -> None:
    d = diagnosticar_analise(
        formato="GCP_BILLING_DETAILED",
        provedor_da_fatura="gcp",
        topologia=_topologia(custo_com_recurso=10, fracao_encontrada=1.0),
        cobertura_classificacao=0.99,
    )
    assert (CRITICO, "inventario_de_outra_nuvem") in _codigos(d)


def test_payload_ainda_vazio_diz_so_o_que_o_formato_diz() -> None:
    d = diagnosticar_payload(formato="AWS_DETAILED_BILLING", provedor_da_fatura="aws", payload=None)
    assert _codigos(d) == [(INFORMATIVO, "fatura_sem_id")]
    assert d.nivel is None


def test_formato_desconhecido_nao_inventa_problema() -> None:
    assert diagnosticar_arquivo("FORMATO_NOVO").problemas == ()
