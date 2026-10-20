"""M11: o item que custa zero no destino sai com o nome da categoria para a tela."""

from __future__ import annotations

from app.use_cases.analysis.run_analysis_pipeline import _com_rotulos_do_gratis


def test_item_gratis_ganha_o_rotulo_da_categoria() -> None:
    resumo = {
        "por_provedor": [
            {
                "provedor": "oci",
                "itens_gratis": [{"categoria": "nat_gateway", "economia_mensal": 30}],
            },
            {"provedor": "gcp"},
        ]
    }
    oci, gcp = _com_rotulos_do_gratis(resumo)["por_provedor"]
    assert oci["itens_gratis"][0]["rotulo"] == "NAT"
    assert gcp == {"provedor": "gcp"}


def test_categoria_desconhecida_fica_com_o_nome_interno() -> None:
    resumo = {"por_provedor": [{"itens_gratis": [{"categoria": "nova_coisa"}]}]}
    assert _com_rotulos_do_gratis(resumo)["por_provedor"][0]["itens_gratis"][0]["rotulo"] == (
        "nova_coisa"
    )
