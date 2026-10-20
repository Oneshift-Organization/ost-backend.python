"""A análise de topologia em JSON (resposta da API e, depois, payload da análise).

Listas longas saem cortadas em ``limite`` itens, e ``limites.cortadas`` diz qual
lista foi cortada e quantos itens ela tinha: cortar em silêncio faria o cliente
achar que viu tudo.
"""

from __future__ import annotations

from typing import Any

from app.domain.services.topologia.modelo import (
    AnaliseDeTopologia,
    Criticidade,
    Dependencia,
    DependenciaEntreOndas,
    Evidencia,
    GrupoDeMovimentacao,
    Ocioso,
    Premissa,
    Risco,
    Unidade,
)

LIMITE_PADRAO = 500


def _ev(e: Evidencia) -> dict[str, Any]:
    return {"origem": e.origem, "descricao": e.descricao, "grupo": e.grupo}


def _crit(c: Criticidade) -> dict[str, Any]:
    return {
        "declarada": c.declarada.value if c.declarada else None,
        "efetiva": c.efetiva.value if c.efetiva else None,
        "origem": c.origem,
    }


def unidade(u: Unidade) -> dict[str, Any]:
    return {
        "id": u.id,
        "nome": u.nome,
        "aplicacao": u.aplicacao,
        "ambiente": u.ambiente,
        "conta": u.conta,
        "regiao": u.regiao,
        "tipos": dict(u.tipos),
        "recursos": len(u.recursos),
    }


def dependencia(d: Dependencia) -> dict[str, Any]:
    return {
        "de": d.de,
        "para": d.para,
        "natureza": d.natureza.value,
        "acoplamento": d.acoplamento.value,
        "portas": list(d.portas),
        "rebaixada": d.rebaixada,
        "evidencias": [_ev(e) for e in d.evidencias],
    }


def grupo(g: GrupoDeMovimentacao) -> dict[str, Any]:
    return {
        "id": g.id,
        "nome": g.nome,
        "unidades": list(g.unidades),
        "recursos": g.recursos,
        "criticidade": _crit(g.criticidade),
        "raio_de_impacto": g.raio_de_impacto,
        "depende_de": list(g.depende_de),
    }


def entre_ondas(e: DependenciaEntreOndas) -> dict[str, Any]:
    return {
        "de_grupo": e.de_grupo,
        "para_grupo": e.para_grupo,
        "onda_de": e.onda_de,
        "onda_para": e.onda_para,
        "portas": list(e.portas),
        "evidencias": [_ev(x) for x in e.evidencias],
    }


def risco(r: Risco) -> dict[str, Any]:
    return {
        "regra": r.regra,
        "severidade": r.severidade.value,
        "titulo": r.titulo,
        "detalhe": r.detalhe,
        "recursos": list(r.recursos),
        "total": r.total,
    }


def ocioso(o: Ocioso) -> dict[str, Any]:
    return {"recurso": o.recurso, "tipo": o.tipo, "motivo": o.motivo, "tamanho_gb": o.tamanho_gb}


def premissa(p: Premissa) -> dict[str, Any]:
    return {"campo": p.campo, "valor": p.valor, "base": p.base}


def serializar_topologia(
    analise: AnaliseDeTopologia, *, limite: int = LIMITE_PADRAO
) -> dict[str, Any]:
    cortadas: dict[str, int] = {}

    def cortar(nome: str, itens: list[Any]) -> list[Any]:
        if len(itens) > limite:
            cortadas[nome] = len(itens)
            return itens[:limite]
        return itens

    corpo = {
        "fingerprint": analise.fingerprint,
        "resumo": dict(analise.resumo),
        "premissas": [premissa(p) for p in analise.premissas],
        "ondas": [{"numero": o.numero, "grupos": list(o.grupos)} for o in analise.ondas],
        "independentes": cortar("independentes", list(analise.independentes)),
        "ferramentas": list(analise.ferramentas),
        "grupos": cortar("grupos", [grupo(g) for g in analise.grupos]),
        "unidades": cortar("unidades", [unidade(u) for u in analise.unidades]),
        "dependencias": cortar("dependencias", [dependencia(d) for d in analise.dependencias]),
        "dependencias_entre_ondas": cortar(
            "dependencias_entre_ondas", [entre_ondas(e) for e in analise.dependencias_entre_ondas]
        ),
        "riscos": cortar("riscos", [risco(r) for r in analise.riscos]),
        "ociosos": cortar("ociosos", [ocioso(o) for o in analise.ociosos]),
    }
    corpo["limites"] = {"maximo_por_lista": limite, "cortadas": cortadas}
    return corpo
