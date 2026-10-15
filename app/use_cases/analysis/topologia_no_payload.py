"""A topologia congelada no payload da análise (RI14 do PLANO_INVENTARIO_API).

Duas partes, com destinos diferentes:

- ``resumo``: contagens, cobertura, ondas, riscos, críticos, ociosos. É o que o
  LLM e o agente recebem (``compactar_topologia``): o bastante para narrar,
  pouco para gastar tokens, e nada de IP ou regra de firewall do cliente.
- ``detalhe``: o que o Terraform consome — subredes e grupos posicionados, o
  posicionamento de cada pool, versão e rede dos bancos, discos de boot e sem
  anexo. Fica só no payload persistido.

**Neutralidade:** nada aqui mexe em outra chave do payload. O que o Terraform
precisa sobre pools e bancos fica em ``detalhe``, chaveado igual a eles, e não
enxertado em ``pools_computacao`` ou ``inventario_medido``.

E ``impor_topologia``: a ordem de migração é do backend, não do modelo. Depois
do LLM (e do agente), a sequência e os riscos vêm das ondas calculadas.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean
from typing import Any
from uuid import UUID

from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.entities.topologia import (
    DADOS,
    Direcao,
    EstadoDoRecurso,
    Inventario,
    TipoDeRecurso,
    TipoDeRelacao,
    TipoDeSeletor,
)
from app.domain.repositories.i_custo_por_recurso import ILeitorDeCustoPorRecurso
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.atribuicao_por_tipo import atribuir_por_tipo
from app.domain.services.correlacao_inventario import (
    Correlacao,
    CustosDoBilling,
    IndiceDeApelidos,
    correlacionar,
)
from app.domain.services.correspondencia_inventario import (
    Correspondencia,
    avaliar_correspondencia,
)
from app.domain.services.instance_shape import parse_instance_shape
from app.domain.services.topologia import analisar_topologia
from app.domain.services.topologia.indice import Indice
from app.domain.services.topologia.modelo import AnaliseDeTopologia, Nivel, Severidade
from app.domain.services.topologia.servicos_do_billing import Vizinho, dependencias_por_servico
from app.schemas.analysis_v2 import AnalysisV2Response
from app.use_cases.inventory.consultas import exigir_processado, obter_do_usuario
from app.use_cases.inventory.correlacao import ProvedorDiferenteError
from app.use_cases.inventory.declaracoes import para_dominio
from app.use_cases.inventory.serializacao import risco as serializar_risco

VERSAO = "oneshift-topologia-v1"
_MAX_NOMES_POR_ONDA = 12
_MAX_EXEMPLOS = 10
_MAX_LISTA = 20
_ORDEM_SEVERIDADE = {Severidade.ALTA.value: 0, Severidade.MEDIA.value: 1}

Converter = Callable[[Decimal], float]


# ── validação do pedido ─────────────────────────────────────────────────


async def validar_inventario_da_analise(
    repo: IInventoryRepository,
    *,
    inventario_id: UUID,
    user_id: UUID,
    provedor_do_report: str | None,
) -> None:
    """Antes de criar o job (e antes da idempotência): um id inválido não pode
    ser engolido por um job que já existe (RI14)."""
    inventario = await obter_do_usuario(repo, inventario_id, user_id)
    exigir_processado(inventario)
    if provedor_do_report and inventario.provedor != provedor_do_report:
        raise ProvedorDiferenteError(
            f"inventário de {inventario.provedor}, billing de {provedor_do_report}"
        )


# ── montagem ────────────────────────────────────────────────────────────


class MontarTopologiaUseCase:
    """Carrega o inventário, roda topologia e correlação e monta o bloco."""

    def __init__(self, inventarios: IInventoryRepository, custos: ILeitorDeCustoPorRecurso) -> None:
        self._inventarios = inventarios
        self._custos = custos

    async def execute(
        self,
        *,
        user_id: UUID,
        inventario_id: UUID,
        report_id: UUID,
        ctx: Any,
        converter: Converter,
    ) -> dict[str, Any]:
        inventario = await obter_do_usuario(self._inventarios, inventario_id, user_id)
        exigir_processado(inventario)
        conteudo = await self._inventarios.carregar_conteudo(inventario)
        analise = analisar_topologia(conteudo, para_dominio(inventario.declaracoes))
        custos = atribuir_por_tipo(
            conteudo,
            await self._custos.custos_por_recurso(report_id, user_id),
            unidade_de=analise.unidade_de.get,
        )
        correlacao = correlacionar(
            conteudo, custos, captura_estimada=inventario.capturado_em_estimado
        )
        return montar_topologia(
            inventario=inventario,
            conteudo=conteudo,
            analise=analise,
            correlacao=correlacao,
            ctx=ctx,
            converter=converter,
            custos=custos,
        )

    async def correspondencia(
        self, *, user_id: UUID, inventario_id: UUID, report_id: UUID
    ) -> Correspondencia:
        """O inventário é da mesma conta da fatura? Antes de criar o job (RI14)."""
        inventario = await obter_do_usuario(self._inventarios, inventario_id, user_id)
        exigir_processado(inventario)
        conteudo = await self._inventarios.carregar_conteudo(inventario)
        custos = atribuir_por_tipo(
            conteudo, await self._custos.custos_por_recurso(report_id, user_id)
        )
        return avaliar_correspondencia(conteudo, custos, correlacionar(conteudo, custos))


def montar_topologia(
    *,
    inventario: InventarioEnviado,
    conteudo: Inventario,
    analise: AnaliseDeTopologia,
    correlacao: Correlacao,
    ctx: Any,
    converter: Converter,
    custos: CustosDoBilling | None = None,
) -> dict[str, Any]:
    indice = Indice(conteudo)
    apelidos = IndiceDeApelidos(conteudo)
    posicionamento = _posicionamento_dos_pools(ctx, indice, apelidos)
    bancos, bancos_sem_grupo = _bancos(ctx, conteudo, indice, apelidos, correlacao)
    return {
        "versao": VERSAO,
        "inventario_id": str(inventario.id),
        "fingerprint": analise.fingerprint,
        "provedor": inventario.provedor,
        "capturado_em": inventario.capturado_em.isoformat() if inventario.capturado_em else None,
        "capturado_em_estimado": inventario.capturado_em_estimado,
        "declaracoes": deepcopy(inventario.declaracoes),
        "resumo": _resumo(analise, correlacao, converter),
        "detalhe": {
            "rede": _rede(conteudo, indice, posicionamento, bancos, bancos_sem_grupo),
            "posicionamento": posicionamento,
            "bancos": bancos,
            "bancos_sem_grupo": bancos_sem_grupo,
            "discos": _discos(conteudo, correlacao),
            # Dependências por serviço da fatura (PLANO_LOCKIN_INVENTARIO B1). Lido
            # pela visão de acoplamento; não vai ao LLM nem ao agente.
            **(
                {"servicos": _servicos(conteudo, analise, custos, converter)}
                if custos is not None
                else {}
            ),
        },
    }


def _servicos(
    conteudo: Inventario,
    analise: AnaliseDeTopologia,
    custos: CustosDoBilling,
    converter: Converter,
) -> dict[str, Any]:
    def vizinho(v: Vizinho) -> dict[str, Any]:
        return {"servico": v.servico, "custo": converter(v.custo), "natureza": v.natureza}

    return {
        nome: {
            "nivel": d.nivel,
            "motivo": d.motivo,
            "grupos": list(d.grupos),
            "ondas": list(d.ondas),
            "migra_junto_com": [vizinho(v) for v in d.migra_junto_com],
            "depende_de": [{"servico": v.servico, "natureza": v.natureza} for v in d.depende_de],
            "dependentes": d.dependentes,
            "banco_compartilhado": d.banco_compartilhado,
            "grupo_grande": d.grupo_grande,
            "custo_do_grupo": converter(d.custo_do_grupo),
            "cobertura": float(d.cobertura) if d.cobertura is not None else None,
            "regioes": list(d.regioes),
            "exclusividade": (
                {
                    "nivel": d.exclusividade.nivel,
                    "motivo": d.exclusividade.motivo,
                    "fracao": float(d.exclusividade.fracao),
                    "recursos": list(d.exclusividade.recursos),
                }
                if d.exclusividade
                else None
            ),
        }
        for nome, d in dependencias_por_servico(conteudo, analise, custos).items()
    }


def _vcpus(atributos: Mapping[str, Any]) -> float | None:
    valor = atributos.get("vcpus")
    if isinstance(valor, int | float) and valor > 0:
        return float(valor)
    forma = parse_instance_shape(str(atributos.get("tipo_maquina") or ""))
    return float(forma.vcpus) if forma else None


def _boot_gb(indice: Indice, chave: str) -> int | None:
    for d in indice.de(chave, TipoDeRelacao.ANEXADO_A):
        recurso = indice.recursos.get(d)
        if recurso and recurso.tipo is TipoDeRecurso.DISCO and recurso.atributos.get("boot"):
            tamanho = recurso.atributos.get("tamanho_gb")
            return int(tamanho) if isinstance(tamanho, int | float) else None
    return None


def _posicionamento_dos_pools(
    ctx: Any, indice: Indice, apelidos: IndiceDeApelidos
) -> list[dict[str, Any]]:
    """Onde estão, na origem, as máquinas de cada pool (RI15.5).

    Chave igual à do pool no payload: ``familia`` e ``regiao`` (nula quando o
    pool é global, como o payload a omite).

    Agrupa por subrede, que é o que o destino precisa preservar (a subrede
    carrega a zona). Os grupos de segurança são a UNIÃO dos da subrede: o
    empacotamento consolida instâncias, e a máquina consolidada tem de aceitar
    o que qualquer uma delas aceitava. ``conjuntos_de_grupos`` > 1 diz que a
    consolidação juntou posturas diferentes. Agrupar também pelo conjunto de
    grupos partia um pool de 79 instâncias em dezenas de posicionamentos
    menores que o número de máquinas aprovadas.
    """
    saida = []
    for fp in getattr(ctx, "family_pools", ()) or ():
        inv = getattr(fp, "inventario", None)
        if inv is None or not inv.recursos:
            continue
        por_subrede: dict[str | None, dict[str, Any]] = {}
        sem = 0
        for rid in sorted(inv.recursos):
            chave = apelidos.casar(rid)
            if chave is None or indice.tipo(chave) is not TipoDeRecurso.INSTANCIA:
                sem += 1
                continue
            subredes = sorted(indice.subredes_de(chave))
            g = por_subrede.setdefault(
                subredes[0] if subredes else None,
                {"instancias": 0, "vcpus": 0.0, "boots": [], "tipos": Counter(), "grupos": set()},
            )
            atributos = indice.recursos[chave].atributos
            g["instancias"] += 1
            g["vcpus"] += _vcpus(atributos) or 1.0
            g["grupos"].add(frozenset(indice.grupos_de.get(chave, ())))
            if (boot := _boot_gb(indice, chave)) is not None:
                g["boots"].append(boot)
            if atributos.get("tipo_maquina"):
                g["tipos"][str(atributos["tipo_maquina"])] += 1
        regiao = fp.pool_key.region if fp.pool_key.region not in ("", "global") else None
        saida.append(
            {
                "familia": fp.pool_key.machine_family,
                "regiao": regiao,
                "posicionamentos": [
                    {
                        "subrede": subrede,
                        "grupos": sorted(set().union(*g["grupos"])),
                        "conjuntos_de_grupos": len(g["grupos"]),
                        "instancias": g["instancias"],
                        "vcpus": round(g["vcpus"], 2),
                        "disco_boot_gb": round(mean(g["boots"])) if g["boots"] else None,
                        "tipos": dict(g["tipos"].most_common(3)),
                    }
                    for subrede, g in sorted(
                        por_subrede.items(), key=lambda kv: (-kv[1]["vcpus"], kv[0] or "")
                    )
                ],
                "sem_posicionamento": sem,
            }
        )
    return sorted(saida, key=lambda p: (p["familia"], p["regiao"] or ""))


def _bancos(
    ctx: Any,
    conteudo: Inventario,
    indice: Indice,
    apelidos: IndiceDeApelidos,
    correlacao: Correlacao,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Versão, classe e rede dos bancos, pelos grupos medidos do billing (RI15.6).

    ``bancos_sem_grupo``: banco do inventário que tem custo no billing mas não
    caiu em grupo medido — o Flexible Server da Azure, cujo billing não traz a
    classe. É o que permite gerá-lo com a classe medida, e não estimada.
    """
    medido = getattr(ctx, "inventario", None)
    bancos_medidos = list(getattr(medido, "bancos", ()) or ())
    grupos: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for b in bancos_medidos:
        grupos[(b.classe, b.motor, b.multi_az, b.regiao)].append(b)
    usados: set[str] = set()
    saida = []
    for (classe, motor, multi_az, regiao), bs in grupos.items():
        versoes: Counter[str] = Counter()
        subredes: set[str] = set()
        seguranca: set[str] = set()
        encontrados = 0
        for b in bs:
            chave = apelidos.casar(b.resource_id)
            if chave is None or indice.tipo(chave) not in DADOS:
                continue
            usados.add(chave)
            encontrados += 1
            atributos = indice.recursos[chave].atributos
            if atributos.get("versao"):
                versoes[str(atributos["versao"])] += 1
            subredes |= indice.subredes_de(chave)
            seguranca |= indice.grupos_de.get(chave, frozenset())
        saida.append(
            {
                "classe": classe,
                "motor": motor,
                "multi_az": multi_az,
                "regiao": regiao,
                "versoes": dict(sorted(versoes.items())),
                "subredes": sorted(subredes),
                "grupos": sorted(seguranca),
                "encontrados": encontrados,
                "total": len(bs),
            }
        )
    sem_grupo = []
    for r in conteudo.recursos:
        if r.tipo is not TipoDeRecurso.BANCO or r.chave in usados:
            continue
        if r.chave not in correlacao.custo_por_chave or not r.atributos.get("classe"):
            continue
        sem_grupo.append(
            {
                "chave": r.chave,
                "classe": r.atributos.get("classe"),
                "tier": r.atributos.get("tier"),
                "motor": r.atributos.get("motor"),
                "versao": r.atributos.get("versao"),
                "alta_disponibilidade": r.atributos.get("alta_disponibilidade"),
                "regiao": r.regiao,
                "subredes": sorted(indice.subredes_de(r.chave)),
                "grupos": sorted(indice.grupos_de.get(r.chave, frozenset())),
            }
        )
    return (
        sorted(saida, key=lambda g: (str(g["classe"]), str(g["motor"]), str(g["regiao"]))),
        sorted(sem_grupo, key=lambda g: g["chave"]),
    )


def _discos(conteudo: Inventario, correlacao: Correlacao) -> dict[str, Any]:
    """Boot e sem anexo, por categoria, só entre os discos com custo: é o que
    se compara com o armazenamento medido no billing (RI15.7)."""
    boot: dict[str, dict[str, int]] = defaultdict(lambda: {"gb": 0, "volumes": 0})
    solto: dict[str, dict[str, int]] = defaultdict(lambda: {"gb": 0, "volumes": 0})
    for r in conteudo.recursos:
        if r.tipo is not TipoDeRecurso.DISCO or r.chave not in correlacao.custo_por_chave:
            continue
        categoria = (
            "armazenamento_hdd"
            if r.atributos.get("categoria_disco") == "hdd"
            else ("armazenamento_ssd")
        )
        tamanho = r.atributos.get("tamanho_gb")
        gb = int(tamanho) if isinstance(tamanho, int | float) else 0
        if r.estado is EstadoDoRecurso.DISPONIVEL:
            solto[categoria]["gb"] += gb
            solto[categoria]["volumes"] += 1
        elif r.atributos.get("boot"):
            boot[categoria]["gb"] += gb
            boot[categoria]["volumes"] += 1
    return {"boot": dict(sorted(boot.items())), "sem_anexo": dict(sorted(solto.items()))}


def _rede(
    conteudo: Inventario,
    indice: Indice,
    posicionamento: list[dict[str, Any]],
    bancos: list[dict[str, Any]],
    bancos_sem_grupo: list[dict[str, Any]],
) -> dict[str, Any]:
    """Subredes e grupos com recurso posicionado, mais o que a tradução de
    regras precisa saber da origem inteira (RI15.2 a RI15.4)."""
    subredes = {
        p["subrede"] for pool in posicionamento for p in pool["posicionamentos"] if p["subrede"]
    }
    grupos = {g for pool in posicionamento for p in pool["posicionamentos"] for g in p["grupos"]}
    for b in [*bancos, *bancos_sem_grupo]:
        subredes |= set(b["subredes"])
        grupos |= set(b["grupos"])

    com_ip_publico = {
        s
        for r in conteudo.recursos
        if r.tipo is TipoDeRecurso.INSTANCIA and r.atributos.get("ip_publico")
        for s in indice.subredes_de(r.chave)
    }
    regras_por_grupo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for regra in conteudo.regras:
        if regra.grupo in grupos and regra.direcao is Direcao.ENTRADA:
            regras_por_grupo[regra.grupo].append(_regra(regra))
    referenciados = {
        regra.origem.valor
        for regra in conteudo.regras
        if regra.grupo in grupos
        and regra.origem.tipo is TipoDeSeletor.GRUPO
        and regra.origem.valor
        and regra.origem.valor not in grupos
    }
    redes = sorted(
        {
            c
            for r in conteudo.recursos
            if r.tipo is TipoDeRecurso.REDE
            for c in [r.atributos.get("cidr"), *(r.atributos.get("cidrs") or [])]
            if c
        }
    )
    return {
        "subredes": [
            {
                "chave": s,
                "nome": indice.recursos[s].nome,
                "cidr": indice.recursos[s].atributos.get("cidr"),
                "publica": bool(indice.recursos[s].atributos.get("publica")) or s in com_ip_publico,
                "zona": indice.recursos[s].zona or indice.recursos[s].atributos.get("zona"),
            }
            for s in sorted(subredes)
            if s in indice.recursos
        ],
        "grupos": [
            {
                "chave": g,
                "nome": indice.recursos[g].nome,
                "regras": regras_por_grupo.get(g, []),
                "restringe_saida": bool(indice.recursos[g].atributos.get("restringe_saida")),
            }
            for g in sorted(grupos)
            if g in indice.recursos
        ],
        "grupos_referenciados": {
            g: {
                "nome": indice.recursos[g].nome if g in indice.recursos else None,
                "cidrs": sorted(
                    {
                        str(indice.recursos[s].atributos.get("cidr"))
                        for m in indice.membros(g)
                        for s in indice.subredes_de(m)
                        if indice.recursos[s].atributos.get("cidr")
                    }
                ),
            }
            for g in sorted(referenciados)
        },
        "redes_origem": redes,
        "subredes_origem": sorted(
            str(r.atributos["cidr"])
            for r in conteudo.recursos
            if r.tipo is TipoDeRecurso.SUBREDE and r.atributos.get("cidr")
        ),
    }


def _regra(r: Any) -> dict[str, Any]:
    return {
        "acao": r.acao.value,
        "protocolo": r.protocolo,
        "porta_inicio": r.porta_inicio,
        "porta_fim": r.porta_fim,
        "origem": {"tipo": r.origem.tipo.value, "valor": r.origem.valor},
        "destino": {"tipo": r.destino.tipo.value, "valor": r.destino.valor} if r.destino else None,
        "prioridade": r.prioridade,
    }


def _resumo(
    analise: AnaliseDeTopologia, correlacao: Correlacao, converter: Converter
) -> dict[str, Any]:
    nomes = {g.id: g.nome for g in analise.grupos}
    nomes_unidade = {u.id: u.nome for u in analise.unidades}
    riscos = sorted(
        (serializar_risco(r) for r in analise.riscos + correlacao.riscos),
        key=lambda r: (_ORDEM_SEVERIDADE.get(r["severidade"], 2), r["regra"]),
    )
    ociosos_custo = sum((o.custo for o in correlacao.ociosos), Decimal("0"))
    return {
        "contagens": dict(analise.resumo),
        "cobertura": {
            "fracao_encontrada": (
                float(round(correlacao.fracao_encontrada, 4))
                if correlacao.fracao_encontrada is not None
                else None
            ),
            "custo_com_recurso": converter(correlacao.custo_com_recurso),
            "custo_encontrado": converter(correlacao.custo_encontrado),
            # Ligado pelo tipo (fatura sem ID ou máquina trocada): estimado, não medido
            "custo_estimado": converter(correlacao.custo_estimado),
            "lacunas": [
                {"conta": x.conta, "regiao": x.regiao, "custo": converter(x.custo)}
                for x in correlacao.lacunas_por_escopo[:5]
            ],
        },
        "ondas": [
            {
                "numero": o.numero,
                "grupos": [nomes[g] for g in o.grupos[:_MAX_NOMES_POR_ONDA]],
                "total": len(o.grupos),
            }
            for o in analise.ondas
        ],
        "independentes": {
            "total": len(analise.independentes),
            "exemplos": [nomes[g] for g in analise.independentes[:_MAX_EXEMPLOS]],
        },
        "ferramentas": [nomes_unidade[f] for f in analise.ferramentas[:_MAX_EXEMPLOS]],
        "dependencias_entre_ondas": [
            {"de": nomes[e.de_grupo], "para": nomes[e.para_grupo], "portas": list(e.portas)}
            for e in analise.dependencias_entre_ondas[:_MAX_LISTA]
        ],
        "riscos": [
            {k: r[k] for k in ("regra", "severidade", "titulo", "total")}
            for r in riscos[:_MAX_LISTA]
        ],
        "criticos": [g.nome for g in analise.grupos if g.criticidade.efetiva is Nivel.ALTA][
            :_MAX_EXEMPLOS
        ],
        "ociosos": {
            "quantidade": len(correlacao.ociosos),
            "custo_mensal": converter(ociosos_custo),
        },
        "limitacao": "Dependências vêm das regras de rede e do que foi declarado: o que a "
        "configuração permite, não o tráfego real.",
    }


# ── o que sai do payload ────────────────────────────────────────────────


def compactar_topologia(payload: dict[str, Any]) -> dict[str, Any]:
    """Tira o ``detalhe`` (rede, regras, posicionamento) do que vai ao LLM e ao
    agente. O payload persistido continua inteiro: é dele que o Terraform lê."""
    topologia = payload.get("topologia")
    if not isinstance(topologia, dict) or "detalhe" not in topologia:
        return payload
    compacto = dict(payload)
    compacto["topologia"] = {
        k: v for k, v in topologia.items() if k not in ("detalhe", "declaracoes")
    }
    return compacto


@dataclass(frozen=True, slots=True)
class _Sequencia:
    passos: list[str]
    riscos: list[str]


def sequencia_de_migracao(topologia: Mapping[str, Any]) -> _Sequencia:
    resumo = topologia.get("resumo") or {}
    contagens = resumo.get("contagens") or {}
    fundacao = contagens.get("fundacao") or {}
    passos = [
        "Onda 0 — fundação: "
        f"{fundacao.get('rede', 0)} rede(s), {fundacao.get('subrede', 0)} subrede(s), "
        f"{fundacao.get('grupo_seguranca', 0)} grupo(s) de segurança e "
        f"{fundacao.get('gateway', 0)} gateway(s), antes de qualquer carga."
    ]
    for onda in resumo.get("ondas") or []:
        nomes = ", ".join(onda["grupos"])
        resto = onda["total"] - len(onda["grupos"])
        passos.append(
            f"Onda {onda['numero']}: {nomes}" + (f" e mais {resto}" if resto > 0 else "") + "."
        )
    independentes = resumo.get("independentes") or {}
    if independentes.get("total"):
        passos.append(
            f"Sem dependência ({independentes['total']}), a qualquer momento: "
            + ", ".join(independentes.get("exemplos") or [])
            + "."
        )
    if resumo.get("ferramentas"):
        passos.append(
            "Ferramentas de administração, por último e com acesso às duas nuvens: "
            + ", ".join(resumo["ferramentas"])
            + "."
        )
    riscos = [
        f"[{r['severidade']}] {r['titulo']} ({r['total']})"
        for r in resumo.get("riscos") or []
        if r["severidade"] in (Severidade.ALTA.value, Severidade.MEDIA.value)
    ]
    return _Sequencia(passos, riscos)


def impor_topologia(response: AnalysisV2Response, payload: Mapping[str, Any]) -> AnalysisV2Response:
    """A ordem de migração é do backend (RI14): sobrescreve a sequência do
    modelo pelas ondas calculadas e acrescenta os riscos de rede e dados."""
    topologia = payload.get("topologia")
    if not isinstance(topologia, Mapping) or topologia.get("versao") != VERSAO:
        return response
    seq = sequencia_de_migracao(topologia)
    risco_migracao = response.agent_views.risk_migration
    flags = list(risco_migracao.risk_flags)
    flags += [r for r in seq.riscos if r not in flags]
    novo_risco = risco_migracao.model_copy(
        update={"recommended_migration_sequence": seq.passos, "risk_flags": flags}
    )
    return response.model_copy(
        update={
            "agent_views": response.agent_views.model_copy(update={"risk_migration": novo_risco})
        }
    )
