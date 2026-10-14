"""Monta o mock da tela de Dependências com os dados reais da análise local."""

import base64
import json
from pathlib import Path

AQUI = Path(__file__).parent
RAIZ = AQUI.parent / "dados"
LOGOS = Path("/var/home/Androw/Documentos/Next/ost-frontend.nextjs/public/landing/clouds")

sc = json.loads((RAIZ / "sc_inv.json").read_text())
res = json.loads((RAIZ / "res_inv.json").read_text())["result"]
eq = res["cross_cloud_equivalents"]

por_rotulo: dict[str, list[dict]] = {}
for row in eq:
    por_rotulo.setdefault(row["workload"], []).append(row)


def estimativas(rotulos: list[str]) -> dict[str, float]:
    soma: dict[str, float] = {}
    for rotulo in rotulos:
        for row in por_rotulo.get(rotulo, []):
            if row["estimated_monthly_cost"] is None:
                continue
            p = row["target_provider"].lower()
            soma[p] = soma.get(p, 0) + row["estimated_monthly_cost"]
    return {k: round(v, 2) for k, v in soma.items()}


servicos = []
for s in sc["servicos"]:
    pools = []
    for p in s["pools"]:
        rotulo = f"{p['familia']} · {p['regiao'] or 'global'}"
        alvos = {
            r["target_provider"].lower(): {
                "sku": r["target_sku"],
                "custo": r["estimated_monthly_cost"],
            }
            for r in por_rotulo.get(rotulo, [])
        }
        pools.append(
            {
                "familia": p["familia"],
                "regiao": p["regiao"],
                "custo": p["custo"],
                "arm": p["arquitetura"] == "arm",
                "burstable": p["burstable"],
                "compromisso": p["coberto_por_compromisso"],
                "alvos": alvos,
            }
        )
    dep = s.get("dependencias")
    if dep:
        dep = {k: v for k, v in dep.items() if k != "grupos"} | {
            "grupos": len(dep.get("grupos") or [])
        }
        if dep.get("exclusividade"):
            dep["exclusividade"] = {
                k: v for k, v in dep["exclusividade"].items() if k != "recursos"
            } | {"recursos": len(dep["exclusividade"].get("recursos") or [])}
    rotulos = [s["nome"]] + [f"{p['familia']} · {p['regiao'] or 'global'}" for p in s["pools"]]
    servicos.append(
        {
            "id": s["id"],
            "nome": s["nome"],
            "categoria": s["categoria"],
            "tipo": s["service_type"],
            "tipo_inferido": s["tipo_inferido"],
            "custo": s["custo"],
            "fracao": s["fracao"],
            "nivel": s["nivel"],
            "nivel_sugerido": s["nivel_sugerido"],
            "sugestoes": s["sugestoes"],
            "por_destino": s["por_destino"],
            "equivalente": s["equivalente_por_destino"],
            "refatoracao": s["refatoracao_por_destino"],
            "classe": s["classe_migracao"],
            "esforco": s["esforco"],
            "alertas": s["alertas"],
            "entra_no_score": s["entra_no_score"],
            "dependencias": dep,
            "pools": pools,
            "estimativa": estimativas(rotulos),
        }
    )

dados = {
    "arquivo": "mock_aws_cur2_2026-03.csv.gz",
    "origem": sc["provedor_origem"],
    "recomendado": res["recommended_provider"].lower(),
    "custo_atual": sc["custo_atual"],
    "reconciliacao": sc["reconciliacao"],
    "resumo": sc["resumo"],
    "com_inventario": sc["com_inventario"],
    "servicos": servicos,
    "escopo": json.loads((RAIZ / "escopo.json").read_text()),
}

# Razão de preço das categorias que o backend cota por taxa (banco e objeto), por destino
_aux = json.loads((RAIZ / "res_inv.json").read_text())["llm_input_payload"]["custos_auxiliares"]
dados["razao_categoria"] = {}
for chave, cat in (
    ("equivalentes_banco_dados", "database"),
    ("equivalentes_armazenamento_objetos", "object_storage"),
):
    for e in _aux.get(chave, []):
        dados["razao_categoria"].setdefault(e["provedor"], {})[cat] = float(
            e["taxa_destino"]
        ) / float(e["taxa_origem"])

# A mesma análise antes do inventário: sem o que vem dele (dependências e as
# hipóteses tiradas delas). É o que o backend devolve para o mesmo billing sem
# inventory_id.
RANK = ["low", "medium", "high"]


def sem_inventario(d: dict) -> dict:
    out = json.loads(json.dumps(d))
    out["com_inventario"] = False
    out["escopo"] = None
    for s in out["servicos"]:
        s["dependencias"] = None
        s["sugestoes"] = [
            x for x in s["sugestoes"] if x["fonte"] not in ("dependencias", "inventario")
        ]
        niveis = [x["nivel"] for x in s["sugestoes"] if x["nivel"] in RANK]
        s["nivel_sugerido"] = max(niveis, key=RANK.index) if (niveis and not s["nivel"]) else None
    return out


def uri(nome: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode((LOGOS / nome).read_bytes()).decode()


logos = {
    "aws": uri("aws.svg"),
    "aws_light": uri("aws-light.svg"),
    "azure": uri("azure.svg"),
    "gcp": uri("gcp.svg"),
    "oci": uri("oci.svg"),
}

html = (AQUI / "template.html").read_text()
html = html.replace("/*__DADOS_SEM__*/null", json.dumps(sem_inventario(dados), ensure_ascii=False))
html = html.replace("/*__DADOS__*/null", json.dumps(dados, ensure_ascii=False))
html = html.replace("/*__LOGOS__*/null", json.dumps(logos))
(AQUI / "dependencias.html").write_text(html)
print("ok", len(html) // 1024, "KB")  # noqa: T201
