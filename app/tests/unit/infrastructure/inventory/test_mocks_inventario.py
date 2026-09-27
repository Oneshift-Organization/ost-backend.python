"""Invariantes dos mocks de inventário (I3 do PLANO_INVENTARIO_API).

O mock só vale se for coerente com o billing mock: os mesmos recursos, com os
mesmos IDs, e topologia que o normalizador lê sem referência pendente. Estas
checagens usam o arquivo de billing bruto, não o parser de billing: se o parser
mudar, a coerência continua sendo medida contra a origem.
"""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from app.domain.entities.topologia import EstadoDoRecurso, TipoDeRecurso
from app.infrastructure.inventory_ingestion.processar import normalizar_arquivos
from app.tests.conftest import gerar_mocks_inventario

_FORMATOS = {
    "aws": "aws_config",
    "gcp": "gcp_asset_inventory",
    "azure": "azure_resource_graph",
    "oci": "oneshift_canonico",
}
# Motivos de contagem que, num mock coerente, não podem aparecer.
_INCOERENCIA = ("referencia_externa", "relacao_invalida", "grupo_externo", "regra_sem_grupo")


def _normalizar(arquivos: list[Path]):
    return normalizar_arquivos([(a.name, a) for a in arquivos])


@pytest.fixture(scope="module")
def inventarios(mocks_inventario):
    return {p: _normalizar(getattr(mocks_inventario, p)) for p in ("aws", "gcp", "azure", "oci")}


def _apelidos(inventario) -> set[str]:
    return {a for r in inventario.recursos for a in r.apelidos}


@pytest.mark.parametrize("provedor", ["aws", "gcp", "azure", "oci"])
def test_formato_oficial_e_nenhuma_incoerencia(inventarios, provedor) -> None:
    resultado = inventarios[provedor]
    assert set(resultado.formatos.values()) == {_FORMATOS[provedor]}
    assert resultado.inventario.provedor == provedor
    ruins = {k: v for k, v in resultado.inventario.contagens.items() if k.startswith(_INCOERENCIA)}
    assert ruins == {}


@pytest.mark.parametrize("provedor", ["aws", "gcp", "azure", "oci"])
def test_todo_recurso_do_billing_incluido_esta_no_inventario(
    inventarios, mocks_inventario, provedor
) -> None:
    resumo = mocks_inventario.resumos[provedor]
    faltando = {
        i
        for i in resumo.ids_billing_incluidos
        if i.lower() not in _apelidos(inventarios[provedor].inventario)
    }
    assert faltando == set()
    assert resumo.ids_billing_incluidos  # não é vazio por engano


def test_conta_fora_do_snapshot_nao_aparece(inventarios, mocks_inventario) -> None:
    resumo = mocks_inventario.resumos["aws"]
    inv = inventarios["aws"].inventario
    assert resumo.ids_billing_fora
    assert not {i.lower() for i in resumo.ids_billing_fora} & _apelidos(inv)
    assert "333333333333" not in {r.conta for r in inv.recursos}


def test_ids_do_billing_bruto_batem(mocks_billing, mocks_inventario) -> None:
    """Os IDs incluídos são mesmo os do arquivo de billing (não uma cópia)."""
    with gzip.open(mocks_billing.cur, "rt", newline="", encoding="utf-8") as fh:
        do_cur = {r["line_item_resource_id"] for r in csv.DictReader(fh)} - {""}
    resumo = mocks_inventario.resumos["aws"]
    assert resumo.ids_billing_incluidos | resumo.ids_billing_fora == do_cur

    with gzip.open(mocks_billing.bigquery, "rt", encoding="utf-8") as fh:
        do_bq = {(json.loads(linha).get("resource") or {}).get("global_name") for linha in fh} - {
            None
        }
    assert mocks_inventario.resumos["gcp"].ids_billing_incluidos == do_bq


def test_topologia_de_proposito_na_aws(inventarios) -> None:
    inv = inventarios["aws"].inventario
    por_app = {r.tags.get("app") for r in inv.recursos if r.tipo is TipoDeRecurso.INSTANCIA}
    assert {"bastion", "monitoramento"} <= por_app
    assert any(r.estado is EstadoDoRecurso.PARADO for r in inv.recursos)
    assert any(
        r.tipo is TipoDeRecurso.DISCO and r.estado is EstadoDoRecurso.DISPONIVEL
        for r in inv.recursos
    )
    assert any(r.atributos.get("boot") for r in inv.recursos if r.tipo is TipoDeRecurso.DISCO)
    bancos = [r for r in inv.recursos if r.tipo is TipoDeRecurso.BANCO]
    assert all(r.atributos.get("versao") for r in bancos)
    assert any(r.atributos.get("publico") for r in bancos)
    assert any(r.tags.get("criticality") for r in inv.recursos)


def test_capturas_alinhadas_com_o_periodo_do_billing(inventarios) -> None:
    assert inventarios["aws"].inventario.capturado_em.isoformat().startswith("2026-03-31")
    assert inventarios["oci"].inventario.capturado_em.isoformat().startswith("2026-03-31")
    assert inventarios["gcp"].inventario.capturado_em.isoformat().startswith("2025-12-31")
    # O Resource Graph não diz quando foi capturado.
    assert inventarios["azure"].inventario.capturado_em is None


def test_azure_ids_com_caixa_mista_casam_com_o_billing(mocks_inventario) -> None:
    pagina = json.loads(mocks_inventario.azure[0].read_text())
    ids = [linha["id"] for linha in pagina["data"]]
    assert any("resourceGroups" in i for i in ids)  # grafia do Resource Graph
    assert any("Microsoft.Compute/virtualMachines" in i for i in ids)


def test_geracao_e_deterministica(mocks_billing, mocks_inventario, tmp_path) -> None:
    de_novo = gerar_mocks_inventario(mocks_billing, tmp_path)
    for provedor in ("aws", "gcp", "azure", "oci"):
        antes = [a.read_bytes() for a in getattr(mocks_inventario, provedor)]
        depois = [a.read_bytes() for a in getattr(de_novo, provedor)]
        assert antes == depois, provedor
