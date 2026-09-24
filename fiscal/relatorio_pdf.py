"""
Relatório PDF de Saldo Fiscal, na identidade visual da Central Zanattex.
Reusa os helpers de `producao.relatorio_pdf` (mesmo padrão que
`carteira/relatorio_pdf.py` já segue) — nada de estilo/layout novo aqui,
só a estrutura de conteúdo específica do saldo fiscal.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from reportlab.lib.units import cm
from reportlab.platypus import Spacer

from producao.relatorio_pdf import (
    PAGE_W, MARGIN, _estilos, _faixa_marca, _titulo_secao, _subheader_navy,
    _bloco_kpis, _tabela, _construir, _fmt,
)

LARGURA = PAGE_W - 2 * MARGIN


def fmt_br(v) -> str:
    """1.446.209,70 — milhar com ponto, decimal com vírgula."""
    return f"{v or 0:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_qtd(v) -> str:
    try:
        return f"{float(v):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    except (TypeError, ValueError):
        return "—"


def gerar_pdf_saldo(*, periodo_label: str, filtros: str, kpis: list[tuple[str, str]],
                    itens: list[dict]) -> bytes:
    """`itens`: lista de dicts com cliente, produto, nf, recebido, saldo, pct —
    uma linha por item de entrada (ver fiscal/servicos.py::saldo_por_produto)."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca("Relatório de Saldo Fiscal", "Industrialização por encomenda — saldo por item",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=3))

    if itens:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Saldo por item", e))
        cab = ["Cliente", "NF", "Produto", "Recebido", "Saldo", "% Consumido"]
        cw = [LARGURA * x for x in (0.20, 0.10, 0.34, 0.12, 0.12, 0.12)]
        linhas = [
            [i["cliente"], i["nf"], i["produto"], _fmt_qtd(i["recebido"]),
             _fmt_qtd(i["saldo"]), f'{i["pct"]:.1f}%']
            for i in itens
        ]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "l", "l", "r", "r", "r"]))

    return _construir(story, titulo=f"Relatório de Saldo Fiscal — {periodo_label}")


_STATUS_LABEL = {
    "NAO_UTILIZADO": "Não utilizado", "PARCIAL": "Parcialmente utilizado",
    "TOTAL": "Totalmente utilizado", "EXCEDIDO": "Excedido", "DIVERGENCIA": "Com divergência",
}
_STATUS_COR = {
    "TOTAL": "good", "PARCIAL": "warn", "NAO_UTILIZADO": "neutro",
    "EXCEDIDO": "crit", "DIVERGENCIA": "crit",
}


def gerar_pdf_historico(*, periodo_label: str, filtros: str, itens: list, totais: dict) -> bytes:
    """`itens`: list[servicos.ItemHistorico] (modo "por NF de entrada" do
    Histórico) — agrupado por tecido (descrição do produto) pra ficar fácil
    de ler, com subtotal por grupo e a % de conclusão colorida por linha."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca(
            "Histórico de Saldo Fiscal", "Consumo e saldo por NF de entrada, agrupado por tecido",
            periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    # Mesmos 4 cards da tela (ver views._totais_historico): quantidade por
    # unidade + valor em R$.
    story.append(_bloco_kpis([
        (card["rotulo"], "  |  ".join(f"{_fmt_qtd(q)} {u}" for u, q in card["linhas"]) or "—")
        for card in totais.get("cards", [])
    ] + [
        (f'{card["rotulo"]} (R$)', "R$ " + _fmt_qtd(card["valor"])) for card in totais.get("cards", [])
    ], e, colunas=4))

    if not itens:
        return _construir(story, titulo=f"Histórico de Saldo Fiscal — {periodo_label}")

    grupos: dict[str, list] = {}
    for linha in itens:
        grupos.setdefault(linha.item.x_prod, []).append(linha)

    cab = ["NF", "Data", "Fornecedor", "Entrada", "Utilizado", "Saldo", "% Conclusão", "Status"]
    cw = [LARGURA * x for x in (0.09, 0.12, 0.17, 0.12, 0.12, 0.12, 0.11, 0.15)]
    aligns = ["l", "l", "l", "r", "r", "r", "r", "l"]

    story.append(Spacer(1, 0.45 * cm))
    story.append(_titulo_secao("Por tecido", e))
    for produto, linhas_grupo in grupos.items():
        unidade = linhas_grupo[0].item.u_com
        recebido = sum((l.item.q_com for l in linhas_grupo), Decimal("0"))
        utilizado = sum((l.utilizado for l in linhas_grupo), Decimal("0"))
        saldo = sum((l.saldo for l in linhas_grupo), Decimal("0"))
        story.append(Spacer(1, 0.2 * cm))
        story.append(_subheader_navy(
            f"{produto}  ·  Recebido: {_fmt_qtd(recebido)} {unidade}  ·  "
            f"Utilizado: {_fmt_qtd(utilizado)} {unidade}  ·  Saldo: {_fmt_qtd(saldo)} {unidade}", e))

        linhas_tbl, status_por_linha = [], []
        for l in linhas_grupo:
            linhas_tbl.append([
                l.item.nota_fiscal.n_nf, l.item.nota_fiscal.data_emissao.strftime("%d/%m/%Y"),
                l.item.nota_fiscal.cliente.nome,
                f"{_fmt_qtd(l.item.q_com)} {unidade}", f"{_fmt_qtd(l.utilizado)} {unidade}",
                f"{_fmt_qtd(l.saldo)} {unidade}", f"{l.pct_utilizado:.1f}%",
                _STATUS_LABEL.get(l.status, l.status),
            ])
            status_por_linha.append(_STATUS_COR.get(l.status, "neutro"))
        story.append(_tabela(
            cab, linhas_tbl, cw, e, aligns=aligns, pct_col=6, pct_status_por_linha=status_por_linha))

    return _construir(story, titulo=f"Histórico de Saldo Fiscal — {periodo_label}")
