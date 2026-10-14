"""Plano de migração em PDF, para circular entre pessoas (PLANO_ESCOPO_DE_MIGRACAO, E7).

Lê como a tela: resumo e custo, os blocos (pré-migração, principal,
pós-migração), a ordem de migração, o que ficou fora do plano e o que fazer,
custo por serviço e, no anexo, cada recurso a migrar. Só desenha o
``Documento``; nenhum número é calculado aqui.
"""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Rect  # type: ignore[import-untyped]
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_RIGHT  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    CondPageBreak,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.infrastructure.exports.plano_documento import (
    METODO,
    NATUREZA,
    NOME_DO_BLOCO,
    POS,
    PRE,
    PRINCIPAL,
    Documento,
    contagem,
    etapa,
)

# Tokens do front (app/globals.css), tema claro
_TEXTO = colors.HexColor("#13133d")
_SUAVE = colors.HexColor("#5b5b88")
_BORDA = colors.HexColor("#e4e4f9")
_FUNDO = colors.HexColor("#f6f6fd")
_PRIMARIA = colors.HexColor("#644aff")
_COR = {PRE: colors.HexColor("#2563eb"), PRINCIPAL: _PRIMARIA, POS: colors.HexColor("#0d9488")}
_ALERTA = colors.HexColor("#b45309")
_BOM = colors.HexColor("#15803d")
_INFO_FUNDO = colors.HexColor("#eef4ff")

_MARGEM = 16 * mm
_LARGURA = A4[0] - 2 * _MARGEM


def _estilo(nome: str, **kw: Any) -> ParagraphStyle:
    base = {"fontName": "Helvetica", "fontSize": 9, "leading": 12, "textColor": _TEXTO}
    return ParagraphStyle(nome, **(base | kw))


_S = {
    "marca": _estilo(
        "marca", fontName="Helvetica-Bold", fontSize=8, textColor=_PRIMARIA, leading=10
    ),
    "titulo": _estilo("titulo", fontName="Helvetica-Bold", fontSize=20, leading=24),
    "sub": _estilo("sub", fontSize=10.5, leading=14, textColor=_SUAVE),
    "meta": _estilo("meta", fontSize=8, leading=11, textColor=_SUAVE),
    "h2": _estilo(
        "h2", fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=14, spaceAfter=4
    ),
    "h3": _estilo("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=14),
    "p": _estilo("p"),
    "suave": _estilo("suave", textColor=_SUAVE, fontSize=8.5, leading=11.5),
    "cel": _estilo("cel", fontSize=8.5, leading=11),
    "cel_b": _estilo("cel_b", fontName="Helvetica-Bold", fontSize=8.5, leading=11),
    "cel_s": _estilo("cel_s", fontSize=8, leading=10.5, textColor=_SUAVE),
    "cel_r": _estilo("cel_r", fontSize=8.5, leading=11, alignment=TA_RIGHT),
    "cab": _estilo("cab", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=_SUAVE),
    "cab_r": _estilo(
        "cab_r",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=_SUAVE,
        alignment=TA_RIGHT,
    ),
    "kpi_l": _estilo("kpi_l", fontSize=8, leading=10, textColor=_SUAVE),
    "kpi_v": _estilo("kpi_v", fontName="Helvetica-Bold", fontSize=14, leading=18),
    "kpi_s": _estilo("kpi_s", fontSize=7.5, leading=10, textColor=_SUAVE),
}


def _t(texto: object) -> str:
    return escape(str(texto))


def _p(texto: str, estilo: str = "cel") -> Paragraph:
    return Paragraph(texto, _S[estilo])


def _dinheiro(valor: float | None, moeda: str) -> str:
    if valor is None:
        return "sem cotação"
    inteiro, _, cent = f"{valor:,.2f}".partition(".")
    simbolo = {"USD": "US$", "BRL": "R$", "EUR": "€"}.get(moeda, moeda)
    return f"{simbolo} {inteiro.replace(',', '.')},{cent}"


def _pct(valor: float | None) -> str:
    if valor is None:
        return "—"
    return f"{'+' if valor > 0 else ''}{valor * 100:.0f}%".replace(".", ",")


def _cor_hex(cor: Any) -> str:
    return "#" + str(cor.hexval())[2:]


def _tag(texto: str, cor: Any) -> str:
    return f'<font color="{_cor_hex(cor)}"><b>{_t(texto)}</b></font>'


def _tabela(
    cabecalho: Sequence[str],
    linhas: Sequence[Sequence[Any]],
    larguras: Sequence[float],
    *,
    direita: Sequence[int] = (),
    faixa: Any | None = None,
    extra: Sequence[tuple[Any, ...]] = (),
) -> Table:
    cab = [_p(_t(c), "cab_r" if i in direita else "cab") for i, c in enumerate(cabecalho)]
    corpo = [
        [
            c if not isinstance(c, str) else _p(c, "cel_r" if i in direita else "cel")
            for i, c in enumerate(linha)
        ]
        for linha in linhas
    ]
    tabela = Table([cab, *corpo], colWidths=[w * _LARGURA for w in larguras], repeatRows=1)
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _BORDA),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _BORDA),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if faixa is not None:
        estilo.append(("LINEBEFORE", (0, 1), (0, -1), 2.5, faixa))
        estilo.append(("LEFTPADDING", (0, 1), (0, -1), 7))
    tabela.setStyle(TableStyle([*estilo, *extra]))
    return tabela


def _kpis(doc: Documento) -> Table:
    p, moeda = doc.plano, doc.moeda
    rec = doc.destino_recomendado
    origem = doc.origem or "origem"
    celulas = [
        ("Custo hoje", _dinheiro(p.custo, moeda), f"por mês, na {origem}"),
        (
            f"Estimado na {doc.recomendado.upper() if doc.recomendado else 'nuvem recomendada'}",
            _dinheiro(rec.custo_estimado if rec else None, moeda),
            (
                f'<font color="{_cor_hex(_BOM if (rec.variacao or 0) < 0 else _ALERTA)}">'
                f"{_pct(rec.variacao)}</font> sobre o que tem cotação"
                if rec
                else "sem destino recomendado"
            ),
        ),
        ("Aplicações", str(len(p.aplicacoes)), _t(", ".join(p.aplicacoes))),
        ("Recursos", str(p.recursos), f"{len(p.servicos)} serviços"),
    ]
    linha = [
        [_p(_t(rotulo), "kpi_l"), _p(_t(v), "kpi_v"), _p(s, "kpi_s")] for rotulo, v, s in celulas
    ]
    t = Table([linha], colWidths=[_LARGURA / 4] * 4)
    t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, _BORDA),
                ("LINEBEFORE", (1, 0), (-1, -1), 0.8, _BORDA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return t


def _composicao(doc: Documento) -> list[Any]:
    """A régua dos três blocos, com o custo de cada um."""
    total = sum(b.custo for b in doc.blocos) or 1.0
    altura = 7
    d = Drawing(_LARGURA, altura)
    x = 0.0
    vao = 2.0
    blocos = [b for b in doc.blocos if b.custo > 0] or list(doc.blocos)
    disponivel = _LARGURA - vao * (len(blocos) - 1)
    for b in blocos:
        w = max(disponivel * b.custo / total, 4)
        d.add(Rect(x, 0, w, altura, fillColor=_COR[b.chave], strokeColor=None, rx=2, ry=2))
        x += w + vao
    legenda = " &nbsp;&nbsp;&nbsp; ".join(
        f'<font color="{_cor_hex(_COR[b.chave])}">■</font> {_t(b.titulo)} '
        f"<b>{_t(_dinheiro(b.custo, doc.moeda))}</b>"
        for b in doc.blocos
    )
    return [Spacer(1, 10), d, Spacer(1, 4), _p(legenda, "suave")]


def _destinos(doc: Documento) -> Table:
    linhas = []
    destaque = []
    for i, d in enumerate(sorted(doc.plano.destino, key=lambda d: d.custo_estimado), start=1):
        recomendado = d.provedor == doc.recomendado
        nome = d.provedor.upper() + ("  · recomendada pela análise" if recomendado else "")
        linhas.append(
            [
                _t(nome),
                _t(_dinheiro(d.custo_estimado, doc.moeda)),
                _t(_pct(d.variacao)),
                _t(_dinheiro(d.sem_cotacao, doc.moeda)),
            ]
        )
        if recomendado:
            destaque.append(("BACKGROUND", (0, i), (-1, i), _FUNDO))
    return _tabela(
        ["Destino", "Estimado por mês", "Variação", "Sem cotação"],
        linhas,
        [0.43, 0.21, 0.14, 0.22],
        direita=(1, 2, 3),
        extra=destaque,
    )


def _ponto(cor: Any) -> Drawing:
    d = Drawing(10, 10)
    d.add(Rect(0, 0, 10, 10, rx=5, ry=5, fillColor=cor, strokeColor=None))
    return d


def _nome_do_passo(numero: int, ordem: int | None) -> str:
    if ordem is None:
        return f"Passo {numero} · sem ordem: não depende de nenhuma etapa, migra quando quiser"
    return f"Passo {numero} · {ordem}ª etapa da conta"


def _blocos(doc: Documento) -> list[Any]:
    passos = sum(len(b.passos) for b in doc.blocos)
    saida: list[Any] = [_p("O plano em passos", "h2")]
    saida.append(
        _p(
            f"{passos} {'passo' if passos == 1 else 'passos'}, na ordem em que migram. A migração "
            "principal é o que foi escolhido e o que não funciona sem ele; pré e pós-migração são "
            "grupos incluídos porque conversam com ela. Dentro de cada bloco, a ordem segue as "
            "etapas da conta.",
            "suave",
        )
    )
    for b in doc.blocos:
        primeiro, ultimo = b.passos[0].numero, b.passos[-1].numero
        faixa = f"passo {primeiro}" if primeiro == ultimo else f"passos {primeiro} a {ultimo}"
        cabeca = Table(
            [
                [
                    _ponto(_COR[b.chave]),
                    _p(
                        f"<b>{_t(b.titulo)}</b> <font size='9' color='#5b5b88'>· {faixa}</font>",
                        "h3",
                    ),
                    _p(_t(_dinheiro(b.custo, doc.moeda)), "cel_r"),
                ]
            ],
            colWidths=[6 * mm, _LARGURA - 6 * mm - 40 * mm, 40 * mm],
        )
        cabeca.setStyle(
            TableStyle(
                [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]
            )
        )
        linhas: list[list[Any]] = []
        extra: list[tuple[Any, ...]] = []
        for passo in b.passos:
            linhas.append(
                [_tag(_nome_do_passo(passo.numero, passo.ordem), _COR[b.chave]), "", "", ""]
            )
            extra += [
                ("SPAN", (0, len(linhas)), (-1, len(linhas))),
                ("BACKGROUND", (0, len(linhas)), (-1, len(linhas)), _FUNDO),
            ]
            linhas += [
                [
                    f"<b>{_t(i.nome)}</b>",
                    _tag(i.papel, _COR[b.chave] if i.papel != "vai junto" else _TEXTO),
                    _t(i.detalhe),
                    _t(_dinheiro(i.custo, doc.moeda)),
                ]
                for i in passo.itens
            ]
        tabela = _tabela(
            ["Grupo ou unidade", "Papel", "Por quê", "Custo/mês"],
            linhas,
            [0.28, 0.14, 0.42, 0.16],
            direita=(3,),
            faixa=_COR[b.chave],
            extra=extra,
        )
        saida += [
            Spacer(1, 10),
            KeepTogether([cabeca, _p(_t(b.descricao), "suave"), Spacer(1, 4)]),
            tabela,
        ]
    return saida


def _ordem(doc: Documento) -> list[Any]:
    saida: list[Any] = [CondPageBreak(60 * mm), _p("Ordem de migração", "h2")]
    saida.append(
        _p(
            "As etapas são da conta inteira: a 1ª vai primeiro e quem depende dela vem depois. "
            "A tabela mostra onde o plano cai nessa ordem e o que ficou de fora em cada etapa.",
            "suave",
        )
    )
    if doc.aviso_da_ordem:
        aviso = Table([[_p(_t(doc.aviso_da_ordem), "cel")]], colWidths=[_LARGURA])
        aviso.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _INFO_FUNDO),
                    ("LINEBEFORE", (0, 0), (0, -1), 2.5, _COR[PRE]),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )
        saida += [Spacer(1, 6), aviso]
    linhas, extra = [], []
    for e in doc.etapas:
        nome_etapa = f"{e.ordem}ª" if e.ordem is not None else "Sem ordem"
        if not e.grupos:
            linhas.append(
                [f"<b>{nome_etapa}</b>", '<font color="#5b5b88">nada deste plano</font>', ""]
            )
            continue
        for j, g in enumerate(e.grupos):
            if g.bloco:
                situacao = _tag(NOME_DO_BLOCO[g.bloco], _COR[g.bloco])
                nome = _t(g.nome)
            else:
                situacao = (
                    '<font color="#5b5b88">fora do plano · '
                    + ("o plano chama" if g.lado == "antes" else "chama o plano")
                    + "</font>"
                )
                nome = f'<font color="#5b5b88">{_t(g.nome)}</font>'
            linhas.append([f"<b>{nome_etapa}</b>" if j == 0 else "", nome, situacao])
        if e.ordem is None:
            extra.append(
                ("BACKGROUND", (0, len(linhas) - len(e.grupos) + 1), (-1, len(linhas)), _FUNDO)
            )
    saida += [
        Spacer(1, 6),
        _tabela(["Etapa", "Grupo", "No plano"], linhas, [0.12, 0.6, 0.28], extra=extra),
    ]
    return saida


def _pendencias(doc: Documento) -> list[Any]:
    saida: list[Any] = [CondPageBreak(50 * mm), _p("Fora do plano: o que resolver", "h2")]
    if not doc.pendencias:
        return [
            *saida,
            _p(
                "Nada conversa com o plano por fora dele. O escopo migra sem chamar a nuvem "
                "de origem.",
                "p",
            ),
        ]
    saida.append(
        _p(
            "Dependências fracas: estes grupos conversam com o plano pela rede, mas podem "
            "ficar onde estão. Para cada um, migrar junto ou manter a conexão entre as nuvens "
            "durante a transição.",
            "suave",
        )
    )
    linhas = [
        [
            f"<b>{_t(x.nome)}</b><br/>" + _tag(etapa(x.ordem), _SUAVE),
            ("Precisa estar antes" if x.lado == "antes" else "Depende do que sai")
            + (" · nos dois sentidos" if x.nos_dois_sentidos else ""),
            ("Chamado por " if x.lado == "antes" else "Chama ")
            + _t(", ".join(x.ligado_a))
            + f'<br/><font color="#5b5b88">{_t(x.ligacao)}</font>',
            _t(x.acao),
            _t(_dinheiro(x.custo, doc.moeda)),
        ]
        for x in doc.pendencias
    ]
    return [
        *saida,
        Spacer(1, 6),
        _tabela(
            ["Grupo", "Situação", "Ligação com o plano", "O que fazer", "Custo/mês"],
            linhas,
            [0.22, 0.15, 0.25, 0.24, 0.14],
            direita=(4,),
            faixa=_ALERTA,
        ),
    ]


def _servicos(doc: Documento) -> list[Any]:
    p, rec = doc.plano, doc.recomendado
    nome_rec = rec.upper() if rec else "destino"
    linhas = []
    for s in p.servicos:
        valor = s.destino.get(rec) if rec else None
        como = (
            ", ".join(METODO.get(m, m) for m in s.metodos)
            if valor is not None
            else f'<font color="{_cor_hex(_ALERTA)}">Sem cotação:</font> '
            + _t(s.sem_cotacao.get(rec or "") or "sem preço de referência na análise")
        )
        linhas.append(
            [_t(s.nome), _t(_dinheiro(s.custo, doc.moeda)), _t(_dinheiro(valor, doc.moeda)), como]
        )
    rodape = (
        "Cada recurso é cotado pelo que é: instância pelo pool da família e região dela; banco, "
        "disco e objeto pela razão de preço da categoria. O que fica sem cotação sai da comparação "
        "e está declarado."
    )
    return [
        CondPageBreak(50 * mm),
        _p("Custo por serviço", "h2"),
        _tabela(
            ["Serviço", "Hoje", f"Na {nome_rec}", "Como foi cotado"],
            linhas,
            [0.34, 0.15, 0.15, 0.36],
            direita=(1, 2),
        ),
        Spacer(1, 4),
        _p(rodape, "suave"),
    ]


def _ligacoes(doc: Documento) -> list[Any]:
    if not doc.plano.ligacoes:
        return []
    linhas = [
        [_t(x.de), _t(NATUREZA.get(x.natureza, x.natureza)), _t(x.para), _t(", ".join(x.portas))]
        for x in doc.plano.ligacoes
    ]
    return [
        CondPageBreak(40 * mm),
        _p("Como os serviços do plano se chamam", "h2"),
        _p("Das regras de rede: o que a configuração permite, não o tráfego medido.", "suave"),
        Spacer(1, 4),
        _tabela(
            ["Serviço", "Ligação", "Serviço chamado", "Portas"], linhas, [0.34, 0.18, 0.34, 0.14]
        ),
    ]


def _anexo(doc: Documento) -> list[Any]:
    """Recursos por unidade: o que tem e quanto custa. A lista recurso a recurso vai na planilha."""
    saida: list[Any] = [CondPageBreak(70 * mm), _p("Anexo A · O que migra, por unidade", "h2")]
    saida.append(
        _p(
            f"{len(doc.recursos)} recursos do inventário, na ordem do plano. A lista recurso a "
            "recurso, com identificadores, está na planilha do plano (Exportar plano · Planilha).",
            "suave",
        )
    )
    por_unidade: dict[tuple[str, int | None, str], list[Any]] = {}
    for r in doc.recursos:
        por_unidade.setdefault((r.bloco, r.ordem, r.unidade), []).append(r)
    linhas: list[list[Any]] = []
    extra: list[tuple[Any, ...]] = []
    atual = None
    for (bloco, ordem, unidade), recursos in por_unidade.items():
        if bloco != atual:
            atual = bloco
            linhas.append([_tag(NOME_DO_BLOCO[bloco], _COR[bloco]), "", "", "", ""])
            extra += [
                ("SPAN", (0, len(linhas)), (-1, len(linhas))),
                ("BACKGROUND", (0, len(linhas)), (-1, len(linhas)), _FUNDO),
            ]
        tipos: dict[str, int] = {}
        for r in recursos:
            tipos[r.tipo] = tipos.get(r.tipo, 0) + 1
        regioes = sorted({r.regiao for r in recursos if r.regiao})
        linhas.append(
            [
                _t(etapa(ordem)),
                f"<b>{_t(unidade)}</b>",
                _t(
                    ", ".join(
                        contagem(tipo, n)
                        for tipo, n in sorted(tipos.items(), key=lambda kv: -kv[1])
                    )
                ),
                _t(", ".join(regioes) or "—"),
                _t(_dinheiro(sum(r.custo for r in recursos), doc.moeda)),
            ]
        )
    saida += [
        Spacer(1, 6),
        _tabela(
            ["Etapa", "Unidade", "Recursos", "Região", "Custo/mês"],
            linhas,
            [0.11, 0.25, 0.37, 0.12, 0.15],
            direita=(4,),
            extra=extra,
        ),
    ]
    saida += [Spacer(1, 14), _p("Anexo B · Premissas e limites", "h2")]
    saida += [_p("• " + _t(x), "p") for x in doc.premissas]
    return saida


def montar_pdf(doc: Documento) -> bytes:
    saida = BytesIO()
    rodape_titulo = f"Plano de migração · {doc.titulo}"

    class _Paginado(Canvas):  # type: ignore[misc]
        """Escreve "página X de Y" depois de saber quantas páginas há."""

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self._paginas: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 (API do reportlab)
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._paginas)
            for estado in self._paginas:
                self.__dict__.update(estado)
                self.setFont("Helvetica", 7.5)
                self.setFillColor(_SUAVE)
                self.drawString(_MARGEM, 10 * mm, rodape_titulo)
                self.drawRightString(
                    A4[0] - _MARGEM, 10 * mm, f"página {self._pageNumber} de {total}"
                )
                self.setStrokeColor(_BORDA)
                self.line(_MARGEM, 13 * mm, A4[0] - _MARGEM, 13 * mm)
                super().showPage()
            super().save()

    pdf = SimpleDocTemplate(
        saida,
        pagesize=A4,
        leftMargin=_MARGEM,
        rightMargin=_MARGEM,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=rodape_titulo,
        author="OneShift",
        subject="Plano de migração",
    )
    rec = doc.recomendado.upper() if doc.recomendado else None
    meta = " · ".join(
        x
        for x in (
            f"Fatura de {doc.periodo}" if doc.periodo else None,
            f"{doc.origem} para {rec} (recomendada pela análise)" if rec else doc.origem,
            f"gerado em {doc.gerado_em.strftime('%d/%m/%Y %H:%M')} UTC",
            f"análise {doc.job_id}",
        )
        if x
    )
    historia: list[Any] = [
        _p("ONESHIFT", "marca"),
        Spacer(1, 4),
        _p(f"Plano de migração: {_t(doc.titulo)}", "titulo"),
        _p(_t(doc.escolha.capitalize()), "sub"),
        Spacer(1, 2),
        _p(_t(meta), "meta"),
        Spacer(1, 12),
        _kpis(doc),
        *_composicao(doc),
        Spacer(1, 10),
        _destinos(doc),
        *_blocos(doc),
        *_ordem(doc),
        *_pendencias(doc),
        *_servicos(doc),
        *_ligacoes(doc),
        *_anexo(doc),
    ]
    pdf.build(historia, canvasmaker=_Paginado)
    return saida.getvalue()
