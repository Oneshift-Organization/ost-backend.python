"""O que os arquivos enviados permitem, e o que falta (PLANO_CENARIOS_DE_ENTRADA §3.4).

Cada combinação de fatura e inventário dá uma análise diferente: Cost Explorer
não dá nada; fatura sem ID dá aplicações com custo estimado; CUR com ID e
inventário dá custo medido por recurso. Tratar todas como se fossem a mesma fez a
tela afirmar coisas que os arquivos não sustentavam.

O diagnóstico diz, em linguagem de quem enviou o arquivo, o que ficou de fora e
como resolver. Três níveis:

- ``critico``: a análise não se sustenta (formato que não serve, inventário de
  outra nuvem, quase nada classificado);
- ``alerta``: a análise sai, mas falta uma parte (sem inventário, inventário que
  cobre só parte da fatura, custo por aplicação estimado);
- ``informativo``: limite conhecido do formato, sem prejuízo do que foi pedido.

Função pura, sem I/O: lê o formato da fatura e o que a análise já congelou.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

CRITICO = "critico"
ALERTA = "alerta"
INFORMATIVO = "informativo"
_ORDEM = {CRITICO: 0, ALERTA: 1, INFORMATIVO: 2}

COST_EXPLORER = "AWS_CE_DAILY_BY_SERVICE"
# Não é fatura: o relatório de uso da OCI tem as quantidades, sem o custo
OCI_RELATORIO_DE_USO = "OCI_USAGE_REPORT"
_CLASSIFICACAO_MINIMA = 0.95
_CLASSIFICACAO_CRITICA = 0.5
_COBERTURA_MINIMA = 0.8


@dataclass(frozen=True, slots=True)
class PerfilDaFatura:
    nome: str
    provedor: str
    aceita: bool
    # Traz o ID de cada recurso? None: depende de como foi exportado (o CUR tem a opção)
    com_id: bool | None


PERFIS: Mapping[str, PerfilDaFatura] = {
    "AWS_CUR2.0": PerfilDaFatura("CUR 2.0 da AWS", "aws", True, None),
    "AWS_CUR_LEGACY": PerfilDaFatura("CUR legado da AWS", "aws", True, None),
    "AWS_DETAILED_BILLING": PerfilDaFatura(
        "fatura detalhada da AWS (formato legado, por tipo de uso)", "aws", True, False
    ),
    COST_EXPLORER: PerfilDaFatura("Cost Explorer da AWS (total por serviço)", "aws", False, False),
    OCI_RELATORIO_DE_USO: PerfilDaFatura("relatório de uso da OCI (sem custo)", "oci", False, True),
    "GCP_BILLING_DETAILED": PerfilDaFatura("export detalhado do BigQuery", "gcp", True, True),
    "GCP_BILLING_STD": PerfilDaFatura("export padrão do BigQuery", "gcp", True, False),
    "GCP_COST_TABLE": PerfilDaFatura("Cost Table do GCP", "gcp", True, False),
    "AZURE_PAYG": PerfilDaFatura("Cost Details da Azure", "azure", True, True),
    "AZURE_MCA": PerfilDaFatura("Cost Details da Azure", "azure", True, True),
    "AZURE_EA": PerfilDaFatura("Cost Details da Azure", "azure", True, True),
    "OCI_COST_REPORT": PerfilDaFatura("relatório de custos da OCI", "oci", True, True),
}

# O export certo, em uma frase, por nuvem. O passo a passo fica no guia da tela.
COMO_EXPORTAR: Mapping[str, str] = {
    "aws": (
        "Na AWS, crie uma exportação CUR 2.0 em Billing and Cost Management, na tela Data Exports, "
        'marque "Include resource IDs" e envie o .csv.gz do mês.'
    ),
    "gcp": (
        "No GCP, ative o export detalhado do Cloud Billing para o BigQuery e envie o resultado "
        "da consulta do mês (CSV ou JSON)."
    ),
    "azure": (
        'Na Azure, em Cost Management, na tela Exports, exporte "Cost and usage details" do mês e '
        "envie o CSV."
    ),
    "oci": (
        "Na OCI, em Billing & Cost Management, na tela Cost and Usage Reports, baixe o relatório de "
        "custos do mês."
    ),
}


@dataclass(frozen=True, slots=True)
class Problema:
    nivel: str
    codigo: str
    titulo: str
    texto: str


@dataclass(frozen=True, slots=True)
class Diagnostico:
    nivel: str | None  # o pior entre crítico e alerta; None quando não há nenhum dos dois
    problemas: tuple[Problema, ...]


def _diagnostico(problemas: list[Problema]) -> Diagnostico:
    problemas.sort(key=lambda p: (_ORDEM[p.nivel], p.codigo))
    pior = next((p.nivel for p in problemas if p.nivel in (CRITICO, ALERTA)), None)
    return Diagnostico(nivel=pior, problemas=tuple(problemas))


def _como_exportar(provedor: str | None, *, depois_de_dois_pontos: bool = False) -> str:
    texto = COMO_EXPORTAR.get((provedor or "").lower(), "")
    return texto[:1].lower() + texto[1:] if depois_de_dois_pontos else texto


def problemas_do_arquivo(formato: str | None, *, com_id: bool | None = None) -> list[Problema]:
    """O que o formato da fatura, sozinho, já diz. ``com_id``: o que a amostra do
    arquivo mostrou (o CUR pode vir com ou sem a coluna de ID)."""
    perfil = PERFIS.get(formato or "")
    if perfil is None:
        return []
    if formato == OCI_RELATORIO_DE_USO:
        return [
            Problema(
                CRITICO,
                "oci_relatorio_de_uso",
                "Esse é o relatório de uso da OCI, não o de custos",
                "O arquivo `reports_usage` traz quanto cada recurso consumiu, mas não quanto "
                "custou, e sem o custo não há o que comparar. Na mesma tela da OCI há o relatório "
                "de custos, com o nome `reports_cost`: é ele que a análise lê. "
                + _como_exportar(perfil.provedor),
            )
        ]
    if not perfil.aceita:
        return [
            Problema(
                CRITICO,
                "cost_explorer",
                "O Cost Explorer não serve para a análise",
                "Esse arquivo é o resumo do Cost Explorer: o gasto de cada serviço por dia, sem "
                "dizer que máquina, que banco ou que disco gerou cada valor. Sem isso não dá para "
                "saber o que você usa nem cotar o equivalente em outra nuvem, e a análise sairia "
                "quase vazia. Não é erro seu: é o primeiro export que o console oferece. "
                + _como_exportar(perfil.provedor),
            )
        ]
    tem_id = perfil.com_id if perfil.com_id is not None else com_id
    if tem_id is False:
        sem_coluna = perfil.com_id is None
        return [
            Problema(
                INFORMATIVO,
                "fatura_sem_id",
                "Fatura sem o ID de cada recurso",
                (
                    'O CUR foi exportado sem "Include resource IDs". '
                    if sem_coluna
                    else f"O {perfil.nome} não traz o ID de cada recurso. "
                )
                + "A análise e a recomendação saem completas; com o inventário, o custo de cada "
                "aplicação sai estimado pelo tipo do recurso. Para o custo medido: "
                + _como_exportar(perfil.provedor, depois_de_dois_pontos=True),
            )
        ]
    return []


def diagnosticar_arquivo(formato: str | None) -> Diagnostico:
    """Só pelo formato: para a análise que ainda não terminou (o resto vem do payload)."""
    return _diagnostico(problemas_do_arquivo(formato))


def diagnosticar_payload(
    *, formato: str | None, provedor_da_fatura: str | None, payload: Mapping[str, Any] | None
) -> Diagnostico:
    """A análise concluída: lê do payload congelado a topologia e a classificação."""
    if not payload:
        return diagnosticar_arquivo(formato)
    premissas = payload.get("premissas") or {}
    cobertura = premissas.get("cobertura_classificacao")
    return diagnosticar_analise(
        formato=formato,
        provedor_da_fatura=provedor_da_fatura,
        topologia=payload.get("topologia") or None,
        cobertura_classificacao=float(cobertura) if isinstance(cobertura, int | float) else None,
    )


def diagnosticar_analise(
    *,
    formato: str | None,
    provedor_da_fatura: str | None,
    topologia: Mapping[str, Any] | None,
    cobertura_classificacao: float | None,
) -> Diagnostico:
    """A partir do que a análise já guardou: o formato da fatura, o bloco de topologia
    do payload (ausente = sem inventário) e a fração do gasto classificada."""
    perfil = PERFIS.get(formato or "")
    cobertura = (topologia or {}).get("resumo", {}).get("cobertura") or {}
    com_id: bool | None = None
    if topologia and cobertura:
        com_id = bool(cobertura.get("custo_com_recurso"))
    problemas = problemas_do_arquivo(formato, com_id=com_id)

    if perfil is None or perfil.aceita:
        if topologia is None:
            problemas.append(
                Problema(
                    ALERTA,
                    "sem_inventario",
                    "Análise sem inventário",
                    "Sem o inventário da conta, a análise não mostra aplicações, dependências nem "
                    "o plano de migração: só o gasto e a recomendação. Envie o inventário (na AWS, "
                    "a saída do AWS Config) e refaça a análise.",
                )
            )
        else:
            problemas.extend(_problemas_do_inventario(perfil, provedor_da_fatura, topologia))
            if any(p.codigo == "custo_estimado" for p in problemas):
                # Com inventário, "sem ID" vira o alerta do custo estimado: o mesmo assunto
                problemas = [p for p in problemas if p.codigo != "fatura_sem_id"]

    if cobertura_classificacao is not None and perfil is not None and perfil.aceita:
        if cobertura_classificacao < _CLASSIFICACAO_CRITICA:
            problemas.append(
                Problema(
                    CRITICO,
                    "classificacao_baixa",
                    "A maior parte do gasto não foi reconhecida",
                    f"Só {cobertura_classificacao:.0%} do gasto foi ligado a um serviço "
                    "conhecido. A comparação entre nuvens fica com o resto de fora. Confira se o "
                    "arquivo é o export completo do mês.",
                )
            )
        elif cobertura_classificacao < _CLASSIFICACAO_MINIMA:
            problemas.append(
                Problema(
                    ALERTA,
                    "classificacao_parcial",
                    "Parte do gasto não foi reconhecida",
                    f"{1 - cobertura_classificacao:.0%} do gasto não foi ligado a um serviço "
                    "conhecido e ficou fora da comparação entre nuvens.",
                )
            )
    return _diagnostico(problemas)


def _problemas_do_inventario(
    perfil: PerfilDaFatura | None,
    provedor_da_fatura: str | None,
    topologia: Mapping[str, Any],
) -> list[Problema]:
    problemas: list[Problema] = []
    provedor_inv = str(topologia.get("provedor") or "").lower()
    provedor_fat = (provedor_da_fatura or (perfil.provedor if perfil else "")).lower()
    if provedor_inv and provedor_fat and provedor_inv != provedor_fat:
        problemas.append(
            Problema(
                CRITICO,
                "inventario_de_outra_nuvem",
                "Inventário de outra nuvem",
                f"A fatura é da {provedor_fat.upper()} e o inventário, da {provedor_inv.upper()}. "
                "Aplicações e plano não correspondem à fatura. Envie o inventário da mesma conta.",
            )
        )
        return problemas
    resumo = topologia.get("resumo") or {}
    cobertura = resumo.get("cobertura") or {}
    fracao = cobertura.get("fracao_encontrada")
    com_recurso = cobertura.get("custo_com_recurso") or 0
    if com_recurso and fracao is not None and fracao < _COBERTURA_MINIMA:
        contas = sorted({x.get("conta") for x in cobertura.get("lacunas") or [] if x.get("conta")})
        problemas.append(
            Problema(
                ALERTA,
                "inventario_parcial",
                "O inventário cobre só parte da fatura",
                f"{fracao:.0%} do custo com recurso identificado está no inventário. Aplicações e "
                "plano mostram só essa parte"
                + (f"; faltam as contas {', '.join(contas)}." if contas else "."),
            )
        )
    if not com_recurso and perfil is not None:
        problemas.append(
            Problema(
                ALERTA,
                "custo_estimado",
                "Custo por aplicação estimado",
                "A fatura não traz o ID de cada recurso: o custo de cada aplicação foi estimado "
                "pelo tipo (a máquina pelo tipo, o banco pela classe). As aplicações e o que migra "
                "junto vêm do inventário e estão completos. Para o custo medido: "
                + _como_exportar(perfil.provedor, depois_de_dois_pontos=True),
            )
        )
    if any(r.get("regra") == "inventario_desalinhado" for r in resumo.get("riscos") or []):
        problemas.append(
            Problema(
                ALERTA,
                "inventario_desalinhado",
                "Inventário e fatura de momentos diferentes",
                "O inventário foi tirado longe do mês da fatura: recursos criados ou removidos "
                "no meio aparecem como lacuna. Exporte o inventário perto do fim do mês da fatura.",
            )
        )
    return problemas
