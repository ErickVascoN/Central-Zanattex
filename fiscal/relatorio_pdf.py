"""
Relatório PDF de Saldo Fiscal, na identidade visual da Central Zanattex.
Reusa os helpers de `producao.relatorio_pdf` (mesmo padrão que
`carteira/relatorio_pdf.py` já segue) — nada de estilo/layout novo aqui,
só a estrutura de conteúdo específica do saldo fiscal.
"""
from __future__ import annotations

from datetime import datetime

from reportlab.lib.units import cm
from reportlab.platypus import Spacer

from producao.relatorio_pdf import (
    PAGE_W, MARGIN, _estilos, _faixa_marca, _titulo_secao, _bloco_kpis, _tabela, _construir, _fmt,
)

LARGURA = PAGE_W - 2 * MARGIN


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
