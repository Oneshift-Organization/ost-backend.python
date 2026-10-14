"""Plano de migração em planilha, a lista de trabalho de quem executa.

PLANO_ESCOPO_DE_MIGRACAO, E7.

O PDF é para ler e circular; a planilha é para acompanhar. Mesma leitura do
``Documento`` (blocos, ordem, o que ficou de fora), mais a lista de cada recurso
a migrar com colunas de status e responsável para a equipe preencher. Só
formata; nenhum número é calculado aqui.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from io import BytesIO
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

from app.infrastructure.exports.plano_documento import (
    METODO,
    NATUREZA,
    NOME_DO_BLOCO,
    Documento,
    etapa,
)

_TITULO = Font(bold=True, size=15, color="13133D")
_SUBTITULO = Font(size=11, color="5B5B88")
_NEGRITO = Font(bold=True)
_CABECALHO = Font(bold=True, color="FFFFFF")
_FUNDO = PatternFill("solid", fgColor="644AFF")
_FUNDO_BLOCO = {
    "pre": PatternFill("solid", fgColor="E8F0FE"),
    "principal": PatternFill("solid", fgColor="EFECFF"),
    "pos": PatternFill("solid", fgColor="E6F7F5"),
}
_DINHEIRO = "#,##0.00"
_PORCENTAGEM = "0%"
STATUS = ("A migrar", "Em andamento", "Migrado", "Validado", "Não vai migrar")
DECISAO = ("Incluir no plano", "Manter conexão entre nuvens", "A decidir")


def _tabela(
    ws: Any,
    linha: int,
    cabecalho: Sequence[str],
    linhas: Iterable[Sequence[Any]],
    *,
    dinheiro: Iterable[int] = (),
    filtro: bool = False,
) -> int:
    """Escreve a tabela a partir de ``linha``; devolve a última linha escrita."""
    for col, texto in enumerate(cabecalho, start=1):
        c = ws.cell(row=linha, column=col, value=texto)
        c.font, c.fill = _CABECALHO, _FUNDO
        c.alignment = Alignment(vertical="center")
    inicio = linha
    colunas_dinheiro = set(dinheiro)
    for valores in linhas:
        linha += 1
        for col, valor in enumerate(valores, start=1):
            c = ws.cell(row=linha, column=col, value=valor)
            c.alignment = Alignment(vertical="top", wrap_text=isinstance(valor, str))
            if col in colunas_dinheiro and isinstance(valor, int | float):
                c.number_format = _DINHEIRO
    if filtro and linha > inicio:
        ws.auto_filter.ref = f"A{inicio}:{get_column_letter(len(cabecalho))}{linha}"
    return linha


def _larguras(ws: Any, larguras: Sequence[int]) -> None:
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura


def _lista(ws: Any, coluna: str, primeira: int, ultima: int, opcoes: Sequence[str]) -> None:
    if ultima < primeira:
        return
    dv = DataValidation(type="list", formula1='"' + ",".join(opcoes) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{coluna}{primeira}:{coluna}{ultima}")


def _resumo(wb: Workbook, doc: Documento) -> None:
    p, moeda = doc.plano, doc.moeda
    ws = wb.active
    ws.title = "Resumo"
    ws["A1"] = f"Plano de migração: {doc.titulo}"
    ws["A1"].font = _TITULO
    ws["A2"] = doc.escolha.capitalize()
    ws["A2"].font = _SUBTITULO
    rec = doc.destino_recomendado
    info: list[tuple[str, Any]] = [
        ("Fatura", doc.periodo or "-"),
        ("Nuvem atual", doc.origem or "-"),
        ("Recomendada pela análise", (doc.recomendado or "-").upper()),
        ("Gerado em", doc.gerado_em.strftime("%d/%m/%Y %H:%M UTC")),
        ("Análise", doc.job_id),
        ("Aplicações no plano", ", ".join(p.aplicacoes)),
        ("Recursos no inventário", p.recursos),
        (f"Custo hoje ({moeda}/mês)", p.custo),
    ]
    if rec:
        info += [
            (f"Estimado na {rec.provedor.upper()} ({moeda}/mês)", rec.custo_estimado),
            ("Variação sobre o que tem cotação", rec.variacao),
            (f"Sem cotação ({moeda}/mês)", rec.sem_cotacao),
        ]
    linha = 4
    for k, v in info:
        ws.cell(row=linha, column=1, value=k).font = _NEGRITO
        c = ws.cell(row=linha, column=2, value=v)
        c.alignment = Alignment(horizontal="left")
        if isinstance(v, float):
            c.number_format = _PORCENTAGEM if k.startswith("Variação") else _DINHEIRO
        linha += 1

    linha += 1
    ws.cell(row=linha, column=1, value="Blocos do plano").font = _NEGRITO
    linha = _tabela(
        ws,
        linha + 1,
        ["Bloco", "O que entra", f"Custo ({moeda}/mês)"],
        ([b.titulo, ", ".join(i.nome for i in b.itens), b.custo] for b in doc.blocos),
        dinheiro=(3,),
    )
    for i, b in enumerate(doc.blocos, start=linha - len(doc.blocos) + 1):
        ws.cell(row=i, column=1).fill = _FUNDO_BLOCO[b.chave]

    linha += 2
    ws.cell(row=linha, column=1, value="Destinos").font = _NEGRITO
    primeira = linha + 2
    linha = _tabela(
        ws,
        linha + 1,
        ["Destino", f"Estimado ({moeda}/mês)", "Variação", f"Sem cotação ({moeda}/mês)"],
        (
            [
                d.provedor.upper() + (" (recomendada)" if d.provedor == doc.recomendado else ""),
                d.custo_estimado,
                d.variacao,
                d.sem_cotacao,
            ]
            for d in sorted(p.destino, key=lambda d: d.custo_estimado)
        ),
        dinheiro=(2, 4),
    )
    for r in range(primeira, linha + 1):
        ws.cell(row=r, column=3).number_format = _PORCENTAGEM

    if doc.aviso_da_ordem:
        linha += 2
        ws.cell(row=linha, column=1, value="Ordem de migração").font = _NEGRITO
        ws.cell(row=linha + 1, column=1, value=doc.aviso_da_ordem)
        linha += 1
    linha += 2
    ws.cell(row=linha, column=1, value="Premissas e limites").font = _NEGRITO
    for i, x in enumerate(doc.premissas, start=linha + 1):
        ws.cell(row=i, column=1, value=x)
    _larguras(ws, [36, 70, 18, 22])


def montar_xlsx(doc: Documento) -> bytes:
    wb = Workbook()
    moeda = doc.moeda
    _resumo(wb, doc)

    ws = wb.create_sheet("Plano")
    ws["A1"] = (
        "O plano em passos, na ordem em que migram. Dentro de cada bloco, as etapas da conta."
    )
    ws["A1"].font = _SUBTITULO
    _tabela(
        ws,
        3,
        [
            "Passo",
            "Bloco",
            "Etapa da conta",
            "Grupo ou unidade",
            "Papel",
            "Por quê",
            f"Custo ({moeda}/mês)",
        ],
        (
            [passo.numero, b.titulo, etapa(passo.ordem), i.nome, i.papel, i.detalhe, i.custo]
            for b in doc.blocos
            for passo in b.passos
            for i in passo.itens
        ),
        dinheiro=(7,),
        filtro=True,
    )
    linha = 4
    for b in doc.blocos:
        for _ in b.itens:
            ws.cell(row=linha, column=2).fill = _FUNDO_BLOCO[b.chave]
            linha += 1
    ws.freeze_panes = "A4"
    _larguras(ws, [8, 20, 14, 36, 18, 64, 16])

    ws = wb.create_sheet("Recursos")
    ws["A1"] = (
        "Cada recurso a migrar, na ordem do plano. Status, responsável e observações são para "
        "a equipe preencher."
    )
    ws["A1"].font = _SUBTITULO
    ultima = _tabela(
        ws,
        3,
        [
            "Bloco",
            "Etapa",
            "Grupo de migração",
            "Unidade",
            "Aplicação",
            "Ambiente",
            "Recurso",
            "Tipo",
            "Tipo na origem",
            "Região",
            f"Custo ({moeda}/mês)",
            "Identificador",
            "Status",
            "Responsável",
            "Observações",
        ],
        (
            [
                NOME_DO_BLOCO[r.bloco],
                etapa(r.ordem),
                r.grupo,
                r.unidade,
                r.aplicacao,
                r.ambiente or "-",
                r.nome,
                r.tipo,
                r.tipo_nativo,
                r.regiao or "-",
                r.custo,
                r.chave,
                "A migrar",
                None,
                None,
            ]
            for r in doc.recursos
        ),
        dinheiro=(11,),
        filtro=True,
    )
    for i, r in enumerate(doc.recursos, start=4):
        ws.cell(row=i, column=1).fill = _FUNDO_BLOCO[r.bloco]
    _lista(ws, "M", 4, ultima, STATUS)
    ws.freeze_panes = "H4"
    _larguras(ws, [18, 11, 34, 26, 14, 10, 28, 16, 22, 12, 14, 60, 14, 18, 30])

    ws = wb.create_sheet("Fora do plano")
    ws["A1"] = (
        "Grupos que conversam com o plano e ficaram de fora. Para cada um: incluir no plano ou "
        "manter a conexão entre nuvens durante a transição."
    )
    ws["A1"].font = _SUBTITULO
    ultima = _tabela(
        ws,
        3,
        [
            "Grupo",
            "Etapa",
            "Situação",
            "Ligado a",
            "Ligação",
            "O que fazer",
            f"Custo ({moeda}/mês)",
            "Decisão",
        ],
        (
            [
                x.nome,
                etapa(x.ordem),
                ("Precisa estar antes" if x.lado == "antes" else "Depende do que sai")
                + (" (nos dois sentidos)" if x.nos_dois_sentidos else ""),
                ", ".join(x.ligado_a),
                x.ligacao,
                x.acao,
                x.custo,
                None,
            ]
            for x in doc.pendencias
        ),
        dinheiro=(7,),
        filtro=True,
    )
    _lista(ws, "H", 4, ultima, DECISAO)
    _larguras(ws, [34, 11, 24, 44, 34, 50, 14, 26])

    ws = wb.create_sheet("Ordem")
    ws["A1"] = (
        "Etapas da conta inteira: a 1ª vai primeiro e quem depende dela vem depois. "
        "Sem ordem: não depende de ninguém."
    )
    ws["A1"].font = _SUBTITULO
    linhas: list[list[Any]] = []
    for e in doc.etapas:
        nome = f"{e.ordem}ª" if e.ordem is not None else "Sem ordem"
        if not e.grupos:
            linhas.append([nome, "-", "nada deste plano"])
        for g in e.grupos:
            situacao = (
                NOME_DO_BLOCO[g.bloco]
                if g.bloco
                else "Fora do plano ("
                + ("o plano chama" if g.lado == "antes" else "chama o plano")
                + ")"
            )
            linhas.append([nome, g.nome, situacao])
    _tabela(ws, 3, ["Etapa", "Grupo", "No plano"], linhas)
    _larguras(ws, [12, 70, 34])

    ws = wb.create_sheet("Serviços")
    provedores = [d.provedor for d in doc.plano.destino]
    _tabela(
        ws,
        1,
        ["Serviço", f"Hoje ({moeda}/mês)", *(p.upper() for p in provedores), "Como foi cotado"],
        (
            [
                s.nome,
                s.custo,
                *(
                    s.destino.get(p) if s.destino.get(p) is not None else "sem cotação"
                    for p in provedores
                ),
                ", ".join(METODO.get(m, m) for m in s.metodos)
                or "Sem cotação: " + "; ".join(dict.fromkeys(s.sem_cotacao.values())),
            ]
            for s in doc.plano.servicos
        ),
        dinheiro=range(2, 3 + len(provedores)),
    )
    _larguras(ws, [44, 16, *([14] * len(provedores)), 60])

    ws = wb.create_sheet("Ligações")
    ws["A1"] = "Como os serviços do plano se chamam (das regras de rede, não do tráfego medido)."
    ws["A1"].font = _SUBTITULO
    _tabela(
        ws,
        3,
        ["Serviço", "Ligação", "Serviço chamado", "Portas"],
        (
            [x.de, NATUREZA.get(x.natureza, x.natureza), x.para, ", ".join(x.portas)]
            for x in doc.plano.ligacoes
        ),
    )
    _larguras(ws, [44, 22, 44, 22])

    saida = BytesIO()
    wb.save(saida)
    return saida.getvalue()
