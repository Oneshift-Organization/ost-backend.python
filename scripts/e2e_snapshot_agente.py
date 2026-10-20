#!/usr/bin/env python3
"""E2E do snapshot do agente pela API HTTP (PLANO_SNAPSHOT_AGENTE, F3).

Faz o caminho que o MCP vai fazer, como um cliente externo: sobe o billing (e o
inventário, quando há), prepara o snapshot, prepara de novo, envia a análise,
repete o envio, manda uma revisão e consulta. Confere a coerência entre os
artefatos:

- o `payload` do snapshot é igual ao do `GET /request?recalcular=true`;
- o `payload_fingerprint` bate com o SHA-256 do JSON canônico do `payload`,
  recalculado aqui, sem código do backend (é o que o MCP pode fazer);
- a segunda preparação reaproveita o mesmo snapshot;
- a análise corrigida traz a decisão do payload lido e o parecer intacto;
- a repetição devolve o mesmo corpo, com `Idempotent-Replayed`;
- o corpo novo vira a revisão 2, e a consulta mostra as duas.

Com `--exemplos`, grava a resposta real de cada chamada (e dos erros) dos
casos com inventário, para o time do MCP montar os mocks.

Uso (API com AGENT_SNAPSHOT_ENABLED=true e INVENTORY_ENABLED=true):
    PYTHONPATH=. python scripts/gerar_mocks_billing.py --destino /tmp/mocks
    PYTHONPATH=. python scripts/gerar_mocks_inventario.py --billing /tmp/mocks --destino /tmp/inv
    python scripts/e2e_snapshot_agente.py --url http://localhost:8000 \\
        --mocks /tmp/mocks --inventarios /tmp/inv --exemplos /tmp/exemplos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

_RAIZ = Path(__file__).resolve().parents[1]
_FIXTURES = _RAIZ / "app/tests/integration/fixtures"
_TERMINAIS = {"done", "failed"}
_CRIADO, _OK = 201, 200
_SEGUNDA_REVISAO = 2
_ESPERA_S = 600

_ENVIO = {
    "recommendation": "migrar para a nuvem mais barata",
    "recommendation_rationale": "a economia mensal compensa o esforço",
    "strategic_notes": ["negociar desconto de compromisso antes de migrar"],
    "action_plan": ["validar compliance", "migrar a fundação de rede", "migrar as cargas"],
    "provider_comparison": [
        {"provider": "oci", "strengths": ["preço"], "weaknesses": ["ecossistema menor"]}
    ],
    "sugestoes": [
        {
            "tipo": "contestacao_preco",
            "severidade": "alta",
            "titulo": "Preço de referência possivelmente defasado",
            "detalhe": "O valor parece abaixo do preço de tabela atual.",
            "referencia": "pool:exemplo",
        }
    ],
    "payback": {
        "meses": 7,
        "premissa_custo_migracao_usd": 18000,
        "base": "esforço estimado de engenharia",
    },
    "parecer": {
        "posicao": "diverge",
        "motivo": "A economia depende de cargas com lock-in alto.",
        "riscos": ["residência de dados"],
    },
}


def _canonico(valor: Any) -> str:
    """JSON canônico do contrato: chaves ordenadas, sem espaços, UTF-8."""
    return json.dumps(
        valor, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _sha256(valor: Any) -> str:
    return hashlib.sha256(_canonico(valor).encode("utf-8")).hexdigest()


def _casos(mocks: Path, inventarios: Path) -> dict[str, tuple[Path, list[Path]]]:
    return {
        "aws_cost_explorer": (_FIXTURES / "aws_cost_explorer_full.csv", []),
        "gcp_cost_table": (_FIXTURES / "gcp_cost_table_full.csv", []),
        "gcp_bigquery_com_inventario": (
            mocks / "mock_gcp_bigquery_2025-12.json.gz",
            [inventarios / "gcp_asset_inventory_2025-12-31.json.gz"],
        ),
        "oci_com_inventario": (
            mocks / "mock_oci_cost_report_2026-03.csv.gz",
            [inventarios / "oci_inventario.json"],
        ),
    }


def _esperar(c: httpx.Client, url: str) -> dict[str, Any]:
    limite = time.monotonic() + _ESPERA_S
    while time.monotonic() < limite:
        corpo: dict[str, Any] = c.get(url).json()
        if corpo.get("status") in _TERMINAIS:
            return corpo
        time.sleep(0.5)
    raise SystemExit(f"tempo esgotado esperando {url}")


def _registro(r: httpx.Response) -> dict[str, Any]:
    cabecalhos = {k: v for k, v in r.headers.items() if k.lower() == "idempotent-replayed"}
    return {
        "request": {"method": r.request.method, "url": str(r.request.url.raw_path.decode())},
        "status": r.status_code,
        "headers": cabecalhos,
        "body": r.json(),
    }


def _um_caso(
    c: httpx.Client, base: str, billing: Path, invs: list[Path]
) -> tuple[dict[str, Any], dict[str, Any]]:
    rid = c.post(f"{base}/billing-files", files={"file": (billing.name, billing.read_bytes())})
    report_id = rid.json()["job_id"]
    if _esperar(c, f"{base}/billing-files/{report_id}")["status"] != "done":
        raise SystemExit(f"billing {billing.name} falhou")
    pedido: dict[str, Any] = {"report_id": report_id}
    if invs:
        r = c.post(f"{base}/inventories", files=[("files", (a.name, a.read_bytes())) for a in invs])
        iid = r.json()["inventory_id"]
        if _esperar(c, f"{base}/inventories/{iid}")["status"] != "done":
            raise SystemExit(f"inventário de {billing.name} falhou")
        pedido["inventory_id"] = iid

    chamadas: dict[str, httpx.Response] = {}
    chamadas["preparacao"] = c.post(f"{base}/agent/snapshots", json=pedido)
    chamadas["reaproveitamento"] = c.post(f"{base}/agent/snapshots", json=pedido)
    snapshot = chamadas["preparacao"].json()
    sid = snapshot["snapshot"]["snapshot_id"]
    qs = f"?inventory_id={pedido['inventory_id']}" if invs else "?recalcular=true"
    request = c.get(f"{base}/analyses/{report_id}/request{qs}").json()
    request.pop("_meta", None)

    envio_url = f"{base}/agent/snapshots/{sid}/submissions"
    chamadas["envio"] = c.post(envio_url, json=_ENVIO)
    chamadas["repeticao"] = c.post(envio_url, json=_ENVIO)
    chamadas["revisao"] = c.post(envio_url, json={**_ENVIO, "action_plan": ["revisão 2"]})
    envio_id = chamadas["envio"].json()["submission"]["submission_id"]
    chamadas["consulta_por_id"] = c.get(f"{base}/agent/submissions/{envio_id}")
    chamadas["lista_por_relatorio"] = c.get(f"{base}/agent/submissions?report_id={report_id}")
    chamadas["erro_snapshot_inexistente"] = c.post(
        f"{base}/agent/snapshots/{uuid.uuid4()}/submissions", json=_ENVIO
    )
    chamadas["erro_report_inexistente"] = c.post(
        f"{base}/agent/snapshots", json={"report_id": str(uuid.uuid4())}
    )
    chamadas["erro_corpo_invalido"] = c.post(
        envio_url, json={**_ENVIO, "payback": {"meses": 7, "base": "sem premissa"}}
    )

    s = snapshot["snapshot"]
    envio = chamadas["envio"].json()
    analise = envio["analysis"]
    decisao = snapshot["payload"].get("decision_support") or {}
    escolhido = decisao.get("selected_provider")
    recomendacao = (
        f"Avaliar {escolhido.upper()}. {decisao.get('selection_reason')}"
        if escolhido
        else decisao.get("selection_reason")
    )
    lista = chamadas["lista_por_relatorio"].json()
    checagens = {
        "status": [r.status_code for r in chamadas.values()],
        "preparacao_201_reaproveitamento_200": (
            chamadas["preparacao"].status_code,
            chamadas["reaproveitamento"].status_code,
        )
        == (_CRIADO, _OK),
        "mesmo_snapshot_na_segunda_preparacao": (
            chamadas["reaproveitamento"].json()["snapshot"]["snapshot_id"] == sid
        ),
        "payload_igual_ao_request": snapshot["payload"] == request,
        "payload_fingerprint_confere": s["payload_fingerprint"] == _sha256(snapshot["payload"]),
        "decisao_do_payload_lido": analise.get("decision_support") == decisao,
        "recomendacao_do_backend": analise.get("recommendation") == recomendacao,
        "parecer_intacto": analise.get("parecer_agente") == _ENVIO["parecer"],
        "envio_201": chamadas["envio"].status_code == _CRIADO,
        "repeticao_200_com_cabecalho": (
            chamadas["repeticao"].status_code == _OK
            and chamadas["repeticao"].headers.get("Idempotent-Replayed") == "true"
        ),
        "repeticao_com_o_mesmo_corpo": chamadas["repeticao"].json() == envio,
        "revisao_2": chamadas["revisao"].json()["submission"]["revision"] == _SEGUNDA_REVISAO,
        "consulta_igual_ao_envio": chamadas["consulta_por_id"].json() == envio,
        "lista_com_as_duas_revisoes": [e["submission"]["revision"] for e in lista] == [2, 1],
        "erros": [
            chamadas[k].status_code
            for k in ("erro_snapshot_inexistente", "erro_report_inexistente", "erro_corpo_invalido")
        ]
        == [404, 404, 422],
        "tamanho_do_payload_kb": round(len(_canonico(snapshot["payload"])) / 1024, 1),
        "versoes": s["versions"],
        "campos_sobrescritos": envio["submission"]["campos_sobrescritos"],
    }
    return checagens, {k: _registro(r) for k, r in chamadas.items()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--token", default="")
    p.add_argument("--mocks", type=Path, required=True)
    p.add_argument("--inventarios", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=Path("e2e_snapshot_agente.json"))
    p.add_argument("--exemplos", type=Path, default=None)
    args = p.parse_args()
    cabecalhos = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    base = args.url.rstrip("/") + "/v1"
    saida: dict[str, Any] = {}
    problemas: list[str] = []
    with httpx.Client(timeout=_ESPERA_S, headers=cabecalhos) as c:
        for nome, (billing, invs) in _casos(args.mocks, args.inventarios).items():
            checagens, registros = _um_caso(c, base, billing, invs)
            falhas = [k for k, v in checagens.items() if v is False]
            problemas += [f"{nome}: {k}" for k in falhas]
            saida[nome] = checagens
            print(nome, falhas or "ok")  # noqa: T201
            if args.exemplos and invs:
                pasta = args.exemplos / nome
                pasta.mkdir(parents=True, exist_ok=True)
                for chave, registro in registros.items():
                    (pasta / f"{chave}.json").write_text(
                        json.dumps(registro, ensure_ascii=False, indent=2)
                    )
    args.saida.write_text(json.dumps(saida, ensure_ascii=False, indent=1))
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
