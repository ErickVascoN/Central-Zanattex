"""
Nota / Cotação de Frete em PDF — na identidade visual da Central Zanattex.

Reaproveita os helpers do gerador de relatórios (faixa da marca, títulos de
seção, tabelas) e monta um documento tipo "nota para o cliente": dados da
viagem, composição do custo (o que está gastando em quê) e o valor do frete.
"""

from __future__ import annotations

from datetime import datetime

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from producao import relatorio_pdf as R


def _brl(v) -> str:
    """Formata em Real: 1234.5 -> 'R$ 1.234,50'."""
    try:
        s = f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "R$ 0,00"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + s


def _data_br(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return datetime.now().strftime("%d/%m/%Y")


def _bloco_total(total, e) -> Table:
    """Faixa navy de destaque com o valor do frete, centralizado (fechamento)."""
    largura = R.PAGE_W - 2 * R.MARGIN
    cen_label = ParagraphStyle("cen_label", parent=e["cell"], alignment=TA_CENTER,
                               leading=13)
    cen_val = ParagraphStyle("cen_val", parent=e["cell"], alignment=TA_CENTER,
                             fontSize=24, leading=27)
    esq = Paragraph(
        '<font size="10" color="#e2e8f0">VALOR DO FRETE</font>', cen_label)
    val = Paragraph(
        f'<font size="24" color="#ffffff"><b>{_brl(total)}</b></font>', cen_val)
    card = Table([[esq], [val]], colWidths=[largura])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), R.NAVY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (0, 0), 14), ("BOTTOMPADDING", (0, 0), (0, 0), 2),
        ("TOPPADDING", (0, 1), (0, 1), 2), ("BOTTOMPADDING", (0, 1), (0, 1), 14),
    ]))
    return card


def gerar_nota_frete(dados: dict) -> bytes:
    """`dados` = snapshot da calculadora (getFreteSnapshot). Gera a nota."""
    e = R._estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    largura = R.PAGE_W - 2 * R.MARGIN
    total = float(dados.get("total_freight") or 0)

    story = [
        R._faixa_marca("Cotação de Frete",
                       "Formação de preço — frete rodoviário",
                       _data_br(dados.get("data")), gerado_em, "", e),
        Spacer(1, 0.5 * cm),
    ]

    # ── Dados da viagem ──────────────────────────────────────────────────────
    story.append(R._titulo_secao("Dados da viagem", e))
    story.append(Spacer(1, 0.25 * cm))
    km = float(dados.get("km") or 0)
    ida_volta = "Sim" if dados.get("ida_volta") else "Não"
    # Na calculadora o cliente é preenchido a partir do destino — é o mesmo
    # campo. Mostramos só "Cliente / Destino" para não repetir.
    cliente = dados.get("cliente") or dados.get("destino") or "—"
    # pares (rótulo, valor) distribuídos em 2 colunas por linha
    pares = [
        ("Cliente / Destino", cliente),
        ("Data", _data_br(dados.get("data"))),
        ("Distância", (f"{km:.0f} km" if km else "—") + f" · Ida e volta: {ida_volta}"),
        ("Veículo", dados.get("veiculo") or "—"),
    ]
    if dados.get("cavalo"):
        pares.append(("Cavalo mecânico", dados.get("cavalo")))
    if float(dados.get("cargo_value") or 0) > 0:
        pares.append(("Valor da carga / NF", _brl(dados.get("cargo_value"))))
        pares.append(("Risco (GRIS)", f"{float(dados.get('risk_pct') or 0):.2f}%"))
    linhas_v = []
    for i in range(0, len(pares), 2):
        r1 = pares[i]
        r2 = pares[i + 1] if i + 1 < len(pares) else ("", "")
        linhas_v.append([r1[0], r1[1], r2[0], r2[1]])
    tv = Table(linhas_v, colWidths=[largura * 0.16, largura * 0.34,
                                    largura * 0.18, largura * 0.32])
    tv.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), R.FAINT),
        ("TEXTCOLOR", (2, 0), (2, -1), R.FAINT),
        ("TEXTCOLOR", (1, 0), (1, -1), R.INK),
        ("TEXTCOLOR", (3, 0), (3, -1), R.INK),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, R.BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tv)

    # ── Composição do custo ──────────────────────────────────────────────────
    story.append(Spacer(1, 0.45 * cm))
    story.append(R._titulo_secao("Composição do custo", e))
    story.append(Paragraph("O que está sendo gasto em cada item da viagem", e["sub"]))
    itens = [it for it in dados.get("itens", []) if float(it.get("valor") or 0) != 0]
    cab = ["Item", "Valor", "% do frete"]
    cw = [largura * 0.5, largura * 0.25, largura * 0.25]
    linhas = []
    for it in itens:
        v = float(it["valor"])
        pct = (v / total * 100) if total else 0
        linhas.append([it["label"], _brl(v), f"{pct:.1f}%"])
    # subtotal de custos e margem
    custos = float(dados.get("custos") or 0)
    profit = float(dados.get("profit") or 0)
    profit_pct = float(dados.get("profit_pct") or 0)
    linhas.append(["Subtotal — custos", _brl(custos),
                   f"{(custos / total * 100) if total else 0:.1f}%"])
    linhas.append([f"Margem de lucro ({profit_pct:.0f}%)", _brl(profit),
                   f"{(profit / total * 100) if total else 0:.1f}%"])
    t = R._tabela(cab, linhas, cw, e, aligns=["l", "r", "r"])
    # destaca as duas últimas linhas (subtotal + margem)
    n = len(linhas)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, n - 1), (-1, n), "Helvetica-Bold"),
        ("LINEABOVE", (0, n - 1), (-1, n - 1), 0.8, R.NAVY),
        ("TEXTCOLOR", (0, n - 1), (-1, n), R.NAVY),
    ]))
    story.append(t)

    # ── Valor do frete (destaque) ────────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(_bloco_total(total, e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "Documento gerado para fins de cotação. Valores baseados nos parâmetros "
        "informados na calculadora no momento da emissão.", e["sub"]))

    return R._construir(story)
