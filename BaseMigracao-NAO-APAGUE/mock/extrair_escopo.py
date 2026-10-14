"""Extrai do cálculo real do backend o grafo por aplicação, para o mock de escopo.

Roda a mesma topologia que a análise usa (analisar_topologia) sobre o inventário
da análise local e cruza com o custo por recurso do billing. Só leitura.

Uso:
    PYTHONPATH=<repo> .venv/bin/python extrair_escopo.py \\
        <inventario_id> <report_id> <user_id> <saida.json>
"""

import asyncio
import json
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.services.correlacao_inventario import IndiceDeApelidos
from app.domain.services.topologia import analisar_topologia
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_inventory_repo import PgInventoryRepository
from app.use_cases.inventory.declaracoes import para_dominio


async def extrair(inventario_id: str, report_id: str, user_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        repo = PgInventoryRepository(s)
        inv = await repo.obter(UUID(inventario_id), UUID(user_id))
        conteudo = await repo.carregar_conteudo(inv)
        custos = await PgBillingItemRepository(s).custos_por_recurso(UUID(report_id), UUID(user_id))

    analise = analisar_topologia(conteudo, para_dominio(inv.declaracoes))
    apelidos = IndiceDeApelidos(conteudo)

    por_unidade: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    fora: dict[str, Decimal] = defaultdict(Decimal)
    for linha in custos.por_recurso:
        chave = apelidos.casar(linha.resource_id)
        unidade = analise.unidade_de.get(chave) if chave else None
        if unidade is None:
            fora[linha.servico] += linha.custo
        else:
            por_unidade[unidade][linha.servico] += linha.custo

    onda_de = {g: o.numero for o in analise.ondas for g in o.grupos}
    unidades = []
    for u in analise.unidades:
        servicos = {
            k: round(float(v), 2) for k, v in sorted(por_unidade[u.id].items(), key=lambda x: -x[1])
        }
        unidades.append(
            {
                "id": u.id,
                "nome": u.nome,
                "aplicacao": u.aplicacao,
                "ambiente": u.ambiente,
                "regiao": u.regiao,
                "conta": u.conta,
                "recursos": len(u.recursos),
                "tipos": dict(u.tipos),
                "grupo": analise.grupo_de.get(u.id),
                "ferramenta": u.id in analise.ferramentas,
                "servicos": servicos,
                "custo": round(sum(servicos.values()), 2),
            }
        )
    grupos = [
        {
            "id": g.id,
            "nome": g.nome,
            "unidades": list(g.unidades),
            "onda": onda_de.get(g.id),
            "criticidade": g.criticidade.efetiva.value if g.criticidade.efetiva else None,
            "depende_de": list(g.depende_de),
        }
        for g in analise.grupos
    ]
    deps = [
        {
            "de": d.de,
            "para": d.para,
            "natureza": d.natureza.value,
            "acoplamento": d.acoplamento.value,
            "portas": list(d.portas),
            "rebaixada": d.rebaixada,
            "evidencia": d.evidencias[0].descricao if d.evidencias else None,
        }
        for d in analise.dependencias
    ]
    riscos_banco = [
        {"titulo": r.titulo, "detalhe": r.detalhe, "recursos": list(r.recursos)[:5]}
        for r in analise.riscos
        if r.regra == "banco_compartilhado"
    ]
    out = {
        "unidades": unidades,
        "grupos": grupos,
        "dependencias": deps,
        "ferramentas": list(analise.ferramentas),
        "fora_das_unidades": {
            k: round(float(v), 2) for k, v in sorted(fora.items(), key=lambda x: -x[1])
        },
        "custo_da_base": round(float(custos.custo_da_base), 2),
        "premissas": [
            {"campo": p.campo, "valor": p.valor, "base": p.base} for p in analise.premissas
        ],
        "bancos_compartilhados": riscos_banco,
    }
    return out


if __name__ == "__main__":
    resultado = asyncio.run(extrair(*sys.argv[1:4]))
    with open(sys.argv[4], "w") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    print(len(resultado["unidades"]), "unidades,", len(resultado["grupos"]), "grupos")  # noqa: T201
