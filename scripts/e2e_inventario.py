#!/usr/bin/env python3
"""E2E do inventário pela API HTTP: billing + inventário → análise → Terraform.

Para cada nuvem: sobe o billing e o inventário, espera os dois, dispara a
análise com `inventory_id`, gera o pacote Terraform para as outras três nuvens e
baixa o ZIP. No fim grava um relatório JSON com o que precisa ser coerente entre
os três artefatos:

- o fingerprint do inventário, o da topologia congelada na análise e o do
  `manifest.json` do pacote são o mesmo;
- a sequência de migração da resposta começa pela fundação (vem das ondas);
- as VMs do `.tf` somam as que a análise aprovou para o destino;
- nenhuma regra de administração ou banco aberta para a internet foi replicada;
- `terraform validate` passou na própria API.

Uso (API com INVENTORY_ENABLED=true):
    PYTHONPATH=. python scripts/gerar_mocks_billing.py --destino /tmp/mocks
    PYTHONPATH=. python scripts/gerar_mocks_inventario.py --billing /tmp/mocks --destino /tmp/inv
    python scripts/e2e_inventario.py --url http://localhost:8000 \\
        --mocks /tmp/mocks --inventarios /tmp/inv
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

_REGIOES = {
    "gcp": "us-central1",
    "aws": "sa-east-1",
    "azure": "brazilsouth",
    "oci": "sa-saopaulo-1",
}
_VM = {
    "aws": "aws_instance",
    "gcp": "google_compute_instance",
    "azure": "azurerm_linux_virtual_machine",
    "oci": "oci_core_instance",
}
_SUBREDE = {
    "aws": "aws_subnet",
    "gcp": "google_compute_subnetwork",
    "azure": "azurerm_subnet",
    "oci": "oci_core_subnet",
}
_GRUPO = {
    "aws": "aws_security_group",
    "gcp": "google_compute_firewall",
    "azure": "azurerm_application_security_group",
    "oci": "oci_core_network_security_group",
}
_PORTAS_DE_RISCO = r"\b(22|3389|5985|5986|5432|3306|1433|1521|6379|27017)\b"
_TERMINAIS = {"done", "failed", "COMPLETED", "FAILED"}
_ESPERA_S = 900


def _casos(mocks: Path, inventarios: Path) -> dict[str, tuple[Path, list[Path]]]:
    return {
        "aws": (
            mocks / "mock_aws_cur2_2026-03.csv.gz",
            sorted((inventarios / "aws_config").glob("*.json.gz")),
        ),
        "gcp": (
            mocks / "mock_gcp_bigquery_2025-12.json.gz",
            [inventarios / "gcp_asset_inventory_2025-12-31.json.gz"],
        ),
        "azure": (
            mocks / "mock_azure_cost_details_2026-03.csv",
            sorted((inventarios / "azure_resource_graph").glob("*.json")),
        ),
        "oci": (
            mocks / "mock_oci_cost_report_2026-03.csv.gz",
            [inventarios / "oci_inventario.json"],
        ),
    }


def _esperar(cliente: httpx.Client, url: str) -> dict[str, Any]:
    limite = time.monotonic() + _ESPERA_S
    while time.monotonic() < limite:
        corpo: dict[str, Any] = cliente.get(url).json()
        if corpo.get("status") in _TERMINAIS:
            return corpo
        time.sleep(0.5)
    raise SystemExit(f"tempo esgotado esperando {url}")


def _blocos(main: str, tipo: str) -> dict[str, str]:
    return {
        m.group(1): main[m.start() : main.index("\n}\n", m.start())]
        for m in re.finditer(rf'resource "{tipo}" "([^"]+)" {{', main)
    }


def _vms_no_tf(main: str, destino: str) -> int:
    total = 0
    for bloco in _blocos(main, _VM[destino]).values():
        m = re.search(r"^\s*count\s*=\s*(\d+)", bloco, re.MULTILINE)
        total += int(m.group(1)) if m else 1
    return total


def _vms_aprovadas(payload: dict[str, Any], destino: str) -> int:
    """A mesma conta do derivador: instâncias do pool, ou as do equivalente."""
    total = 0
    for pool in payload.get("pools_computacao") or []:
        com_sku = [
            e
            for e in pool.get("equivalentes") or []
            if str(e.get("provedor", "")).lower() == destino and e.get("sku")
        ]
        if com_sku:
            total += max(1, int(pool.get("instancias") or com_sku[0].get("instancias") or 1))
    return total


def _risco_replicado(main: str) -> list[str]:
    achados = []
    for tipo, internet in (
        ("aws_vpc_security_group_ingress_rule", r'cidr_ipv4\s*=\s*"0\.0\.0\.0/0"'),
        ("google_compute_firewall", r'source_ranges\s*=\s*\["0\.0\.0\.0/0"'),
        ("oci_core_network_security_group_security_rule", r'source\s*=\s*"0\.0\.0\.0/0"'),
    ):
        for nome, bloco in _blocos(main, tipo).items():
            if re.search(internet, bloco) and re.search(_PORTAS_DE_RISCO, bloco):
                achados.append(nome)
    for nome, nsg in _blocos(main, "azurerm_network_security_group").items():
        for regra in nsg.split("security_rule {")[1:]:
            if '"0.0.0.0/0"' in regra and re.search(_PORTAS_DE_RISCO, regra):
                achados.append(nome)
    return achados


def _pacote(cliente: httpx.Client, base: str, job_id: str, origem: str, destino: str) -> dict:
    r = cliente.post(
        f"{base}/terraform-generations",
        json={
            "analysis_job_id": job_id,
            "project_name": f"e2e-{origem}-{destino}",
            "environment": "prod",
            "target_provider": destino,
            "region": _REGIOES[destino],
            "network": {"mode": "create", "cidr": "10.50.0.0/16", "public_access": False},
            "availability": {"high_availability": True},
        },
    )
    r.raise_for_status()
    gid = r.json()["generation_id"]
    estado = _esperar(cliente, f"{base}/terraform-generations/{gid}")
    info: dict[str, Any] = {"status": estado["status"], "erro": estado.get("error")}
    if estado["status"] != "COMPLETED":
        return info
    zipado = zipfile.ZipFile(
        io.BytesIO(cliente.get(f"{base}/terraform-generations/{gid}/download").content)
    )
    arquivos = {
        Path(n).name: zipado.read(n).decode() for n in zipado.namelist() if not n.endswith("/")
    }
    main = arquivos["main.tf"]
    manifesto = json.loads(arquivos["manifest.json"])
    validacao = json.loads(arquivos["validation.json"])
    info.update(
        {
            "validado": validacao.get("validado"),
            "motivo_sem_validacao": validacao.get("motivo_sem_validacao"),
            "fingerprint_pacote": (manifesto.get("topologia") or {}).get("fingerprint"),
            "vms_tf": _vms_no_tf(main, destino),
            "subredes": len(_blocos(main, _SUBREDE[destino])),
            "grupos_ou_firewalls": len(_blocos(main, _GRUPO[destino])),
            "risco_replicado": _risco_replicado(main),
            "readme_topologia": "## Topologia de origem" in arquivos["README.md"],
            "avisos": len(manifesto.get("avisos") or []),
        }
    )
    return info


def _uma_nuvem(
    cliente: httpx.Client, base: str, origem: str, billing: Path, invs: list[Path]
) -> dict:
    rid = cliente.post(
        f"{base}/billing-files", files={"file": (billing.name, billing.read_bytes())}
    ).json()["job_id"]
    if _esperar(cliente, f"{base}/billing-files/{rid}")["status"] != "done":
        raise SystemExit(f"billing {billing.name} falhou")
    iid = cliente.post(
        f"{base}/inventories", files=[("files", (a.name, a.read_bytes())) for a in invs]
    ).json()["inventory_id"]
    inventario = _esperar(cliente, f"{base}/inventories/{iid}")
    if inventario["status"] != "done":
        raise SystemExit(f"inventário de {origem} falhou: {inventario.get('error')}")
    r = cliente.post(f"{base}/analyses/start/{rid}", json={"inventory_id": iid})
    r.raise_for_status()
    job_id = r.json()["job_id"]
    analise = _esperar(cliente, f"{base}/analyses/{job_id}/result")
    if analise["status"] != "COMPLETED":
        raise SystemExit(f"análise de {origem} falhou: {analise.get('error')}")
    payload = analise["llm_input_payload"]
    topologia = payload["topologia"]
    sequencia = analise["result"]["agent_views"]["risk_migration"]["recommended_migration_sequence"]
    caso: dict[str, Any] = {
        "report_id": rid,
        "inventory_id": iid,
        "analysis_job_id": job_id,
        "fingerprint_inventario": inventario.get("fingerprint"),
        "fingerprint_analise": topologia["fingerprint"],
        "ondas": len(topologia["resumo"]["ondas"]),
        "sequencia_comeca_pela_fundacao": bool(sequencia) and sequencia[0].startswith("Onda 0"),
        "cobertura": topologia["resumo"]["cobertura"]["fracao_encontrada"],
        "recomendado": payload["resumo_executivo"].get("recomendado"),
        "destinos": {},
    }
    for destino in (d for d in _VM if d != origem):
        info = _pacote(cliente, base, job_id, origem, destino)
        info["vms_aprovadas"] = _vms_aprovadas(payload, destino)
        caso["destinos"][destino] = info
    return caso


def _coerente(caso: dict[str, Any]) -> list[str]:
    problemas = []
    if caso["fingerprint_inventario"] != caso["fingerprint_analise"]:
        problemas.append("fingerprint do inventário diferente do da análise")
    if not caso["sequencia_comeca_pela_fundacao"]:
        problemas.append("a sequência de migração não vem das ondas")
    for destino, info in caso["destinos"].items():
        if info["status"] != "COMPLETED":
            problemas.append(f"{destino}: geração {info['status']} ({info['erro']})")
            continue
        if info["fingerprint_pacote"] != caso["fingerprint_analise"]:
            problemas.append(f"{destino}: fingerprint do pacote diferente do da análise")
        if info["vms_tf"] != info["vms_aprovadas"]:
            problemas.append(
                f"{destino}: {info['vms_tf']} VMs no .tf, {info['vms_aprovadas']} aprovadas"
            )
        if info["risco_replicado"]:
            problemas.append(f"{destino}: regra de risco alto replicada {info['risco_replicado']}")
        if info["validado"] is False and not info.get("motivo_sem_validacao"):
            problemas.append(f"{destino}: terraform validate reprovou")
    return problemas


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--token", default="")
    p.add_argument("--mocks", type=Path, required=True)
    p.add_argument("--inventarios", type=Path, required=True)
    p.add_argument("--saida", type=Path, default=Path("e2e_inventario.json"))
    args = p.parse_args()
    cabecalhos = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    base = args.url.rstrip("/") + "/v1"
    saida: dict[str, Any] = {}
    problemas: list[str] = []
    with httpx.Client(timeout=_ESPERA_S, headers=cabecalhos) as cliente:
        for origem, (billing, invs) in _casos(args.mocks, args.inventarios).items():
            caso = _uma_nuvem(cliente, base, origem, billing, invs)
            caso["problemas"] = _coerente(caso)
            problemas += [f"{origem}: {x}" for x in caso["problemas"]]
            saida[origem] = caso
            print(origem, "ok" if not caso["problemas"] else caso["problemas"])  # noqa: T201
    args.saida.write_text(json.dumps(saida, ensure_ascii=False, indent=1))
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
