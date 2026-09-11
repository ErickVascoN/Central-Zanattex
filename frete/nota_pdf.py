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

    return R._construir(story, titulo="Cotação de Frete · " + cliente + f" — {_data_br(dados.get('data'))}")


def dados_do_calculo(calculo) -> dict:
    """Converte um `CalculoFrete` já salvo (frete emitido) para o mesmo formato
    de snapshot que `getFreteSnapshot()` monta no JS — permite reaproveitar
    `gerar_nota_frete` para os fretes que já foram salvos, não só o cálculo
    em andamento na tela."""
    itens = [
        {"label": "Combustível / Diesel", "valor": calculo.diesel},
        {"label": "Motorista / Diárias", "valor": calculo.motorista},
        {"label": "Pedágio", "valor": calculo.pedagio},
        {"label": "Manutenção", "valor": calculo.manutencao},
        {"label": "Arla 32", "valor": calculo.arla},
        {"label": "Depreciação", "valor": calculo.depreciacao},
        {"label": "Seguro do veículo", "valor": calculo.seguro},
        {"label": "GRIS (Risco)", "valor": calculo.gris},
        {"label": "Multa", "valor": calculo.multa},
        {"label": f"Impostos ({calculo.impostos_pct:.0f}%)", "valor": calculo.impostos_valor},
    ]
    custos = sum(it["valor"] for it in itens)
    return {
        "cliente": calculo.cliente.nome if calculo.cliente else (calculo.destino or ""),
        "destino": calculo.destino or "",
        "km": calculo.distancia_km or 0,
        "ida_volta": calculo.ida_volta,
        "veiculo": calculo.veiculo or "",
        "cavalo": calculo.cavalo or "",
        "data": calculo.data.isoformat(),
        "itens": itens,
        "cargo_value": calculo.valor_carga,
        "risk_pct": calculo.risco_pct,
        "taxes_pct": calculo.impostos_pct,
        "taxes_value": calculo.impostos_valor,
        "profit_pct": calculo.margem_pct,
        "profit": calculo.valor_frete - custos,
        "custos": custos,
        "total_freight": calculo.valor_frete,
    }


def gerar_relatorio_fretes(calculos, *, periodo_label: str, filtros: str = "") -> bytes:
    """Relatório único cobrindo vários fretes já emitidos (salvos) — o "tudo
    junto" da tela de Dados & Análises: resumo, totais por indicador e uma
    tabela com um frete por linha. `calculos`: queryset/lista de `CalculoFrete`
    já ordenada (mais recente primeiro)."""
    e = R._estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    largura = R.PAGE_W - 2 * R.MARGIN

    calculos = list(calculos)
    n = len(calculos)
    total_freight = sum(c.valor_frete for c in calculos)
    total_km = sum((c.distancia_km or 0) for c in calculos)
    ticket_medio = total_freight / n if n else 0

    story = [
        R._faixa_marca("Relatório de Fretes Emitidos",
                       "Fretes já calculados e salvos — Calculadora de Frete",
                       periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(R._titulo_secao("Resumo", e))
    story.append(Spacer(1, 0.25 * cm))
    story.append(R._bloco_kpis([
        ("Fretes emitidos", str(n)),
        ("Frete total arrecadado", _brl(total_freight)),
        ("Km total rodado", f"{total_km:,.0f} km".replace(",", ".")),
        ("Ticket médio", _brl(ticket_medio)),
    ], e, colunas=4))

    story.append(Spacer(1, 0.45 * cm))
    story.append(R._titulo_secao("Totais por indicador", e))
    story.append(Spacer(1, 0.25 * cm))
    story.append(R._bloco_kpis([
        (label, _brl(sum(getattr(c, campo) for c in calculos)))
        for campo, label in (
            ("diesel", "Combustível / Diesel"), ("motorista", "Motorista / Diárias"),
            ("pedagio", "Pedágio"), ("manutencao", "Manutenção"),
            ("arla", "Arla 32"), ("depreciacao", "Depreciação"),
            ("seguro", "Seguro"), ("gris", "GRIS (Risco)"),
            ("impostos_valor", "Impostos"), ("multa", "Multa"),
        )
    ], e, colunas=3))

    story.append(Spacer(1, 0.45 * cm))
    story.append(R._titulo_secao("Fretes no período", e))
    story.append(Paragraph(f"{n} cálculo(s) — um por linha", e["sub"]))
    story.append(Spacer(1, 0.15 * cm))
    cab = ["Data", "Cliente", "Destino", "Km", "Ida/Volta", "Veículo", "Frete Total"]
    cw = [largura * x for x in (0.12, 0.17, 0.24, 0.09, 0.10, 0.13, 0.15)]
    linhas = [[
        c.data.strftime("%d/%m/%Y"),
        c.cliente.nome if c.cliente else "—",
        c.destino or "—",
        f"{c.distancia_km:.0f}" if c.distancia_km else "—",
        "Sim" if c.ida_volta else "Não",
        c.veiculo or "—",
        _brl(c.valor_frete),
    ] for c in calculos]
    story.append(R._tabela(cab, linhas, cw, e, aligns=["l", "l", "l", "r", "l", "l", "r"]))

    titulo = "Relatório de Fretes Emitidos" + (f" — {periodo_label}" if periodo_label else "")
    return R._construir(story, titulo=titulo)
