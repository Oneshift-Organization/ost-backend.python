#!/usr/bin/env python3
"""Deixa uma análise pronta para a demo da geração de Terraform.

Sobe um dos billings versionados, espera a ingestão, dispara a análise e espera
ela concluir. No fim imprime o `analysis_job_id` e o corpo pronto para colar no
Swagger.

Fala com a API por **HTTP**, não com o banco. Isso é de propósito: assim o script
usa o provedor de LLM, as chaves e as flags que o ambiente já tem configurados, e
não exige `DATABASE_URL` nem credencial nenhuma na sua máquina. O que ele faz é o
que você faria clicando no Swagger — só que sem esperar olhando.

Uso:
    python scripts/preparar_demo.py --url https://api-dev.oneshift.com.br --token "<jwt>"
    python scripts/preparar_demo.py --url http://localhost:8000 --billing gcp
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "app/tests/integration/fixtures"

_AGENTE = "oneshift-demo/1.0"

BILLINGS = {
    "aws": ("aws_cost_explorer_full.csv", "sa-east-1", "gcp", "southamerica-east1"),
    "gcp": ("gcp_cost_table_full.csv", "us-central1", "aws", "sa-east-1"),
}

# Estados terminais de cada etapa. A ingestão responde em minúsculas e a análise
# em MAIÚSCULAS — é assim na API, e normalizar aqui esconderia a diferença de
# quem for ler o Swagger depois.
_FIM_INGESTAO = {"done", "failed"}
_FIM_ANALISE = {"COMPLETED", "FAILED"}


def _chamar(
    metodo: str,
    url: str,
    token: str,
    corpo: dict | None = None,
    arquivo: tuple[str, bytes] | None = None,
) -> tuple[int, dict]:
    # O User-Agent não é cosmético: o Cloudflare na frente da API responde
    # **403 (erro 1010)** ao `Python-urllib/3.x` default, e o script morre antes
    # de chegar na aplicação. Mesma armadilha do cliente de câmbio.
    cabecalhos = {"Accept": "application/json", "User-Agent": _AGENTE}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"

    if arquivo is not None:
        limite = uuid.uuid4().hex
        nome, conteudo = arquivo
        dados = (
            f"--{limite}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{nome}"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode() + conteudo + f"\r\n--{limite}--\r\n".encode()
        cabecalhos["Content-Type"] = f"multipart/form-data; boundary={limite}"
    elif corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    else:
        dados = None

    req = urllib.request.Request(  # noqa: S310 - esquema validado em main()
        url, data=dados, headers=cabecalhos, method=metodo
    )
    try:
        # Esquema validado em main(): só http(s) chega aqui.
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310  # nosemgrep:python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(corpo_erro)
        except json.JSONDecodeError:
            return e.code, {"detail": corpo_erro[:400]}


def _esperar(
    rotulo: str, url: str, token: str, terminais: set[str], limite_s: int
) -> dict:
    """Polling com deadline. Sem webhook, é o que a API oferece."""
    inicio = time.time()
    ultimo = ""
    while time.time() - inicio < limite_s:
        st, corpo = _chamar("GET", url, token)
        if st != HTTPStatus.OK:
            detalhe = json.dumps(corpo, ensure_ascii=False)[:200]
            raise SystemExit(f"  ✗ {rotulo}: HTTP {st} — {detalhe}")
        estado = str(corpo.get("status", ""))
        if estado != ultimo:
            print(f"  … {rotulo}: {estado}")
            ultimo = estado
        if estado in terminais:
            return corpo
        time.sleep(3)
    raise SystemExit(f"  ✗ {rotulo}: não concluiu em {limite_s}s")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000", help="base da API")
    p.add_argument("--token", default="", help="JWT do Supabase (omita se AUTH_ENABLED=false)")
    p.add_argument("--billing", choices=sorted(BILLINGS), default="aws")
    p.add_argument("--timeout-analise", type=int, default=600)
    args = p.parse_args()

    # A URL vem da linha de comando e vai direto para `urlopen`, que aceitaria
    # `file://` e leria arquivo local. Só HTTP(S) faz sentido aqui.
    if not args.url.startswith(("http://", "https://")):
        raise SystemExit(f"✗ --url precisa ser http(s): {args.url!r}")
    base = args.url.rstrip("/") + "/v1"
    arquivo, _, destino_sugerido, regiao_sugerida = BILLINGS[args.billing]
    conteudo = (FIXTURES / arquivo).read_bytes()

    print(f"\nAPI: {base}")
    print(f"Billing: {arquivo} ({len(conteudo):,} bytes, origem {args.billing.upper()})\n")

    st, saude = _chamar("GET", f"{base}/health", args.token)
    if st != HTTPStatus.OK:
        raise SystemExit(f"✗ API não respondeu: HTTP {st}")
    print(f"1. health            {saude}")

    st, up = _chamar("POST", f"{base}/billing-files", args.token, arquivo=(arquivo, conteudo))
    if st != HTTPStatus.ACCEPTED:
        raise SystemExit(f"✗ upload: HTTP {st} — {json.dumps(up, ensure_ascii=False)[:300]}")
    report_id = up["job_id"]
    print(f"2. upload            202  report_id={report_id}")

    ing = _esperar("ingestão", f"{base}/billing-files/{report_id}", args.token, _FIM_INGESTAO, 300)
    if ing["status"] != "done":
        raise SystemExit(f"✗ ingestão falhou: {ing.get('error')}")
    print(f"3. ingestão          done  {ing.get('row_count')} linhas")

    st, an = _chamar("POST", f"{base}/analyses/start/{report_id}", args.token)
    if st != HTTPStatus.ACCEPTED:
        raise SystemExit(f"✗ análise: HTTP {st} — {json.dumps(an, ensure_ascii=False)[:300]}")
    analysis_job_id = an["job_id"]
    print(f"4. análise           202  analysis_job_id={analysis_job_id}")

    res = _esperar(
        "análise",
        f"{base}/analyses/{analysis_job_id}/result",
        args.token,
        _FIM_ANALISE,
        args.timeout_analise,
    )
    if res["status"] != "COMPLETED":
        raise SystemExit(f"✗ análise falhou: {res.get('error')}")

    estruturado = res.get("result") or {}
    print("5. análise           COMPLETED")
    if estruturado:
        print(f"   recomendado: {estruturado.get('recommended_provider') or '—'}"
              f"  |  confiança: {estruturado.get('analysis_confidence') or '—'}")

    corpo_tf = {
        "analysis_job_id": analysis_job_id,
        "project_name": f"demo-{args.billing}-para-{destino_sugerido}",
        "environment": "prod",
        "target_provider": destino_sugerido,
        "region": regiao_sugerida,
        "network": {"mode": "create", "cidr": "10.50.0.0/16", "public_access": False},
        "availability": {"high_availability": True},
        "tags": {"managed_by": "oneshift"},
    }

    print("\n" + "═" * 72)
    print("PRONTO. No Swagger, em POST /v1/terraform-generations, cole:")
    print("═" * 72)
    print(json.dumps(corpo_tf, indent=2, ensure_ascii=False))
    print("═" * 72)
    print("\nDepois:")
    print("  GET  /v1/terraform-generations/{generation_id}           → espere COMPLETED")
    print("  GET  /v1/terraform-generations/{generation_id}/download  → baixa o ZIP")
    print("\nTrocar a nuvem de destino: `target_provider` aceita gcp, aws, azure, oci.")
    print("Ao trocar, ajuste `region` para uma região daquela nuvem.\n")


if __name__ == "__main__":
    sys.exit(main())
