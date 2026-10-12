"""Versões do snapshot do agente e a trava das regras de autoridade (D13).

A trava existe porque o envio aplica a autoridade **atual** sobre um payload
**congelado**. Se as regras mudarem e a versão não subir, um snapshot preparado
antes do deploy seria corrigido depois por outras regras, em silêncio.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

from app.use_cases.agent import versoes
from app.use_cases.agent.versoes import (
    AUTHORITY_VERSION,
    divergencias,
    politica_de_decisao_atual,
    versoes_do_payload,
)
from app.use_cases.analysis import topologia_no_payload

_RAIZ = Path(__file__).resolve().parents[4]

# O código que roda no envio, entre o payload congelado e a análise corrigida.
_ARQUIVOS_DA_AUTORIDADE = (
    "app/use_cases/analysis/agent_result.py",
    "app/domain/services/authority.py",
    "app/domain/services/confidence.py",
    "app/use_cases/analysis/decision_support.py",
    "app/domain/services/decision_support.py",
    "app/use_cases/analysis/backend_authority.py",
    "app/use_cases/analysis/equivalence_projection.py",
    "app/use_cases/analysis/equivalence_pricing.py",
    "app/domain/services/migration_strategy.py",
    "app/domain/services/service_identity.py",
    "app/domain/services/sku_normalization.py",
    "app/schemas/analysis_v2.py",
    "app/schemas/decision_support.py",
)
# Do `topologia_no_payload`, só o que roda no envio. O resto do arquivo monta o
# payload na preparação, e mudança lá já gera snapshot novo pelo fingerprint.
_FUNCOES_DA_TOPOLOGIA = (
    topologia_no_payload.impor_topologia,
    topologia_no_payload.sequencia_de_migracao,
)
_MODULO_DA_TOPOLOGIA = "app.use_cases.analysis.topologia_no_payload"

# Hash do código acima para cada versão já publicada. Ao mudar as regras:
# - se a mesma entrada passa a produzir outra análise corrigida, suba
#   AUTHORITY_VERSION em `use_cases/agent/versoes.py` e registre o hash novo;
# - se não (refactor, comentário, tipo), só troque o hash da versão atual.
_HASH_POR_VERSAO = {
    "agent-authority-v1": "1d3e1f935092a2260a5c740b8b1b94c2ec9319faffa5eaf078a31d7bcea4d8c6",
}


def _hash_das_regras() -> str:
    h = hashlib.sha256()
    for caminho in _ARQUIVOS_DA_AUTORIDADE:
        h.update(caminho.encode())
        h.update((_RAIZ / caminho).read_bytes())
    for funcao in _FUNCOES_DA_TOPOLOGIA:
        h.update(inspect.getsource(funcao).encode())
    h.update(topologia_no_payload.VERSAO.encode())
    return h.hexdigest()


def test_regras_de_autoridade_nao_mudam_sem_a_versao() -> None:
    atual = _hash_das_regras()
    assert _HASH_POR_VERSAO.get(AUTHORITY_VERSION) == atual, (
        "As regras de autoridade do envio do agente mudaram "
        f"(hash {atual}). Se a mesma entrada passa a produzir outra análise "
        "corrigida, suba AUTHORITY_VERSION em app/use_cases/agent/versoes.py e "
        "registre o hash novo em _HASH_POR_VERSAO; senão, só atualize o hash da "
        "versão atual. Ver PLANO_SNAPSHOT_AGENTE, D13."
    )


def test_a_lista_da_trava_cobre_os_imports() -> None:
    """Um import novo nas regras precisa entrar na lista, ou a trava não o vê."""
    cobertos = {c.removesuffix(".py").replace("/", ".") for c in _ARQUIVOS_DA_AUTORIDADE} | {
        _MODULO_DA_TOPOLOGIA
    }
    fora: dict[str, set[str]] = {}
    for caminho in _ARQUIVOS_DA_AUTORIDADE:
        arvore = ast.parse((_RAIZ / caminho).read_text())
        for no in ast.walk(arvore):
            modulo = no.module if isinstance(no, ast.ImportFrom) else None
            if modulo and modulo.startswith("app.") and modulo not in cobertos:
                fora.setdefault(caminho, set()).add(modulo)
    assert not fora, f"módulos fora da trava de autoridade: {fora}"


def test_versoes_de_um_payload_com_decisao_e_topologia() -> None:
    payload = {
        "decision_support": {"policy_version": "score-7rs-v3"},
        "topologia": {"versao": topologia_no_payload.VERSAO},
    }
    assert versoes_do_payload(payload) == {
        "contract": versoes.CONTRACT_VERSION,
        "decision_policy": "score-7rs-v3",
        "topology": topologia_no_payload.VERSAO,
        "authority": AUTHORITY_VERSION,
    }


def test_sem_inventario_a_topologia_fica_nula() -> None:
    assert versoes_do_payload({"decision_support": {"policy_version": "x"}})["topology"] is None


def test_snapshot_do_codigo_atual_nao_diverge() -> None:
    v = versoes_do_payload(
        {
            "decision_support": {"policy_version": politica_de_decisao_atual()},
            "topologia": {"versao": topologia_no_payload.VERSAO},
        }
    )
    assert divergencias(v) == []


def test_politica_de_decisao_anterior_diverge() -> None:
    # O caso real: score-7rs-v2 → v3 (#30), entre a preparação e o envio.
    v = versoes_do_payload({"decision_support": {"policy_version": "score-7rs-v2"}})
    assert divergencias(v) == ["decision_policy"]


def test_autoridade_e_topologia_anteriores_divergem() -> None:
    v = {
        "authority": "agent-authority-v0",
        "decision_policy": politica_de_decisao_atual(),
        "topology": "oneshift-topologia-v0",
    }
    assert divergencias(v) == ["authority", "topology"]


def test_contrato_nao_entra_na_comparacao() -> None:
    # A forma da representação entregue não muda a correção.
    v = versoes_do_payload({"decision_support": {"policy_version": politica_de_decisao_atual()}})
    v["contract"] = "agent-snapshot-v0"
    assert divergencias(v) == []
