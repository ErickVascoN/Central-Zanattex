"""
Gerador de relatório PDF de Previsão de Cargas, na identidade visual da
Central Zanattex. Reusa os helpers de `producao.relatorio_pdf` — mesmos
indicadores/filtros do dashboard, só muda o layout impresso."""

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


def gerar_pdf_cargas(*, periodo_label: str, filtros: str,
                     kpis: list[tuple[str, str]], resumo_mes: list[dict],
                     por_destino: dict, por_local: dict,
                     detalhamento_semanal: list[dict],
                     por_tipo_veiculo: dict, ocorrencias: dict,
                     detalhe_registros: list[dict]) -> bytes:
    """Relatório de Previsão de Cargas — mesma estrutura de indicadores do
    dashboard: Resumo executivo, Resumo por mês, Realizado por destino /
    Previsão por local, Detalhamento semanal, Por tipo de veículo,
    Ocorrências e Detalhe de registros."""
    e = _estilos()
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    story: list = [
        _faixa_marca("Relatório de Previsão de Cargas",
                     "Análise logística — previsão vs. realizado",
                     periodo_label, gerado_em, filtros, e),
        Spacer(1, 0.5 * cm),
    ]

    story.append(_titulo_secao("Resumo executivo", e))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_bloco_kpis(kpis, e, colunas=3))

    if resumo_mes:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Resumo por mês", e))
        cab = ["Mês", "Previsão", "Realizado", "Diferença", "Aderência", "Cancel./Adiadas"]
        aligns = ["l", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.16, 0.18, 0.18, 0.16, 0.14, 0.18)]
        linhas, status = [], []
        for m in resumo_mes:
            tem_real = m["realizado"] > 0
            linhas.append([
                m["mes"], _fmt_rs(m["previsao"]),
                _fmt_rs(m["realizado"]) if tem_real else "—",
                (f"{'+' if m['diferenca'] >= 0 else ''}{_fmt_rs(m['diferenca'])}"
                 if tem_real else "—"),
                f"{m['aderencia']:.1f}%" if m["aderencia"] is not None else "—",
                f"{m['canceladas']} / {m['adiadas']}",
            ])
            status.append(_status_pct(m["aderencia"]) if m["aderencia"] is not None else "neutro")
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=4,
                             pct_status_por_linha=status))

    if por_destino and por_destino.get("y"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Realizado por destino", e))
        pares = list(zip(por_destino["y"], por_destino["x"]))
        pares.sort(key=lambda p: p[1], reverse=True)
        cab = ["Destino", "Realizado"]
        cw = [LARGURA * 0.65, LARGURA * 0.35]
        linhas = [[n, _fmt_rs(v)] for n, v in pares]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r"]))

    if por_local and por_local.get("labels"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Previsão por local de carregamento", e))
        total = sum(por_local["valores"]) or 1
        cab = ["Local", "Previsão", "% do Total"]
        cw = [LARGURA * 0.5, LARGURA * 0.28, LARGURA * 0.22]
        linhas = [[n, _fmt_rs(v), f"{v / total * 100:.1f}%"]
                  for n, v in zip(por_local["labels"], por_local["valores"])]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if detalhamento_semanal:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Detalhamento semanal", e))
        cab = ["Mês", "Semana", "Cargas", "Previsão", "Realizado", "Diferença", "Aderência"]
        aligns = ["l", "l", "r", "r", "r", "r", "r"]
        cw = [LARGURA * x for x in (0.11, 0.24, 0.09, 0.16, 0.16, 0.13, 0.11)]
        linhas = [[
            s["mes"], s["semana"], str(s["cargas"]), _fmt_rs(s["previsao"]),
            _fmt_rs(s["realizado"]), f"{'+' if s['diferenca'] >= 0 else ''}{_fmt_rs(s['diferenca'])}",
            f"{s['aderencia']:.1f}%" if s["aderencia"] is not None else "—",
        ] for s in detalhamento_semanal]
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns))

    if por_tipo_veiculo and por_tipo_veiculo.get("x"):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Por tipo de veículo", e))
        cab = ["Veículo", "Cargas", "Previsão"]
        cw = [LARGURA * 0.4, LARGURA * 0.25, LARGURA * 0.35]
        linhas = [[v, str(n), _fmt_rs(p)] for v, n, p in
                  zip(por_tipo_veiculo["x"], por_tipo_veiculo["n"], por_tipo_veiculo["previsao"])]
        story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r", "r"]))

    if ocorrencias and not ocorrencias.get("vazio", True):
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Ocorrências — canceladas e adiadas", e))
        story.append(Paragraph(
            f"<b>{ocorrencias['total']} ocorrência(s)</b> · "
            f"{ocorrencias['n_cancel']} cancelada(s) · {ocorrencias['n_adiad']} adiada(s) · "
            f"impacto estimado: <b>{_fmt_rs(ocorrencias['valor_impacto'])}</b>", e["nota"]))
        dest = ocorrencias.get("grafico_destino")
        if dest and dest.get("y"):
            story.append(Spacer(1, 0.2 * cm))
            pares = sorted(zip(dest["y"], dest["x"]), key=lambda p: p[1], reverse=True)
            cab = ["Destino", "Ocorrências"]
            cw = [LARGURA * 0.7, LARGURA * 0.3]
            linhas = [[n, str(v)] for n, v in pares]
            story.append(_tabela(cab, linhas, cw, e, aligns=["l", "r"]))

    if detalhe_registros:
        story.append(Spacer(1, 0.45 * cm))
        story.append(_titulo_secao("Detalhe de registros", e))
        cab = ["Mês", "Data", "Destino", "Local", "Cliente", "Previsão", "Realizado", "Status"]
        aligns = ["l", "l", "l", "l", "l", "r", "r", "l"]
        cw = [LARGURA * x for x in (0.07, 0.1, 0.18, 0.1, 0.18, 0.13, 0.13, 0.11)]
        status_map = {"Cancelada": "crit", "Adiada": "warn"}
        linhas, status = [], []
        for r in detalhe_registros[:200]:
            linhas.append([
                r["mes"], r["data"], str(r["destino"])[:22], str(r["local"])[:14],
                str(r["cliente"])[:22], _fmt_rs(r["previsao"]),
                _fmt_rs(r["realizado"]) if r["realizado"] else "—", r["status"],
            ])
            status.append(status_map.get(r["status"], "neutro"))
        story.append(_tabela(cab, linhas, cw, e, aligns=aligns, pct_col=7,
                             pct_status_por_linha=status))
        if len(detalhe_registros) > 200:
            story.append(Paragraph(
                f"... e mais {len(detalhe_registros) - 200} registro(s).", e["nota"]))

    return _construir(story)
