#!/usr/bin/env python3
"""E2E dos exports detalhados pela API HTTP: upload → análise → Terraform.

Para cada arquivo: sobe o billing, espera a ingestão, dispara a análise (com o
LLM configurado no ambiente), gera o pacote Terraform para as outras nuvens e
baixa o ZIP. No fim grava um relatório JSON com o que precisa ser coerente
entre as camadas — o número do payload, o da resposta e o do pacote.

Serve aos mocks hoje e ao dado real amanhã: é o mesmo caminho.

Uso:
    python scripts/gerar_mocks_billing.py --destino /tmp/mocks
    python scripts/e2e_exports_detalhados.py --url http://localhost:8000 \\
        --arquivo /tmp/mocks/mock_aws_cur2_2026-03.csv.gz --arquivo ...
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from http import HTTPStatus
from pathlib import Path

_AGENTE = "oneshift-e2e/1.0"  # o Cloudflare barra o User-Agent default do urllib
_REGIOES = {
    "gcp": "us-central1",
    "aws": "sa-east-1",
    "azure": "brazilsouth",
    "oci": "sa-saopaulo-1",
}


def _chamar(
    metodo: str,
    url: str,
    token: str,
    *,
    corpo: dict | None = None,
    arquivo: tuple[str, bytes] | None = None,
    bruto: bool = False,
):
    cabecalhos = {"Accept": "application/json", "User-Agent": _AGENTE}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    dados = None
    if arquivo is not None:
        limite = uuid.uuid4().hex
        nome, conteudo = arquivo
        dados = (
            (
                f'--{limite}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{nome}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode()
            + conteudo
            + f"\r\n--{limite}--\r\n".encode()
        )
        cabecalhos["Content-Type"] = f"multipart/form-data; boundary={limite}"
    elif corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    req = urllib.request.Request(  # noqa: S310 - esquema validado em main()
        url, data=dados, headers=cabecalhos, method=metodo
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310  # nosemgrep:python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            conteudo = resp.read()
            return resp.status, (conteudo if bruto else json.loads(conteudo or b"{}"))
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(texto)
        except json.JSONDecodeError:
            return e.code, {"detail": texto[:400]}


def _esperar(url: str, token: str, terminais: set[str], limite_s: int) -> dict:
    inicio = time.time()
    while time.time() - inicio < limite_s:
        st, corpo = _chamar("GET", url, token)
        if st != HTTPStatus.OK:
            raise SystemExit(f"GET {url}: HTTP {st} {str(corpo)[:200]}")
        if str(corpo.get("status", "")) in terminais:
            return corpo
        time.sleep(3)
    raise SystemExit(f"{url}: não terminou em {limite_s}s")


def _um_arquivo(base: str, token: str, caminho: Path, destinos: list[str] | None) -> dict:
    registro: dict = {"arquivo": caminho.name}
    st, up = _chamar(
        "POST", f"{base}/billing-files", token, arquivo=(caminho.name, caminho.read_bytes())
    )
    if st != HTTPStatus.ACCEPTED:
        return {**registro, "erro": f"upload HTTP {st}: {up}"}
    report_id = up["job_id"]
    ingestao = _esperar(f"{base}/billing-files/{report_id}", token, {"done", "failed"}, 900)
    registro["ingestao"] = {
        k: ingestao.get(k) for k in ("status", "provider", "schema_version", "row_count", "error")
    }
    if ingestao["status"] != "done":
        return registro

    st, pedido = _chamar("GET", f"{base}/analyses/{report_id}/request", token)
    payload = (pedido.get("payload") or pedido) if st == HTTPStatus.OK else {}
    premissas = payload.get("premissas") or {}
    registro["payload"] = {
        "gasto_total": payload.get("gasto_total"),
        "moeda": payload.get("moeda"),
        "pools": len(payload.get("pools_computacao") or []),
        "base_de_custo": (premissas.get("base_de_custo") or {}).get("base"),
        "desconto_origem_sobre_lista": (premissas.get("desconto_origem_sobre_lista") or {}).get(
            "fracao"
        ),
        "dimensionamento_medido": premissas.get("dimensionamento_medido"),
        "dimensionamento_estimado": premissas.get("dimensionamento_estimado"),
        "conservacao_bate": (
            (premissas.get("reconciliacao") or {}).get("conservacao_custo") or {}
        ).get("bate"),
        "violacoes": (premissas.get("reconciliacao") or {}).get("violacoes"),
        "inventario_medido": sorted((payload.get("inventario_medido") or {}).keys()),
        "recomendado_backend": (payload.get("resumo_executivo") or {}).get("recomendado"),
    }

    st, analise = _chamar("POST", f"{base}/analyses/start/{report_id}", token)
    if st != HTTPStatus.ACCEPTED:
        return {**registro, "erro": f"análise HTTP {st}: {analise}"}
    job_id = analise["job_id"]
    resultado = _esperar(f"{base}/analyses/{job_id}/result", token, {"COMPLETED", "FAILED"}, 900)
    estruturado = resultado.get("result") or {}
    registro["analise"] = {
        "status": resultado.get("status"),
        "erro": resultado.get("error"),
        "recomendado": estruturado.get("recommended_provider"),
        "recomendacao": estruturado.get("recommendation"),
        "economia_mensal_usd": (estruturado.get("savings_summary") or {}).get(
            "total_monthly_savings_usd"
        ),
        "confianca": estruturado.get("analysis_confidence"),
    }
    if resultado.get("status") != "COMPLETED":
        return registro

    origem = str(ingestao.get("provider") or "").lower()
    registro["terraform"] = []
    for destino in destinos or [d for d in _REGIOES if d != origem]:
        corpo = {
            "analysis_job_id": job_id,
            "project_name": f"e2e-{origem}-{destino}",
            "environment": "prod",
            "target_provider": destino,
            "region": _REGIOES[destino],
            "network": {"mode": "create", "cidr": "10.50.0.0/16", "public_access": False},
            "availability": {"high_availability": True},
        }
        st, geracao = _chamar("POST", f"{base}/terraform-generations", token, corpo=corpo)
        if st != HTTPStatus.ACCEPTED:
            registro["terraform"].append({"destino": destino, "erro": f"HTTP {st}: {geracao}"})
            continue
        fim = _esperar(
            f"{base}/terraform-generations/{geracao['generation_id']}",
            token,
            {"COMPLETED", "FAILED"},
            600,
        )
        st, zip_bytes = _chamar(
            "GET",
            f"{base}/terraform-generations/{geracao['generation_id']}/download",
            token,
            bruto=True,
        )
        arquivos = []
        if st == HTTPStatus.OK:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                arquivos = z.namelist()
                main_tf = next((z.read(n).decode() for n in arquivos if n.endswith("main.tf")), "")
        else:
            main_tf = ""
        validacao = fim.get("validation") or {}
        registro["terraform"].append(
            {
                "destino": destino,
                "status": fim.get("status"),
                "erro": fim.get("error"),
                "validado": validacao.get("validado"),
                "formatado": validacao.get("formatado"),
                "erros_validacao": validacao.get("erros"),
                "motivo_sem_validacao": validacao.get("motivo_sem_validacao"),
                "recursos": main_tf.count("\nresource "),
                "premissas": len(fim.get("assumptions") or []),
                "fora_do_pacote": fim.get("unsupported_items") or [],
                "avisos": len(fim.get("warnings") or []),
                "arquivos": arquivos,
            }
        )
    return registro


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--token", default="")
    p.add_argument("--arquivo", action="append", type=Path, required=True)
    p.add_argument("--destino", action="append", choices=sorted(_REGIOES))
    p.add_argument("--saida", type=Path, default=Path("e2e_exports_detalhados.json"))
    args = p.parse_args()
    if not args.url.startswith(("http://", "https://")):
        raise SystemExit(f"--url precisa ser http(s): {args.url!r}")
    base = args.url.rstrip("/") + "/v1"
    relatorio = [_um_arquivo(base, args.token, a, args.destino) for a in args.arquivo]
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2, default=str))
    campos_tf = ("destino", "status", "validado", "recursos", "premissas", "avisos", "erro")
    for r in relatorio:
        resumo = {k: r.get(k) for k in ("arquivo", "ingestao", "payload", "analise")}
        print(json.dumps(resumo, ensure_ascii=False, default=str))  # noqa: T201
        for t in r.get("terraform") or []:
            print("   ", {k: t.get(k) for k in campos_tf})  # noqa: T201


if __name__ == "__main__":
    sys.exit(main())
