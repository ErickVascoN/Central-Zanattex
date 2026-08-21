"""
Gerador de relatórios PDF de Produção Diária (facções/externos e colaboradores/
internos), na identidade visual da Central Zanattex.

Sem dependências nativas — usa apenas reportlab (pip puro, roda no Windows).
Os indicadores são os mesmos exibidos nos dashboards; aqui só muda a estrutura
(layout impresso) e a "casca" da marca (wordmark, paleta navy/vermelho, cards
de KPI, barras de meta).
"""

from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.colors import HexColor, Color
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
    KeepTogether,
)

# ── Paleta da marca (mesma do static/css/app.css) ────────────────────────────
NAVY = HexColor("#172554")
NAVY_DEEP = HexColor("#0f172a")
NAVY_SOFT = HexColor("#1e3a8a")
RED = HexColor("#dc2626")
BG = HexColor("#f4f6fb")
CARD = HexColor("#ffffff")
BORDER = HexColor("#e2e8f0")
INK = HexColor("#1e293b")
MUTED = HexColor("#64748b")
FAINT = HexColor("#6b7280")
GOOD = HexColor("#059669")
WARN = HexColor("#d97706")
CRIT = HexColor("#be123c")
GOOD_BG = HexColor("#d1fae5")
WARN_BG = HexColor("#fef3c7")
CRIT_BG = HexColor("#ffe4e6")
NEUTRO_BG = HexColor("#f1f5f9")
ZEBRA = HexColor("#f8fafc")
TRACK = HexColor("#eef2f7")

PAGE_W, PAGE_H = A4
MARGIN = 1.6 * cm


def _fmt(v) -> str:
    """Inteiro com separador de milhar pt-BR."""
    try:
        return f"{int(round(float(v))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _fmt_moeda(v) -> str:
    """Valor monetário com separador de milhar/decimal pt-BR (ex.: 1.234,50)."""
    try:
        s = f"{float(v):,.2f}"
        inteiro, decimal = s.split(".")
        return inteiro.replace(",", ".") + "," + decimal
    except (TypeError, ValueError):
        return "—"


def _cor_status(status: str) -> Color:
    return {"good": GOOD, "warn": WARN, "crit": CRIT}.get(status, NAVY_SOFT)


def _cor_status_bg(status: str) -> Color:
    return {"good": GOOD_BG, "warn": WARN_BG, "crit": CRIT_BG}.get(status, NEUTRO_BG)


def _hx(cor: Color) -> str:
    """Cor reportlab → string '#rrggbb' para uso inline em <font color=...>."""
    return "#" + cor.hexval()[2:]


def _status_pct(pct):
    if pct is None:
        return "neutro"
    if pct >= 100:
        return "good"
    if pct >= 80:
        return "warn"
    return "crit"


# ── Estilos de parágrafo ─────────────────────────────────────────────────────
def _estilos() -> dict:
    return {
        "secao": ParagraphStyle(
            "secao", fontName="Helvetica-Bold", fontSize=12.5, textColor=NAVY,
            spaceBefore=2, spaceAfter=2, leading=15,
        ),
        "sub": ParagraphStyle(
            "sub", fontName="Helvetica", fontSize=8.5, textColor=FAINT,
            spaceAfter=6, leading=11,
        ),
        "cell": ParagraphStyle(
            "cell", fontName="Helvetica", fontSize=8.5, textColor=INK, leading=11,
        ),
        "nota": ParagraphStyle(
            "nota", fontName="Helvetica", fontSize=9, textColor=INK, leading=13,
        ),
        "subnavy": ParagraphStyle(
            "subnavy", fontName="Helvetica-Bold", fontSize=9, textColor=CARD, leading=12,
        ),
        "meta_big": ParagraphStyle(
            "meta_big", fontName="Helvetica-Bold", fontSize=20, textColor=NAVY,
            leading=24,
        ),
        "cell_r": ParagraphStyle(
            "cell_r", fontName="Helvetica", fontSize=8.5, textColor=INK,
            leading=11, alignment=TA_RIGHT,
        ),
        "th": ParagraphStyle(
            "th", fontName="Helvetica-Bold", fontSize=8, textColor=CARD, leading=10,
        ),
        "th_r": ParagraphStyle(
            "th_r", fontName="Helvetica-Bold", fontSize=8, textColor=CARD,
            leading=10, alignment=TA_RIGHT,
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label", fontName="Helvetica-Bold", fontSize=6.8, textColor=FAINT,
            leading=9, spaceAfter=2,
        ),
        "kpi_val": ParagraphStyle(
            "kpi_val", fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, leading=17,
        ),
        "wm": ParagraphStyle(
            "wm", fontName="Helvetica-Bold", fontSize=17, textColor=CARD, leading=18,
        ),
        "wm_tag": ParagraphStyle(
            "wm_tag", fontName="Helvetica", fontSize=6.5, textColor=HexColor("#cbd5e1"),
            leading=8,
        ),
        "rel_tit": ParagraphStyle(
            "rel_tit", fontName="Helvetica-Bold", fontSize=13, textColor=CARD,
            leading=15, alignment=TA_RIGHT,
        ),
        "rel_sub": ParagraphStyle(
            "rel_sub", fontName="Helvetica", fontSize=8, textColor=HexColor("#cbd5e1"),
            leading=11, alignment=TA_RIGHT,
        ),
    }


# ── Faixa de cabeçalho (wordmark + título do relatório) ──────────────────────
def _faixa_marca(titulo: str, subtitulo: str, periodo: str, gerado_em: str,
                 filtros: str, e: dict) -> Table:
    largura = PAGE_W - 2 * MARGIN
    # Wordmark ZANATTEX com Z e X em vermelho
    wm = Paragraph(
        '<font color="#dc2626"><b>Z</b></font>ANATTE'
        '<font color="#dc2626"><b>X</b></font>', e["wm"],
    )
    tag = Paragraph("CENTRAL DE DADOS", e["wm_tag"])
    # tabelas internas descontam o padding (14 de cada lado) da faixa externa
    col_esq = largura * 0.40 - 28
    col_dir = largura * 0.60 - 28
    esquerda = Table([[wm], [tag]], colWidths=[col_esq])
    esquerda.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (0, 0), 2), ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 1), (0, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
    ]))

    linhas_dir = [
        [Paragraph(titulo, e["rel_tit"])],
        [Paragraph(subtitulo, e["rel_sub"])],
        [Paragraph(f"Período: <b>{periodo}</b>", e["rel_sub"])],
    ]
    if filtros:
        linhas_dir.append([Paragraph(filtros, e["rel_sub"])])
    linhas_dir.append([Paragraph(f"Gerado em {gerado_em}", e["rel_sub"])])
    direita = Table(linhas_dir, colWidths=[col_dir])
    direita.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    faixa = Table([[esquerda, direita]], colWidths=[largura * 0.40, largura * 0.60])
    faixa.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LINEABOVE", (0, 0), (-1, 0), 3, RED),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return faixa


def _subheader_navy(texto: str, e: dict) -> Table:
    """Barra navy de largura total com texto branco em negrito (cabeçalho de
    grupo dentro de uma seção — ex.: cada facção no detalhamento)."""
    largura = PAGE_W - 2 * MARGIN
    t = Table([[Paragraph(texto, e["subnavy"])]], colWidths=[largura])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBEFORE", (0, 0), (0, -1), 3, RED),
    ]))
    return t


def _titulo_secao(texto: str, e: dict) -> Table:
    """Título de seção com o acento vermelho embaixo (como os chart-t do app)."""
    largura = PAGE_W - 2 * MARGIN
    t = Table([[Paragraph(texto, e["secao"])]], colWidths=[largura])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 2, RED),
    ]))
    return t


# ── Cards de KPI ─────────────────────────────────────────────────────────────
def _bloco_kpis(kpis: list[tuple[str, str]], e: dict, colunas: int = 4) -> Table:
    """kpis: lista de (label, valor). Renderiza cards brancos com borda."""
    largura = PAGE_W - 2 * MARGIN
    gap = 0.3 * cm
    col_w = (largura - gap * (colunas - 1)) / colunas

    def _card(label, valor):
        c = Table([[Paragraph(label.upper(), e["kpi_label"])],
                   [Paragraph(valor, e["kpi_val"])]], colWidths=[col_w])
        c.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CARD),
            ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
            ("LINEBEFORE", (0, 0), (0, -1), 2, RED),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (0, 0), 7), ("BOTTOMPADDING", (0, 1), (0, 1), 8),
            ("TOPPADDING", (0, 1), (0, 1), 0),
        ]))
        return c

    linhas, linha = [], []
    for i, (label, valor) in enumerate(kpis):
        linha.append(_card(label, valor))
        if len(linha) == colunas:
            linhas.append(linha)
            linha = []
    if linha:
        while len(linha) < colunas:
            linha.append("")
        linhas.append(linha)

    # intercala espaçadores entre colunas
    col_widths = []
    for i in range(colunas):
        col_widths.append(col_w)
        if i < colunas - 1:
            col_widths.append(gap)
    dados = []
    for linha in linhas:
        row = []
        for i, cel in enumerate(linha):
            row.append(cel)
            if i < colunas - 1:
                row.append("")
        dados.append(row)

    t = Table(dados, colWidths=col_widths)
    estilo = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), gap),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    t.setStyle(TableStyle(estilo))
    return t


# ── Barra de meta (Realizado × Meta) ─────────────────────────────────────────
def _barra_meta(pct, status: str, largura=None, altura=0.42 * cm) -> Drawing:
    if largura is None:
        largura = PAGE_W - 2 * MARGIN
    d = Drawing(largura, altura)
    d.add(Rect(0, 0, largura, altura, fillColor=TRACK, strokeColor=None, rx=5, ry=5))
    if pct is not None:
        w = largura * min(float(pct), 100.0) / 100.0
        if w > 0:
            d.add(Rect(0, 0, max(w, 6), altura, fillColor=_cor_status(status),
                       strokeColor=None, rx=5, ry=5))
    return d


def _banner_meta(pct, realizado, meta, e: dict) -> Table:
    largura = PAGE_W - 2 * MARGIN
    status = _status_pct(pct)
    if pct is not None:
        titulo = Paragraph(
            f'<font color="{_hx(_cor_status(status))}">{pct:.1f}%</font>'
            f'&nbsp;&nbsp;<font size="9" color="#64748b">'
            f'{_fmt(realizado)} / {_fmt(meta)} pçs</font>', e["meta_big"],
        )
        barra = _barra_meta(pct, status)
        corpo = [[titulo], [Spacer(1, 3)], [barra]]
    else:
        corpo = [[Paragraph(
            '<font size="10" color="#64748b">Sem meta cadastrada para o período.'
            '</font>', e["cell"])]]
    inner = Table(corpo, colWidths=[largura - 0.6 * cm])
    inner.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    card = Table([[inner]], colWidths=[largura])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, _cor_status(status)),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return card


# ── Largura de coluna calculada pelo texto real ──────────────────────────────
_PAD_CELULA = 14  # pt — LEFTPADDING + RIGHTPADDING usados em _tabela


def _largura_min_coluna(cabecalho: str, exemplo: str = "") -> float:
    """Largura mínima (pt) pro cabeçalho (negrito 8) e um valor de exemplo
    (regular 8.5) caberem numa linha só, já com o padding da célula. Usado em
    tabelas com número de colunas variável (ex.: Detalhamento por etapa do
    processo), pra não precisar reajustar fração de largura na mão toda vez
    que uma coluna nova entrar."""
    w_cab = stringWidth(cabecalho, "Helvetica-Bold", 8)
    w_val = stringWidth(exemplo, "Helvetica", 8.5) if exemplo else 0
    return max(w_cab, w_val) + _PAD_CELULA


def _larguras_auto(colunas: list[tuple[str, str]], largura_total: float,
                    coluna_flex: int = -1) -> list[float]:
    """`colunas`: lista de (cabeçalho, valor de exemplo). Cada coluna recebe
    a largura mínima pra não quebrar o cabeçalho; a `coluna_flex` (índice,
    Observações por padrão) absorve o espaço que sobrar. Se nem os mínimos
    couberem (excesso de colunas), encolhe tudo proporcionalmente."""
    minimos = [_largura_min_coluna(cab, ex) for cab, ex in colunas]
    total_min = sum(minimos)
    if total_min > largura_total:
        escala = largura_total / total_min
        return [m * escala for m in minimos]
    larguras = list(minimos)
    larguras[coluna_flex] += largura_total - total_min
    return larguras


# ── Tabela genérica (header navy + zebra) ────────────────────────────────────
def _tabela(cabecalho: list, linhas: list[list], larguras: list, e: dict,
            aligns: list | None = None, pct_col: int | None = None,
            pct_status_por_linha: list | None = None) -> Table:
    """Tabela estilizada. `pct_col` pinta a célula (texto + fundo) por status
    (good/warn/crit) — a cor tem que ir dentro do Paragraph mesmo (markup
    <font color=...>), porque o TEXTCOLOR do TableStyle não tem efeito quando
    o conteúdo da célula é um Paragraph (o texto já nasce com cor própria)."""
    th_style = e["th"]
    head = []
    for i, c in enumerate(cabecalho):
        st = e["th_r"] if (aligns and aligns[i] == "r") else th_style
        head.append(Paragraph(str(c), st))
    dados = [head]
    for ri, linha in enumerate(linhas):
        row = []
        for i, val in enumerate(linha):
            st = e["cell_r"] if (aligns and aligns[i] == "r") else e["cell"]
            if i == pct_col and pct_status_por_linha:
                cor = _hx(_cor_status(pct_status_por_linha[ri]))
                row.append(Paragraph(f'<font color="{cor}"><b>{val}</b></font>', st))
            else:
                row.append(Paragraph(str(val), st))
        dados.append(row)

    t = Table(dados, colWidths=larguras, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 1), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD, ZEBRA]),
        ("LINEBELOW", (0, 0), (-1, 0), 0, NAVY),
    ]
    if pct_col is not None and pct_status_por_linha:
        for ri, status in enumerate(pct_status_por_linha, start=1):
            estilo.append(("BACKGROUND", (pct_col, ri), (pct_col, ri), _cor_status_bg(status)))
    t.setStyle(TableStyle(estilo))
    return t


# ── Decoração de página (rodapé + numeração) ─────────────────────────────────
def _rodape(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.05 * cm, PAGE_W - MARGIN, 1.05 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(FAINT)
    canvas.drawString(MARGIN, 0.65 * cm, "Zanattex Indústria · Arealva/SP")
    canvas.drawRightString(PAGE_W - MARGIN, 0.65 * cm, f"Página {doc.page}")
    canvas.restoreState()


def _construir(story: list, titulo: str = "Relatório · Zanattex") -> bytes:
    """`titulo` vira o metadado /Title do PDF — sem ele, a aba do navegador
    mostra "(anonymous)" ao abrir o PDF direto (a URL do relatório não termina
    em .pdf, então o Chrome não tem de onde tirar um nome pra aba)."""
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=1.4 * cm,
        title=titulo, author="Zanattex — Central de Dados",
    )
    frame = Frame(MARGIN, 1.4 * cm, PAGE_W - 2 * MARGIN,
                  PAGE_H - MARGIN - 1.4 * cm, id="corpo",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="principal", frames=[frame],
                                       onPage=_rodape)])
    doc.build(story)
    return buf.getvalue()


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — PRODUÇÃO POR FACÇÃO (EXTERNOS)
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_faccoes(*, periodo_label: str, filtros: str,
                      kpis: list[tuple[str, str]], meta: dict | None,
                      ranking_faccao: list[dict], alertas: dict,
                      visao_geral: list[dict], detalhamento: list[dict],
                      producao_diaria: list[dict], meta_dia_total,
                      mix_produtos: list[dict],
                      processo_detalhado: dict | None = None,
                      dia_unico: bool = False) -> bytes:
    """Relatório de Produção · Facções, com a mesma estrutura de indicadores do
    original (Streamlit), no design da nova Central. Seções: Resumo Executivo,
    Realizado × Meta, Detalhamento do Processo (opcional, só com 1 facção no
    escopo), Facção × Meta, Painel de Alertas, Visão Geral por Empresa/
    Produto, Detalhamento por Produto/Empresa/Facção, Produção Diária e Mix."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    largura = PAGE_W - 2 * MARGIN
    story: list = [
        _faixa_marca("Relatório de Produção · Facções",
                     "Acompanhamento de produção — facções externas",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    # ── Resumo Executivo (KPIs) ──────────────────────────────────────────────
    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=(len(kpis) if dia_unico else 4)))

    # ── Realizado × Meta ─────────────────────────────────────────────────────
    if meta is not None:
        story.append(Spacer(1, 0.2 * cm))
        story.append(_titulo_secao(
            "Realizado × Meta do dia" if dia_unico else "Realizado × Meta do período", e))
        story.append(Spacer(1, 0.3 * cm))
        story.append(_banner_meta(meta.get("pct"), meta.get("realizado"),
                                  meta.get("meta"), e))

    # ── Detalhamento do processo (por etapa) ─────────────────────────────────
    # Só aparece com exatamente 1 facção no escopo do relatório — hoje é a
    # GGTTEX Rute, que a partir de 03/08/2026 passou a informar quanto
    # produziu em cada etapa (ver ETAPAS_PROCESSO em producao/faccao_loader)
    # além do total de Jogos de Cama, pra dar noção do processo inteiro.
    if processo_detalhado:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao(
            f"Detalhamento do processo — {processo_detalhado['faccao']}", e))
        story.append(Paragraph(
            "Quantidade produzida em cada etapa no período, além do total já "
            "mostrado no resumo executivo", e["sub"]))
        story.append(Spacer(1, 0.2 * cm))
        etapas = processo_detalhado["etapas"]
        story.append(_bloco_kpis(
            [(label, _fmt(qtd) + " pçs") for label, qtd in etapas],
            e, colunas=len(etapas)))

    # ── Facção × Meta ────────────────────────────────────────────────────────
    if ranking_faccao:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Facção × Meta", e))
        story.append(Paragraph(
            "Produzido, participação e atingimento por facção no dia" if dia_unico else
            "Produzido, participação e atingimento por facção no período", e["sub"]))
        if dia_unico:
            # Sem "Meta Período"/"Média/Dia"/"Saldo da Meta" — com 1 dia só,
            # esses três colapsam no mesmo valor de "Meta/Dia" e "Produzido"
            # (viram ruído redundante em vez de informação nova).
            cab = ["Facção", "Produzido", "Produtos", "Meta/Dia", "% Meta", "Última data"]
            aligns = ["l", "r", "l", "r", "r", "r"]
            cw = [largura * x for x in (0.17, 0.13, 0.24, 0.13, 0.12, 0.16)]
            pct_col = 4
        else:
            cab = ["Facção", "Produzido", "Produtos", "Meta Período", "Meta/Dia", "Média/Dia",
                   "% Meta", "Saldo da Meta", "Última data"]
            aligns = ["l", "r", "l", "r", "r", "r", "r", "r", "r"]
            cw = [largura * x for x in (0.13, 0.11, 0.145, 0.10, 0.095, 0.105, 0.09, 0.095, 0.13)]
            pct_col = 6
        linhas, status = [], []
        for r in ranking_faccao:
            tem = r["meta"] > 0
            produtos = r.get("produtos") or []
            produtos_txt = "<br/>".join(
                f"{_xml_escape(p['produto'])}: {_fmt(p['quantidade'])}" for p in produtos
            ) if produtos else "—"
            if dia_unico:
                row = [
                    r["nome"], _fmt(r["produzido"]), produtos_txt,
                    _fmt(r["meta_dia"]) if tem else "—",
                    f"{r['pct']:.0f}%" if (tem and r["pct"] is not None) else "sem meta",
                    r.get("ultima_data") or "—",
                ]
            else:
                row = [
                    r["nome"], _fmt(r["produzido"]), produtos_txt,
                    _fmt(r["meta"]) if tem else "—",
                    _fmt(r["meta_dia"]) if tem else "—",
                    _fmt(r["media_dia"]) if (tem and r["media_dia"] is not None) else "—",
                    f"{r['pct']:.0f}%" if (tem and r["pct"] is not None) else "sem meta",
                    _fmt(r["restante"]) if (tem and r["restante"] is not None) else "—",
                    r.get("ultima_data") or "—",
                ]
            linhas.append(row)
            status.append(_status_pct(r["pct"]) if tem else "neutro")
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=pct_col,
                             pct_status_por_linha=status))

    # ── Painel de Alertas ────────────────────────────────────────────────────
    _blocos_alerta = _bloco_alertas(alertas, e)
    if _blocos_alerta:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Painel de alertas — Facção × Meta", e))
        story.append(Spacer(1, 0.2 * cm))
        story.extend(_blocos_alerta)

    # ── Visão Geral por Empresa / Produto ────────────────────────────────────
    if visao_geral:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Visão geral por empresa / produto", e))
        story.append(Paragraph(
            "Produção por produto, quebrada por facção e cliente · Média/Dia sobre "
            "os dias úteis do período", e["sub"]))
        cab = ["Facção", "Cliente", "Produzido", "Média/Dia"]
        cw = [largura * x for x in (0.34, 0.34, 0.16, 0.16)]
        for g in visao_geral:
            story.append(Spacer(1, 0.18 * cm))
            story.append(_subheader_navy(
                f"{g['produto']}  ·  Total: {_fmt(g['total_produto'])} pçs  ·  "
                f"Média/Dia: {_fmt(g['media_dia_produto'])} pçs", e))
            linhas = [[l["faccao"], l["cliente"], _fmt(l["produzido"]),
                       _fmt(l["media_dia"])] for l in g["linhas"]]
            story.append(_tabela(cab, linhas, cw, e, aligns=["l", "l", "r", "r"]))

    # ── Detalhamento por Produto / Empresa / Facção ──────────────────────────
    if detalhamento:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Detalhamento por produto / empresa / facção", e))
        story.append(Paragraph("Detalhe dia a dia do que cada facção produziu", e["sub"]))
        cab_padrao = ["Data", "Produto", "Empresa", "Produzido", "Observações"]
        cw_padrao = [largura * x for x in (0.13, 0.28, 0.24, 0.13, 0.22)]
        # Facções com etapa do processo informada (ver ETAPAS_PROCESSO em
        # producao/faccao_loader — hoje só a GGTTEX Rute) ganham colunas a
        # mais nessa tabela, pra acompanhar dia a dia; as demais seguem como
        # sempre. Largura de cada coluna calculada pelo texto real (cabeçalho
        # + valor de exemplo), então uma etapa nova entra sem precisar
        # reajustar fração na mão de novo.
        cab_etapas = cw_etapas = None
        for d in detalhamento:
            if d.get("colunas_etapa"):
                cab_etapas = ["Data", "Produto", "Empresa", "Produzido"] + d["colunas_etapa"] + ["Observações"]
                colunas_larg = (
                    [("Data", "00/00/0000"), ("Produto", "Jogo De Cama"),
                     ("Empresa", "Niazittex"), ("Produzido", "000.000")]
                    + [(lbl, "00.000") for lbl in d["colunas_etapa"]]
                    + [("Observações", "")]
                )
                cw_etapas = _larguras_auto(colunas_larg, largura)
                break
        aligns_etapas = None
        if cab_etapas:
            aligns_etapas = ["l", "l", "l", "r"] + ["r"] * len(cab_etapas[4:-1]) + ["l"]
        for d in detalhamento:
            story.append(Spacer(1, 0.18 * cm))
            if d.get("meta"):
                hdr = (f"{d['nome']}  ·  Produzido: {_fmt(d['produzido'])}  |  "
                       f"Meta/Dia: {_fmt(d['meta_dia'])}  |  Meta Período: "
                       f"{_fmt(d['meta'])}  |  % Meta: "
                       f"{d['pct']:.1f}%" if d.get("pct") is not None else
                       f"{d['nome']}  ·  Produzido: {_fmt(d['produzido'])}")
            else:
                hdr = f"{d['nome']}  ·  Produzido: {_fmt(d['produzido'])}  |  sem meta cadastrada"
            story.append(_subheader_navy(hdr, e))
            if d.get("colunas_etapa"):
                linhas = [[l["data"], l["produto"], l["empresa"], _fmt(l["produzido"]),
                           *[_fmt(v) for v in l["etapas"]], l["obs"]] for l in d["linhas"]]
                story.append(_tabela(cab_etapas, linhas, cw_etapas, e, aligns=aligns_etapas))
            else:
                linhas = [[l["data"], l["produto"], l["empresa"], _fmt(l["produzido"]),
                           l["obs"]] for l in d["linhas"]]
                story.append(_tabela(cab_padrao, linhas, cw_padrao, e,
                                    aligns=["l", "l", "l", "r", "l"]))

    # ── Produção Diária ──────────────────────────────────────────────────────
    if producao_diaria:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Produção diária", e))
        md = int(meta_dia_total or 0)
        story.append(Paragraph(
            f"Peças por dia" + (f" · Meta/dia: {_fmt(md)} pçs" if md else ""), e["sub"]))
        cab = ["Dia", "Produzido"] + (["Meta", "% da Meta/dia"] if md else [])
        aligns = ["l", "r"] + (["r", "r"] if md else [])
        cw = ([largura * 0.28, largura * 0.24, largura * 0.24, largura * 0.24] if md
              else [largura * 0.6, largura * 0.4])
        linhas, status = [], []
        for p in producao_diaria:
            row = [p["dia"], _fmt(p["qtd"])]
            if md:
                pct = round(p["qtd"] / md * 100, 0)
                row.append(_fmt(md))
                row.append(f"{pct:.0f}%")
                status.append(_status_pct(pct))
            linhas.append(row)
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns,
                            pct_col=3 if md else None,
                            pct_status_por_linha=status if md else None))

    # ── Mix de Produtos ──────────────────────────────────────────────────────
    if mix_produtos:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Mix de produtos", e))
        story.append(Paragraph("Participação de cada produto no total do período", e["sub"]))
        cab = ["Produto", "Produzido", "% do Total"]
        cw = [largura * 0.5, largura * 0.25, largura * 0.25]
        linhas = [[m["produto"], _fmt(m["qtd"]), f"{m['pct']:.1f}%"] for m in mix_produtos]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    return _construir(story, titulo="Relatório de Produção · Facções" + f" — {periodo_label}")


def _bloco_alertas(alertas: dict, e: dict) -> list:
    """Monta os parágrafos do Painel de Alertas (Facção × Meta), como no
    original: desatualizadas, sem dado, melhor/pior, sem meta e meta suspeita."""
    if not alertas:
        return []
    blocos = []

    desat = alertas.get("desatualizadas") or []
    if desat:
        txt = ", ".join(f"{n} (até {d}, {dias}d)" for n, d, dias in desat[:8])
        blocos.append(Paragraph(
            f'<font color="{_hx(WARN)}"><b>Desatualizadas</b></font> — enviaram '
            f"dado, mas não dos últimos dias: {txt}.", e["nota"]))

    sem_dado = alertas.get("sem_dado") or []
    if sem_dado:
        blocos.append(Paragraph(
            f'<font color="{_hx(CRIT)}"><b>Sem nenhum dado no período</b></font> — '
            f"facções com meta mas sem lançamento: {', '.join(sem_dado[:8])}.", e["nota"]))

    melhor = alertas.get("melhor")
    pior = alertas.get("pior")
    if melhor or pior:
        partes = []
        if melhor:
            partes.append(
                f'<font color="{_hx(GOOD)}"><b>Melhor desempenho:</b></font> '
                f"{melhor['nome']} — {melhor['pct']:.1f}% da meta "
                f"({_fmt(melhor['produzido'])} de {_fmt(melhor['meta'])})")
        if pior:
            partes.append(
                f'<font color="{_hx(CRIT)}"><b>Maior atenção:</b></font> '
                f"{pior['nome']} — {pior['pct']:.1f}% da meta "
                f"({_fmt(pior['produzido'])} de {_fmt(pior['meta'])})")
        blocos.append(Paragraph("<br/>".join(partes), e["nota"]))

    sem_meta = alertas.get("sem_meta") or []
    if sem_meta:
        nomes = ", ".join(f"{n} ({_fmt(q)})" for n, q in sem_meta[:6])
        blocos.append(Paragraph(
            f'<font color="{_hx(WARN)}"><b>Sem meta configurada</b></font> '
            f"(produziram, mas não entram no % da meta): {nomes}.", e["nota"]))

    suspeita = alertas.get("suspeita") or []
    if suspeita:
        nomes = ", ".join(f"{n} ({p:.0f}%)" for n, p in suspeita[:6])
        blocos.append(Paragraph(
            f'<font color="{_hx(WARN)}"><b>Meta possivelmente incompleta</b></font> '
            f"(% acima de 200% — revisar planilha): {nomes}.", e["nota"]))

    # espaça os parágrafos
    espacado = []
    for i, b in enumerate(blocos):
        espacado.append(b)
        if i < len(blocos) - 1:
            espacado.append(Spacer(1, 0.18 * cm))
    return espacado


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — PRODUÇÃO POR COLABORADOR (INTERNOS)
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_colaboradores(*, unidade_label: str, periodo_label: str,
                            kpis: list[tuple[str, str]], meta: dict | None,
                            ranking_colab: list[dict], consistencia: list[dict],
                            breakdowns: list[dict], colaborador_unico: bool = False,
                            producao_diaria: list[dict] | None = None,
                            meta_dia: int = 0,
                            premiacao_colaboradores: list[dict] | None = None,
                            premiacao_totais: dict | None = None,
                            premiacao_diaria: list[dict] | None = None,
                            premiacao_dias_uteis: int | None = None) -> bytes:
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    largura = PAGE_W - 2 * MARGIN
    story: list = [
        _faixa_marca(f"Relatório de Produção · {unidade_label}",
                     "Acompanhamento de produção — colaboradores internos",
                     periodo_label, gerado_em, "", e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo geral", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=4))

    if meta is not None:
        story.append(Spacer(1, 0.2 * cm))
        story.append(_titulo_secao("Realizado × Meta do período", e))
        story.append(Spacer(1, 0.3 * cm))
        story.append(_banner_meta(meta.get("pct"), meta.get("realizado"),
                                  meta.get("meta"), e))

    # Ranking de colaboradores — omitido com 1 colaborador filtrado (sempre 100%, sem valor)
    if ranking_colab and not colaborador_unico:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Ranking de colaboradores", e))
        story.append(Paragraph("Produção e participação no total do período", e["sub"]))
        cab = ["Colaborador", "Produzido", "% do Total"]
        cw = [largura * 0.5, largura * 0.25, largura * 0.25]
        linhas = [[r["nome"], _fmt(r["produzido"]), f"{r['pct_total']:.1f}%"]
                  for r in ranking_colab]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    # ── Premiação por Produtividade (ANEXO I — GGTTEX Jogos/Fronha) ──────────
    # Vem antes de Consistência: é a informação mais importante do relatório
    # para o fechamento de mês do RH.
    if premiacao_totais and premiacao_totais.get("colaboradores"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Premiação por produtividade", e))
        story.append(Paragraph(
            "Meta por atividade (ANEXO I) × produzido em dias úteis · valor de "
            "R$/peça excedente conforme regra vigente", e["sub"]))
        story.append(Spacer(1, 0.3 * cm))
        rotulo_meta = "Meta do período"
        if premiacao_dias_uteis:
            rotulo_meta += f" ({premiacao_dias_uteis} dias úteis)"
        story.append(_bloco_kpis([
            ("Produzido (dias úteis)", _fmt(premiacao_totais["produzido"]) + " pçs"),
            (rotulo_meta, _fmt(premiacao_totais["meta"]) + " pçs"),
            ("Peças excedentes", _fmt(premiacao_totais["excedente"]) + " pçs"),
            ("A bonificar (dias úteis)", "R$ " + _fmt_moeda(premiacao_totais["valor"])),
        ], e, colunas=4))

        # Sábado é pago à parte: não tem meta (a meta do período conta só dias
        # úteis), então cada peça vale o mesmo R$/peça da atividade.
        if premiacao_totais.get("produzido_sabado"):
            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph(
                "Sábado — sem meta: cada peça vale o mesmo R$/peça da atividade", e["sub"]))
            story.append(Spacer(1, 0.2 * cm))
            story.append(_bloco_kpis([
                ("Produzido no sábado", _fmt(premiacao_totais["produzido_sabado"]) + " pçs"),
                ("A pagar pelo sábado", "R$ " + _fmt_moeda(premiacao_totais["valor_sabado"])),
                ("Total geral a bonificar", "R$ " + _fmt_moeda(premiacao_totais["valor_total"])),
            ], e, colunas=3))

        if colaborador_unico:
            # Meta e bônus fecham por período (dias úteis × meta diária vs.
            # total do período) — a tabela por atividade é o "extrato" do
            # cálculo; o ritmo diário abaixo é só contexto, sem bônus por dia.
            if premiacao_colaboradores:
                atividades = premiacao_colaboradores[0]["atividades"]
                # Colunas de sábado só aparecem se houve produção no sábado —
                # senão a tabela fica larga à toa.
                tem_sab = any(a.get("produzido_sabado") for a in atividades)
                story.append(Spacer(1, 0.3 * cm))
                story.append(Paragraph("Fechamento por atividade", e["sub"]))
                cab = ["Atividade", "Produzido", "Meta do período", "% Meta",
                       "Excedente", "Bônus R$"]
                if tem_sab:
                    cab += ["Sáb pçs", "Sáb R$"]
                    pesos = (0.20, 0.12, 0.14, 0.09, 0.11, 0.12, 0.10, 0.12)
                else:
                    pesos = (0.24, 0.16, 0.18, 0.12, 0.14, 0.16)
                cw = [largura * x for x in pesos]
                aligns = ["l"] + ["r"] * (len(cab) - 1)
                linhas, status = [], []
                for a in atividades:
                    linha = [
                        a["atividade"], _fmt(a["produzido"]), _fmt(a["meta"]),
                        f"{a['pct']:.0f}%" if a["pct"] is not None else "—",
                        _fmt(a["excedente"]), "R$ " + _fmt_moeda(a["valor"]),
                    ]
                    if tem_sab:
                        linha += [_fmt(a["produzido_sabado"]),
                                  "R$ " + _fmt_moeda(a["valor_sabado"])]
                    linhas.append(linha)
                    status.append(_status_pct(a["pct"]))
                story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=3,
                                    pct_status_por_linha=status))

            if premiacao_diaria:
                story.append(Spacer(1, 0.3 * cm))
                story.append(Paragraph(
                    "Ritmo diário por atividade (informativo — o bônus fecha no "
                    "período, não por dia; dias sem produção aparecem com a "
                    "observação da planilha)", e["sub"]))
                cab = ["Dia", "Atividade", "Produzido", "Meta do dia", "% do dia", "Observação"]
                cw = [largura * x for x in (0.13, 0.22, 0.13, 0.13, 0.11, 0.28)]
                aligns = ["l", "l", "r", "r", "r", "l"]
                linhas = [[
                    p["dia"], p["atividade"], _fmt(p["produzido"]), _fmt(p["meta"]),
                    f"{p['pct']:.0f}%" if p["pct"] is not None else "—",
                    p["observacao"].title() if p["observacao"] else "—",
                ] for p in premiacao_diaria]
                story.append(_tabela(cab, linhas, cw, e, aligns=aligns))
        elif premiacao_colaboradores:
            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph("Ranking por colaborador", e["sub"]))
            tem_sab = any(c.get("produzido_sabado") for c in premiacao_colaboradores)
            cab = ["Colaborador", "Produzido", "Meta", "% Meta", "Excedente", "Bônus R$"]
            if tem_sab:
                cab += ["Sáb pçs", "Sáb R$", "Total R$"]
                pesos = (0.18, 0.11, 0.10, 0.08, 0.10, 0.11, 0.09, 0.11, 0.12)
            else:
                pesos = (0.26, 0.16, 0.14, 0.12, 0.14, 0.18)
            cw = [largura * x for x in pesos]
            aligns = ["l"] + ["r"] * (len(cab) - 1)
            linhas, status = [], []
            for c in premiacao_colaboradores:
                linha = [
                    c["nome"], _fmt(c["produzido"]), _fmt(c["meta"]),
                    f"{c['pct']:.0f}%" if c["pct"] is not None else "—",
                    _fmt(c["excedente"]), "R$ " + _fmt_moeda(c["valor"]),
                ]
                if tem_sab:
                    linha += [_fmt(c["produzido_sabado"]),
                              "R$ " + _fmt_moeda(c["valor_sabado"]),
                              "R$ " + _fmt_moeda(c["valor_total"])]
                linhas.append(linha)
                status.append(_status_pct(c["pct"]))
                # Quem acumula funções ganha uma sub-linha por atividade: meta e
                # excedente são apurados POR ATIVIDADE, então o agregado pode
                # ficar abaixo de 100% e ainda assim haver bônus (bateu numa
                # função, ficou abaixo na outra). Sem o desdobramento a linha
                # do colaborador parece contraditória.
                if not c.get("multi_atividade"):
                    continue
                for a in c["atividades"]:
                    sub = [
                        f"    ↳ {a['atividade']} ({a['dias']}d · meta "
                        f"{_fmt(round(a['meta_dia']))}/dia)",
                        _fmt(a["produzido"]), _fmt(a["meta"]),
                        f"{a['pct']:.0f}%" if a["pct"] is not None else "—",
                        _fmt(a["excedente"]), "R$ " + _fmt_moeda(a["valor"]),
                    ]
                    if tem_sab:
                        sub += [_fmt(a["produzido_sabado"]),
                                "R$ " + _fmt_moeda(a["valor_sabado"]), ""]
                    linhas.append(sub)
                    status.append(_status_pct(a["pct"]))
            story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=3,
                                pct_status_por_linha=status))

    # Consistência — com 1 colaborador vira uma fileira de KPIs (a coluna "Colaborador"
    # de uma tabela de 1 linha só é redundante com o cabeçalho do relatório).
    if consistencia:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Consistência", e))
        story.append(Paragraph(
            "Regularidade = uniformidade diária · Assiduidade = % de dias úteis com "
            "produção", e["sub"]))
        story.append(Spacer(1, 0.3 * cm))
        if colaborador_unico and len(consistencia) == 1:
            c = consistencia[0]
            kpis_cons = [
                ("Dias ativos", str(c["dias_ativos"])),
                ("Média/Dia", _fmt(c["media_dia"])),
                ("Melhor dia", _fmt(c["melhor"])),
                ("Pior dia", _fmt(c["pior"])),
                ("Regularidade", f"{c['regularidade']:.0f}%"),
                ("Assiduidade", f"{c['assiduidade']:.0f}%"),
            ]
            story.append(_bloco_kpis(kpis_cons, e, colunas=3))
        else:
            cab = ["Colaborador", "Dias", "Média/Dia", "Melhor", "Pior",
                   "Regularid.", "Assiduid."]
            aligns = ["l", "r", "r", "r", "r", "r", "r"]
            cw = [largura * x for x in (0.28, 0.09, 0.14, 0.12, 0.12, 0.13, 0.12)]
            linhas = [[
                c["nome"], c["dias_ativos"], _fmt(c["media_dia"]),
                _fmt(c["melhor"]), _fmt(c["pior"]),
                f"{c['regularidade']:.0f}%", f"{c['assiduidade']:.0f}%",
            ] for c in consistencia]
            story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    # Produção diária (mesma estrutura do relatório de Facções). Com 1 colaborador
    # filtrado, mostra também Setor/Função exercidos em cada dia. Quando a
    # unidade tem Premiação, essa tabela fica redundante/ambígua com o "Detalhe
    # diário por atividade" (que já mostra o mesmo produzido, mas com a meta) —
    # nesse caso mostra só a versão com meta.
    tem_detalhe_premiacao = colaborador_unico and premiacao_diaria
    if producao_diaria and not tem_detalhe_premiacao:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Produção diária", e))
        md = int(meta_dia or 0)
        tem_setor = colaborador_unico and any(p.get("setor") or p.get("funcao") for p in producao_diaria)
        story.append(Paragraph(
            "Peças por dia" + (f" · Meta/dia: {_fmt(md)} pçs" if md else ""), e["sub"]))
        cab = ["Dia"] + (["Setor", "Função"] if tem_setor else []) + ["Produzido"] + \
              (["Meta", "% da Meta/dia"] if md else [])
        aligns = ["l"] + (["l", "l"] if tem_setor else []) + ["r"] + (["r", "r"] if md else [])
        if tem_setor:
            cw = ([largura * x for x in (0.14, 0.2, 0.2, 0.15, 0.15, 0.16)] if md
                  else [largura * x for x in (0.2, 0.3, 0.3, 0.2)])
        else:
            cw = ([largura * 0.28, largura * 0.24, largura * 0.24, largura * 0.24] if md
                  else [largura * 0.6, largura * 0.4])
        pct_col = len(cab) - 1 if md else None
        linhas, status = [], []
        for p in producao_diaria:
            row = [p["dia"]]
            if tem_setor:
                row += [p.get("setor") or "—", p.get("funcao") or "—"]
            row.append(_fmt(p["qtd"]))
            if md:
                pct = round(p["qtd"] / md * 100, 0)
                row.append(_fmt(md))
                row.append(f"{pct:.0f}%")
                status.append(_status_pct(pct))
            linhas.append(row)
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns,
                            pct_col=pct_col,
                            pct_status_por_linha=status if md else None))

    # Breakdowns por dimensão (Setor / Função / Tamanho / Cliente)
    for bd in breakdowns:
        if not bd["itens"]:
            continue
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao(f"Produção por {bd['label'].lower()}", e))
        linhas = [[nome, _fmt(v)] for nome, v in bd["itens"]]
        story.append(_tabela([bd["label"], "Peças"], linhas,
                            [largura * 0.72, largura * 0.28], e, aligns=["l", "r"]))

    return _construir(story, titulo=f"Relatório de Produção · {unidade_label}" + f" — {periodo_label}")
