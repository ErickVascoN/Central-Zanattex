"""
Geradores de relatórios PDF do Corte (Manta Arealva/Iacanga, Itaju e Lençol),
na identidade visual da Central Zanattex.

Reusa os helpers e a "casca" visual de `producao.relatorio_pdf` (faixa navy
com wordmark, cards de KPI, banner de meta, tabelas com cabeçalho navy) —
os indicadores são os mesmos exibidos nos dashboards de Corte, só muda o
layout impresso.
"""

from __future__ import annotations

from datetime import datetime

from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, PageBreak

from producao.relatorio_pdf import (
    PAGE_W, MARGIN, _estilos, _faixa_marca, _titulo_secao, _subheader_navy,
    _bloco_kpis, _banner_meta, _tabela, _construir, _fmt, _status_pct,
)

LARGURA = PAGE_W - 2 * MARGIN


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — CORTE · MANTA (ARELVA / IACANGA)
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_manta(*, unidade_label: str, periodo_label: str, filtros: str,
                    kpis: list[tuple[str, str]], meta: dict | None,
                    progresso_estacao: list[dict],
                    producao_diaria: list[dict], meta_dia_total,
                    distribuicao_estacao: list[tuple[str, int]],
                    top_cores: list[tuple[str, int]],
                    por_tamanho: list[tuple[str, int]],
                    por_produto: list[tuple[str, int]],
                    resumo_op: list[dict]) -> bytes:
    """Relatório de Corte · Manta — mesma estrutura de indicadores do
    dashboard: Resumo executivo, Realizado × Meta (média/dia), Desempenho por
    Estação, Produção Diária, Distribuição por Estação, Top Cores, Tamanho,
    Produto e Análise por OP."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca(f"Relatório de Corte · {unidade_label}",
                     "Controle de produção — Manta",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=4))

    if meta is not None:
        story.append(Spacer(1, 0.2 * cm))
        story.append(_titulo_secao("Média/Dia × Meta/Dia", e))
        story.append(Spacer(1, 0.3 * cm))
        story.append(_banner_meta(meta.get("pct"), meta.get("realizado"),
                                  meta.get("meta"), e))

    if progresso_estacao:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Desempenho por estação", e))
        story.append(Paragraph(
            "Total, dias trabalhados, média/dia e % da meta diária ponderada "
            "pelo mix de tamanhos", e["sub"]))
        cab = ["Estação", "Meta/Dia", "Total", "Dias", "Média/Dia", "% Meta"]
        aligns = ["l", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.24, 0.16, 0.15, 0.11, 0.16, 0.18)]
        linhas, status = [], []
        for r in progresso_estacao:
            linhas.append([
                r["estacao"], _fmt(r["meta_dia"]), _fmt(r["total"]), str(r["dias"]),
                _fmt(r["media_dia"]),
                f"{r['pct']:.1f}%" if r["pct"] is not None else "sem meta",
            ])
            status.append(r["status"])
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=5,
                             pct_status_por_linha=status))

    if producao_diaria and producao_diaria.get("x"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Produção diária", e))
        md = int(meta_dia_total or 0)
        story.append(Paragraph(
            "Peças por dia" + (f" · Meta/dia: {_fmt(md)} pçs" if md else ""), e["sub"]))
        cab = ["Dia", "Produzido"] + (["% da Meta/dia"] if md else [])
        aligns = ["l", "r"] + (["r"] if md else [])
        cw = ([LARGURA * 0.4, LARGURA * 0.3, LARGURA * 0.3] if md
              else [LARGURA * 0.6, LARGURA * 0.4])
        linhas, status = [], []
        for dia, qtd in zip(producao_diaria["x"], producao_diaria["y"]):
            row = [dia, _fmt(qtd)]
            if md:
                pct = round(qtd / md * 100, 0)
                row.append(f"{pct:.0f}%")
                status.append(_status_pct(pct))
            linhas.append(row)
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns,
                            pct_col=2 if md else None,
                            pct_status_por_linha=status if md else None))

    if distribuicao_estacao:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Distribuição por estação", e))
        total = sum(v for _, v in distribuicao_estacao) or 1
        cab = ["Estação", "Peças", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
        linhas = [[n, _fmt(v), f"{v / total * 100:.1f}%"] for n, v in distribuicao_estacao]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if top_cores:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Top cores por volume", e))
        total = sum(v for _, v in top_cores) or 1
        cab = ["#", "Cor", "Peças", "% do Total"]
        cw = [LARGURA * 0.08, LARGURA * 0.52, LARGURA * 0.2, LARGURA * 0.2]
        linhas = [[str(i + 1), n, _fmt(v), f"{v / total * 100:.1f}%"]
                  for i, (n, v) in enumerate(top_cores)]
        story.append(_tabela(cab, linhas, cw, e, aligns=["r", "l", "r", "r"]))

    if por_tamanho:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Volume por tamanho", e))
        total = sum(v for _, v in por_tamanho) or 1
        cab = ["Tamanho", "Peças", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
        linhas = [[n, _fmt(v), f"{v / total * 100:.1f}%"] for n, v in por_tamanho]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if por_produto:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Produção por produto", e))
        total = sum(v for _, v in por_produto) or 1
        cab = ["Produto", "Peças", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
        linhas = [[n, _fmt(v), f"{v / total * 100:.1f}%"] for n, v in por_produto]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if resumo_op:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Análise por ordem de produção (OP)", e))
        cab = ["OP", "Produto", "Total", "Cores", "Dias", "Início", "Último corte"]
        aligns = ["l", "l", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.12, 0.28, 0.12, 0.09, 0.09, 0.15, 0.15)]
        linhas = [[r["op"], r["produto"], _fmt(r["total_pecas"]), str(r["qtd_cores"]),
                   str(r["dias_producao"]), r["data_inicio"], r["ultimo_corte"]]
                  for r in resumo_op[:50]]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))
        if len(resumo_op) > 50:
            story.append(Paragraph(f"... e mais {len(resumo_op) - 50} OPs.", e["nota"]))

    return _construir(story)


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — CORTE · ITAJU (PONTO PALITO)
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_itaju(*, periodo_label: str, filtros: str,
                    kpis: list[tuple[str, str]], caseamento: list[dict],
                    mix_produto: dict, por_tamanho: dict,
                    por_cor_ou_estacao: dict, detalhe_op: list[dict]) -> bytes:
    """Relatório de Corte · Itaju — sem metas: o indicador central é o
    caseamento Cima × Fundo × Fronha por OP + Tamanho + Cor, igual ao
    dashboard."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca("Relatório de Corte · Itaju",
                     "Controle de produção — Ponto Palito Marcelino",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=4))

    if caseamento:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Caseamento — Cima × Fundo × Fronha", e))
        story.append(Paragraph(
            "Reconciliação por OP + Tamanho + Cor · Diferença = Fundo − Cima "
            "(negativo = falta fundo, positivo = sobra)", e["sub"]))
        cab = ["OP", "Tamanho", "Cor", "Cima", "Fundo", "Fronha", "Diferença", "Status"]
        aligns = ["l", "l", "l", "r", "r", "r", "r", "l"]
        cw = [LARGURA * x for x in (0.14, 0.13, 0.17, 0.11, 0.11, 0.11, 0.11, 0.12)]
        status_map = {"ok": "good", "falta": "crit", "sobra": "warn"}
        linhas, status = [], []
        for r in caseamento[:80]:
            linhas.append([
                r["op"], r["tamanho"], r["cor"], _fmt(r["cima"]), _fmt(r["fundo"]),
                _fmt(r["fronha"]), f"{r['diferenca']:+d}", r["status"].upper(),
            ])
            status.append(status_map.get(r["status"], "neutro"))
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=7,
                             pct_status_por_linha=status))
        if len(caseamento) > 80:
            story.append(Paragraph(f"... e mais {len(caseamento) - 80} linhas.", e["nota"]))

    if mix_produto and mix_produto.get("labels"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Produção por produto", e))
        total = sum(mix_produto["valores"]) or 1
        cab = ["Produto", "Peças", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
        linhas = [[p, _fmt(v), f"{v / total * 100:.1f}%"]
                  for p, v in zip(mix_produto["labels"], mix_produto["valores"])]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if por_tamanho and por_tamanho.get("x"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Produção por tamanho", e))
        cab = ["Tamanho"] + [s["name"] for s in por_tamanho["series"]]
        cw = [LARGURA * 0.3] + [LARGURA * 0.7 / max(len(por_tamanho["series"]), 1)] * len(por_tamanho["series"])
        aligns = ["l"] + ["r"] * len(por_tamanho["series"])
        linhas = []
        for i, tam in enumerate(por_tamanho["x"]):
            row = [tam] + [_fmt(s["y"][i]) for s in por_tamanho["series"]]
            linhas.append(row)
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    if por_cor_ou_estacao and por_cor_ou_estacao.get("y"):
        story.append(Spacer(1, 0.45 * cm))
        campo = por_cor_ou_estacao.get("campo") or "Cor"
        story.append(_titulo_secao(f"Produção por {campo.lower()}", e))
        pares = list(zip(por_cor_ou_estacao["y"], por_cor_ou_estacao["x"]))
        pares.sort(key=lambda p: p[1], reverse=True)
        total = sum(v for _, v in pares) or 1
        cab = [campo, "Peças", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
        linhas = [[n, _fmt(v), f"{v / total * 100:.1f}%"] for n, v in pares[:20]]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if detalhe_op:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Detalhe por OP", e))
        cab = ["OP", "Tamanho", "Cor", "Cima", "Fundo", "Fronha", "Total", "Status"]
        aligns = ["l", "l", "l", "r", "r", "r", "r", "l"]
        cw = [LARGURA * x for x in (0.13, 0.12, 0.17, 0.1, 0.1, 0.1, 0.11, 0.17)]
        linhas = [[r["op"], r["tamanho"], r["cor"], _fmt(r["cima"]), _fmt(r["fundo"]),
                   _fmt(r["fronha"]), _fmt(r["total"]), r["status"].upper()]
                  for r in detalhe_op[:80]]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))
        if len(detalhe_op) > 80:
            story.append(Paragraph(f"... e mais {len(detalhe_op) - 80} linhas.", e["nota"]))

    return _construir(story)


def _fmt_rs(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    except (TypeError, ValueError):
        return "—"


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — CORTE · LENÇOL
# ═════════════════════════════════════════════════════════════════════════════
def gerar_pdf_lencol(*, periodo_label: str, filtros: str,
                     kpis: list[tuple[str, str]], destaques: str,
                     caseamento: dict, prestadores: list[dict],
                     resumo_op: list[dict], empresas: list[dict],
                     categorias: dict, financeiro: dict,
                     ranking_financeiro: dict, metas_comp: list[dict],
                     metas_resumo: dict, ranking: list[dict]) -> bytes:
    """Relatório de Corte · Lençol — mesma estrutura das abas do dashboard:
    Visão Geral, Prestadores, OPs, Empresas, Categorias, Financeiro, Metas e
    Ranking."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca("Relatório de Corte · Lençol",
                     "Controle de produção — Lençol (Arealva)",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    # ── Visão geral ──────────────────────────────────────────────────────────
    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=4))
    if destaques:
        story.append(Spacer(1, 0.25 * cm))
        story.append(Paragraph(destaques, e["nota"]))

    # ── Caseamento Jogo Duplo × Fundo ────────────────────────────────────────
    if caseamento and caseamento.get("linhas"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Caseamento — Jogo Duplo × Fundo", e))
        saldo = caseamento["fundo"] - caseamento["jogo"]
        resumo_txt = (
            f"Considerando apenas OPs com corte de fundo: "
            f"<b>{_fmt(caseamento['jogo'])} jogos duplos</b> vs "
            f"<b>{_fmt(caseamento['fundo'])} fundos</b>. " +
            ("Tudo caseado." if saldo == 0 else
             (f"Faltam {_fmt(abs(saldo))} fundos." if saldo < 0 else
              f"Sobram {_fmt(saldo)} fundos.")) +
            f" ({caseamento['divergentes']} divergência(s) de {caseamento['total_ops']} OPs)."
        )
        story.append(Paragraph(resumo_txt, e["nota"]))
        story.append(Spacer(1, 0.2 * cm))
        cab = ["OP", "Tamanho", "Jogo", "Fundo", "Fronha", "Diferença", "Status"]
        aligns = ["l", "l", "r", "r", "r", "r", "l"]
        cw = [LARGURA * x for x in (0.16, 0.14, 0.11, 0.11, 0.11, 0.13, 0.24)]
        status_map = {"ok": "good", "falta": "crit", "sobra": "warn"}
        linhas, status = [], []
        for r in caseamento["linhas"][:60]:
            d = r.get("diferenca", 0)
            linhas.append([
                str(r.get("op", "")), str(r.get("tamanho", "")),
                _fmt(r.get("jogo", 0)), _fmt(r.get("fundo", 0)), _fmt(r.get("fronha", 0)),
                f"{d:+d}", str(r.get("status", "")).upper(),
            ])
            status.append(status_map.get(r.get("status"), "neutro"))
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=6,
                             pct_status_por_linha=status))
        if len(caseamento["linhas"]) > 60:
            story.append(Paragraph(
                f"... e mais {len(caseamento['linhas']) - 60} linha(s).", e["nota"]))

    # ── Prestadores ──────────────────────────────────────────────────────────
    if prestadores:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Cortes por prestador", e))
        cab = ["Prestador", "Peças", "Valor", "Dias", "Média/Dia", "Empresas", "Categorias"]
        aligns = ["l", "r", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.26, 0.13, 0.16, 0.09, 0.13, 0.11, 0.12)]
        linhas = [[p["prestador"], _fmt(p["pecas"]), _fmt_rs(p["valor"]), str(p["dias"]),
                   _fmt(p["media_dia"]), str(p["empresas"]), str(p["categorias"])]
                  for p in prestadores]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    # ── OPs ──────────────────────────────────────────────────────────────────
    if resumo_op:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Análise por ordem de produção (OP)", e))
        cab = ["OP", "Categoria", "Peças", "Valor", "Prestadores", "Empresas", "Dias"]
        aligns = ["l", "l", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.13, 0.24, 0.13, 0.16, 0.12, 0.11, 0.11)]
        linhas = [[str(r.get("op", "")), str(r.get("categoria", ""))[:26],
                   _fmt(r.get("pecas", 0)), _fmt_rs(r.get("valor", 0)),
                   str(r.get("prestadores", "")), str(r.get("empresas", "")),
                   str(r.get("registros", ""))]
                  for r in resumo_op[:60]]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))
        if len(resumo_op) > 60:
            story.append(Paragraph(f"... e mais {len(resumo_op) - 60} OPs.", e["nota"]))

    # ── Empresas ─────────────────────────────────────────────────────────────
    if empresas:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Cortes por empresa", e))
        cab = ["Empresa", "Peças", "% Total", "Valor", "Prestadores", "OPs"]
        aligns = ["l", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.32, 0.13, 0.12, 0.18, 0.13, 0.12)]
        linhas = [[r["empresa"], _fmt(r["pecas"]), f"{r['share']:.1f}%", _fmt_rs(r["valor"]),
                   str(r["prestadores"]), str(r["ops"])] for r in empresas]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    # ── Categorias ───────────────────────────────────────────────────────────
    if categorias and categorias.get("volume", {}).get("y"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Volume por categoria", e))
        pares = list(zip(categorias["volume"]["y"], categorias["volume"]["x"]))
        pares.sort(key=lambda p: p[1], reverse=True)
        total = sum(v for _, v in pares) or 1
        cab = ["Categoria", "Peças", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
        linhas = [[n, _fmt(v), f"{v / total * 100:.1f}%"] for n, v in pares]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    # ── Financeiro ───────────────────────────────────────────────────────────
    if financeiro:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Financeiro", e))
        story.append(Spacer(1, 0.2 * cm))
        story.append(_bloco_kpis([
            ("Total a pagar/pago", _fmt_rs(financeiro.get("total", 0))),
            ("Média/Dia", _fmt_rs(financeiro.get("media_dia", 0))),
            ("Média/Peça", _fmt_rs(financeiro.get("media_peca", 0))),
        ], e, colunas=3))
        if ranking_financeiro and ranking_financeiro.get("y"):
            story.append(Spacer(1, 0.3 * cm))
            pares = list(zip(ranking_financeiro["y"], ranking_financeiro["x"]))
            pares.sort(key=lambda p: p[1], reverse=True)
            cab = ["Prestador", "Valor recebido"]
            cw = [LARGURA * 0.65, LARGURA * 0.35]
            linhas = [[n, _fmt_rs(v)] for n, v in pares]
            story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r"]))

    # ── Metas ────────────────────────────────────────────────────────────────
    if metas_comp:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Metas — Empresa × Categoria", e))
        story.append(Paragraph(
            f"Meta total: {_fmt(metas_resumo.get('meta_total', 0))}  ·  "
            f"Real: {_fmt(metas_resumo.get('real_total', 0))}  ·  "
            f"Atingimento geral: {metas_resumo.get('atingimento_geral', 0):.1f}%  ·  "
            f"{metas_resumo.get('n_atingidas', 0)} meta(s) batida(s)", e["sub"]))
        cab = ["Empresa", "Categoria", "Meta", "Real", "Desvio", "% Atingido"]
        aligns = ["l", "l", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.22, 0.24, 0.13, 0.13, 0.12, 0.16)]
        linhas, status = [], []
        for m in metas_comp:
            linhas.append([
                str(m["empresa"]).title(), str(m["categoria"]).title(),
                _fmt(m["meta"]), _fmt(m["real"]), f"{m['desvio']:+d}",
                f"{m['atingimento']:.1f}%",
            ])
            status.append(_status_pct(m["atingimento"]))
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=5,
                             pct_status_por_linha=status))

    # ── Ranking geral ────────────────────────────────────────────────────────
    if ranking:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Ranking geral de prestadores", e))
        cab = ["#", "Prestador", "Peças", "% do Total", "Valor", "Média/Dia"]
        aligns = ["r", "l", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.06, 0.32, 0.14, 0.14, 0.18, 0.16)]
        linhas = [[str(r["pos"]), r["prestador"], _fmt(r["pecas"]), f"{r['pct']:.1f}%",
                   _fmt_rs(r["valor"]), _fmt(r["media_dia"])] for r in ranking]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    return _construir(story)


# ═════════════════════════════════════════════════════════════════════════════
# RELATÓRIO — CORTE CONSOLIDADO (Manta Arealva + Manta Iacanga + Lençol Arealva)
# ═════════════════════════════════════════════════════════════════════════════
def _bloco_manta_detalhado(titulo: str, grupos: list[dict], e: dict) -> list:
    if not grupos:
        return [Paragraph(f"{titulo}: sem produção no período.", e["nota"])]
    story = [_subheader_navy(titulo, e)]
    total = sum(g["subtotal"] for g in grupos)
    cab = ["OP", "Produto", "Tamanho", "Cor", "Qtd"]
    cw = [LARGURA * x for x in (0.16, 0.34, 0.16, 0.2, 0.14)]
    for g in grupos:
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph(
            f"Estação: <b>{g['estacao']}</b> — Subtotal: {_fmt(g['subtotal'])} peças", e["nota"]))
        linhas = [[l["op"], l["produto"], l["tamanho"], l["cor"], _fmt(l["quantidade"])]
                  for l in g["linhas"]]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "l", "l", "l", "r"]))
    story.append(Paragraph(f"<b>TOTAL {titulo}: {_fmt(total)} peças</b>", e["nota"]))
    return story


def _bloco_manta_resumo(titulo: str, por_estacao: list[tuple], top_ops: list[dict], e: dict) -> list:
    if not por_estacao:
        return [Paragraph(f"{titulo}: sem produção no período.", e["nota"])]
    story = [_subheader_navy(titulo, e)]
    total = sum(v for _, v in por_estacao)
    cab = ["Estação", "Peças", "% do Total"]
    cw = [LARGURA * 0.5, LARGURA * 0.25, LARGURA * 0.25]
    linhas = [[n, _fmt(v), f"{v / total * 100:.1f}%" if total else "0.0%"] for n, v in por_estacao]
    story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))
    if top_ops:
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph("Top OPs", e["sub"]))
        cab2 = ["OP", "Produto", "Peças"]
        cw2 = [LARGURA * 0.2, LARGURA * 0.6, LARGURA * 0.2]
        linhas2 = [[o["op"], o["produto"], _fmt(o["total_pecas"])] for o in top_ops[:8]]
        story.append(_tabela(cab2, linhas2, cw2, e, aligns=["l", "l", "r"]))
    story.append(Paragraph(f"<b>TOTAL {titulo}: {_fmt(total)} peças</b>", e["nota"]))
    return story


def _bloco_lencol_detalhado(grupos: list[dict], e: dict) -> list:
    titulo = "Lençol Arealva"
    if not grupos:
        return [Paragraph(f"{titulo}: sem produção no período.", e["nota"])]
    story = [_subheader_navy(titulo, e)]
    total = sum(g["subtotal"] for g in grupos)
    total_valor = sum(g["subtotal_valor"] for g in grupos)
    cab = ["OP", "Categoria", "Empresa", "Tecido", "Qtd", "Valor"]
    cw = [LARGURA * x for x in (0.12, 0.22, 0.2, 0.16, 0.12, 0.18)]
    for g in grupos:
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph(
            f"Prestador: <b>{g['prestador']}</b> — Subtotal: {_fmt(g['subtotal'])} peças · "
            f"{_fmt_rs(g['subtotal_valor'])}", e["nota"]))
        linhas = [[l["op"], l["categoria"], l["empresa"], l["tecido"], _fmt(l["qtd"]),
                   _fmt_rs(l["valor_total"])] for l in g["linhas"]]
        store_aligns = ["l", "l", "l", "l", "r", "r"]
        story.append(_tabela(cab, linhas, cw, e, aligns=store_aligns))
    story.append(Paragraph(
        f"<b>TOTAL {titulo}: {_fmt(total)} peças · {_fmt_rs(total_valor)}</b>", e["nota"]))
    return story


def _bloco_lencol_resumo(prestadores: list[dict], top_ops: list[dict], e: dict) -> list:
    titulo = "Lençol Arealva"
    if not prestadores:
        return [Paragraph(f"{titulo}: sem produção no período.", e["nota"])]
    story = [_subheader_navy(titulo, e)]
    total = sum(p["pecas"] for p in prestadores)
    cab = ["Prestador", "Peças", "% do Total", "Valor"]
    cw = [LARGURA * 0.4, LARGURA * 0.2, LARGURA * 0.2, LARGURA * 0.2]
    linhas = [[p["prestador"], _fmt(p["pecas"]),
               f"{p['pecas'] / total * 100:.1f}%" if total else "0.0%", _fmt_rs(p["valor"])]
              for p in prestadores]
    story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r", "r"]))
    if top_ops:
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph("Top OPs", e["sub"]))
        cab2 = ["OP", "Categoria", "Tecido", "Peças", "Valor"]
        cw2 = [LARGURA * 0.16, LARGURA * 0.24, LARGURA * 0.2, LARGURA * 0.16, LARGURA * 0.24]
        linhas2 = [[o["op"], o["categoria"], o["tecido"], _fmt(o["pecas"]), _fmt_rs(o["valor"])]
                   for o in top_ops[:8]]
        story.append(_tabela(cab2, linhas2, cw2, e, aligns=["l", "l", "l", "r", "r"]))
    total_valor = sum(p["valor"] for p in prestadores)
    story.append(Paragraph(
        f"<b>TOTAL {titulo}: {_fmt(total)} peças · {_fmt_rs(total_valor)}</b>", e["nota"]))
    return story


def gerar_pdf_corte_consolidado(*, filtros: str, secoes: list[dict]) -> bytes:
    """Relatório Consolidado de Corte — Manta Arealva + Manta Iacanga + Lençol
    Arealva, idêntico em estrutura ao PDF enviado automaticamente por e-mail:
    3 seções (Dia / Mês Atual / Últimos 2 Meses). As seções 'Mês Atual' e
    'Últimos 2 Meses' sempre usam a Data Final como referência — só a seção 1
    muda: dia único vira detalhado, período vira resumo.

    `secoes`: lista de dicts, cada um com:
      - titulo, periodo_label
      - kpis: list[tuple[str,str]] (Total Geral, Manta Arealva, Manta Iacanga,
        Lençol Arealva)
      - detalhado: bool
      - manta_are / manta_iac: se detalhado, list de detalhado_por_estacao();
        senão, {"por_estacao": [...], "top_ops": [...]}
      - lencol: se detalhado, list de detalhado_por_prestador(); senão,
        {"prestadores": [...], "top_ops": [...]}
    """
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    periodo_capa = secoes[0]["periodo_label"] if secoes else ""
    story: list = [
        _faixa_marca("Relatório Consolidado de Corte",
                     "Manta Arealva + Manta Iacanga + Lençol Arealva",
                     periodo_capa, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    for i, sec in enumerate(secoes):
        if i > 0:
            story.append(PageBreak())
        story.append(_titulo_secao(f"{sec['titulo']} — {sec['periodo_label']}", e))
        story.append(Spacer(1, 0.3 * cm))
        story.append(_bloco_kpis(sec["kpis"], e, colunas=4))
        story.append(Spacer(1, 0.4 * cm))

        if sec["detalhado"]:
            story.extend(_bloco_manta_detalhado("Manta Arealva", sec["manta_are"], e))
            story.append(Spacer(1, 0.35 * cm))
            story.extend(_bloco_manta_detalhado("Manta Iacanga", sec["manta_iac"], e))
            story.append(Spacer(1, 0.35 * cm))
            story.extend(_bloco_lencol_detalhado(sec["lencol"], e))
        else:
            story.extend(_bloco_manta_resumo(
                "Manta Arealva", sec["manta_are"]["por_estacao"], sec["manta_are"]["top_ops"], e))
            story.append(Spacer(1, 0.35 * cm))
            story.extend(_bloco_manta_resumo(
                "Manta Iacanga", sec["manta_iac"]["por_estacao"], sec["manta_iac"]["top_ops"], e))
            story.append(Spacer(1, 0.35 * cm))
            story.extend(_bloco_lencol_resumo(
                sec["lencol"]["prestadores"], sec["lencol"]["top_ops"], e))

    return _construir(story)
