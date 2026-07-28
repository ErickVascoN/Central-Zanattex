"""
Gerador de relatório PDF de Carteira de Pedidos, na identidade visual da
Central Zanattex. Reusa os helpers de `producao.relatorio_pdf` — mesmos
indicadores do dashboard, só muda o layout impresso (tabelas em vez de
gráficos, cards de KPI, faixa navy)."""

from __future__ import annotations

from datetime import datetime

from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer

from producao.relatorio_pdf import (
    PAGE_W, MARGIN, _estilos, _faixa_marca, _titulo_secao, _bloco_kpis,
    _tabela, _construir, _fmt, _status_pct,
)

LARGURA = PAGE_W - 2 * MARGIN


def _fmt_rs(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    except (TypeError, ValueError):
        return "—"


def gerar_pdf_carteira(*, periodo_label: str, filtros: str,
                       kpis: list[tuple[str, str]],
                       pecas_categoria: list[dict],
                       por_tamanho: dict, top_produtos: dict,
                       por_estado: dict, por_centro_custo: dict,
                       resumo_cliente: list[dict], abc: dict) -> bytes:
    """Relatório de Carteira de Pedidos — mesma estrutura de indicadores do
    dashboard: Resumo executivo, Distribuição por categoria, Tamanho, Top
    produtos, Estado, Centro de custo, Resumo por cliente e Curva ABC."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca("Relatório de Carteira de Pedidos",
                     "Análise comercial — pedidos em aberto",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=3))

    if pecas_categoria:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Distribuição por categoria", e))
        story.append(Paragraph("Peças e pedidos em aberto por categoria", e["sub"]))
        cab = ["Categoria", "Peças", "% Peças", "Pedidos"]
        aligns = ["l", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.4, 0.2, 0.2, 0.2)]
        linhas = [[c["categoria"], _fmt(c["pecas"]), f"{c['pct']:.1f}%", str(c["pedidos"])]
                  for c in pecas_categoria]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    if por_tamanho and por_tamanho.get("x"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Distribuição por tamanho", e))
        cab = ["Tamanho", "Peças", "Valor"]
        cw = [LARGURA * 0.34, LARGURA * 0.33, LARGURA * 0.33]
        linhas = [[t, _fmt(p), _fmt_rs(v)] for t, p, v in
                  zip(por_tamanho["x"], por_tamanho["pecas"], por_tamanho["valor"])]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if top_produtos and top_produtos.get("y"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Top produtos — peças em aberto", e))
        triplas = list(zip(top_produtos["y"], top_produtos["pecas"],
                           top_produtos["x"], top_produtos["clientes"]))
        triplas.sort(key=lambda t: t[1], reverse=True)
        cab = ["Produto", "Peças", "Valor", "Clientes"]
        cw = [LARGURA * 0.46, LARGURA * 0.18, LARGURA * 0.22, LARGURA * 0.14]
        linhas = [[nome, _fmt(pecas), _fmt_rs(valor), str(cli)]
                  for nome, pecas, valor, cli in triplas]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r", "r"]))

    if por_estado and por_estado.get("x"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Distribuição por estado", e))
        total = sum(por_estado["y"]) or 1
        cab = ["Estado", "Valor", "% do Total"]
        cw = [LARGURA * 0.34, LARGURA * 0.33, LARGURA * 0.33]
        linhas = [[uf, _fmt_rs(v), f"{v / total * 100:.1f}%"]
                  for uf, v in zip(por_estado["x"], por_estado["y"])]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if por_centro_custo and por_centro_custo.get("y"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Distribuição por centro de custo", e))
        cab = ["Centro de Custo", "Valor", "Peças", "Pedidos"]
        cw = [LARGURA * 0.4, LARGURA * 0.24, LARGURA * 0.18, LARGURA * 0.18]
        linhas = sorted(
            zip(por_centro_custo["y"], por_centro_custo["x"],
                por_centro_custo["pecas"], por_centro_custo["pedidos"]),
            key=lambda t: t[1], reverse=True,
        )
        linhas = [[cc, _fmt_rs(v), _fmt(p), str(ped)] for cc, v, p, ped in linhas]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r", "r"]))

    if resumo_cliente:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Resumo por cliente", e))
        cab = ["Cliente", "Estado", "Pedidos", "Peças", "Valor", "% Carteira"]
        aligns = ["l", "l", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.28, 0.1, 0.12, 0.13, 0.2, 0.17)]
        linhas = [[r["cliente"], r["estado"], str(r["pedidos"]), _fmt(r["pecas"]),
                   _fmt_rs(r["valor"]), f"{r['pct']:.1f}%"] for r in resumo_cliente]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    if abc and abc.get("linhas"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Curva ABC de clientes", e))
        story.append(Paragraph(
            f"Classe A (até 80% acumulado): {abc['n_a']} cliente(s)  ·  "
            f"B (80–95%): {abc['n_b']}  ·  C (95–100%): {abc['n_c']}", e["sub"]))
        cab = ["Cliente", "Valor", "% Acumulado", "Classe"]
        aligns = ["l", "r", "r", "l"]
        cw = [LARGURA * x for x in (0.4, 0.22, 0.2, 0.18)]
        classe_status = {"A": "good", "B": "warn", "C": "crit"}
        linhas, status = [], []
        for r in abc["linhas"]:
            linhas.append([r["cliente"], _fmt_rs(r["valor"]), f"{r['pct_acum']:.1f}%", r["classe"]])
            status.append(classe_status.get(r["classe"], "neutro"))
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=3,
                             pct_status_por_linha=status))

    return _construir(story)
