"""Diagnóstico de entrada (PLANO_CENARIOS_DE_ENTRADA).

O aviso da lista acende só para o que o usuário resolve mandando outro arquivo.
Limite aceitável do arquivo é informativo; o que só nós resolvemos não entra.
"""

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


def test_cost_explorer_e_critico_e_orienta_sem_acusar() -> None:
    d = diagnosticar_arquivo("AWS_CE_DAILY_BY_SERVICE")
    assert d.nivel == CRITICO
    [p] = d.problemas
    assert p.codigo == "cost_explorer"
    assert "Include resource IDs" in p.texto
    assert "não serve" not in p.titulo + p.texto


def test_cost_explorer_nao_soma_avisos_de_inventario() -> None:
    d = diagnosticar_analise(
        formato="AWS_CE_DAILY_BY_SERVICE", provedor_da_fatura="aws", topologia=None
    )
    assert _codigos(d) == [(CRITICO, "cost_explorer")]


def test_sem_inventario_e_informativo_e_nao_acende_o_aviso() -> None:
    d = diagnosticar_analise(
        formato="AWS_DETAILED_BILLING", provedor_da_fatura="aws", topologia=None
    )
    assert d.nivel is None
    assert _codigos(d) == [(INFORMATIVO, "fatura_sem_id"), (INFORMATIVO, "sem_inventario")]


def test_custo_calculado_pelo_tipo_e_informativo() -> None:
    d = diagnosticar_analise(
        formato="AWS_DETAILED_BILLING",
        provedor_da_fatura="aws",
        topologia=_topologia(custo_com_recurso=0, fracao_encontrada=None, custo_estimado=320),
    )
    assert d.nivel is None
    assert _codigos(d) == [(INFORMATIVO, "custo_estimado")]


def test_cur_com_id_e_inventario_completo_nao_tem_aviso() -> None:
    d = diagnosticar_analise(
        formato="AWS_CUR2.0",
        provedor_da_fatura="aws",
        topologia=_topologia(custo_com_recurso=900, fracao_encontrada=0.97),
    )
    assert d.nivel is None
    assert d.problemas == ()


def test_cur_sem_coluna_de_id_diz_qual_opcao_marcar() -> None:
    [p] = problemas_do_arquivo("AWS_CUR2.0", com_id=False)
    assert p.codigo == "fatura_sem_id"
    assert "Include resource IDs" in p.texto


def test_contas_fora_do_inventario_sao_alerta_com_o_que_enviar() -> None:
    topologia = _topologia(
        custo_com_recurso=900,
        fracao_encontrada=0.55,
        lacunas=[{"conta": "333333333333", "regiao": "us-east-1", "custo": 400}],
    )
    d = diagnosticar_analise(formato="AWS_CUR2.0", provedor_da_fatura="aws", topologia=topologia)
    assert d.nivel == ALERTA
    [p] = d.problemas
    assert (p.codigo, p.titulo) == ("inventario_parcial", "Faltam contas no inventário")
    assert "333333333333" in p.texto


def test_inventario_de_outra_nuvem_e_critico() -> None:
    d = diagnosticar_analise(
        formato="GCP_BILLING_DETAILED",
        provedor_da_fatura="gcp",
        topologia=_topologia(custo_com_recurso=10, fracao_encontrada=1.0),
    )
    assert (CRITICO, "inventario_de_outra_nuvem") in _codigos(d)


def test_gasto_nao_classificado_nao_vira_aviso_do_usuario() -> None:
    """A cobertura da classificação é nossa: o usuário não tem o que fazer com ela."""
    d = diagnosticar_payload(
        formato="AWS_CUR2.0",
        provedor_da_fatura="aws",
        payload={
            "premissas": {"cobertura_classificacao": 0.3},
            "topologia": _topologia(custo_com_recurso=900, fracao_encontrada=0.97),
        },
    )
    assert d.problemas == ()


def test_payload_ainda_vazio_diz_so_o_que_o_formato_diz() -> None:
    d = diagnosticar_payload(formato="AWS_DETAILED_BILLING", provedor_da_fatura="aws", payload=None)
    assert _codigos(d) == [(INFORMATIVO, "fatura_sem_id")]
    assert d.nivel is None


def test_formato_desconhecido_nao_inventa_problema() -> None:
    assert diagnosticar_arquivo("FORMATO_NOVO").problemas == ()
