from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from integracao.fontes import CORES_FACCAO
from . import servicos
from .metas_calc import calcular_meta_faccoes

_COR_PADRAO = "#1e3a8a"   # navy-soft — barras sem cor de facção definida
_COR_LINHA = "#dc2626"    # red-600 — linha de evolução


@login_required
def dashboard(request):
    """Dashboard de Produção Diária — KPIs + gráficos, ao vivo do Google Sheets."""
    df = servicos.carregar_producao()

    if df.empty:
        return render(request, "producao/dashboard.html", {
            "titulo_pagina": "Análise de Produção",
            "sem_dados": True,
        })

    meses = servicos.meses_disponiveis(df)  # [(ano, mes), ...] desc

    # mês selecionado (default: mais recente)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]

    kpis = servicos.resumo_periodo(df_periodo)
    grupos = servicos.por_grupo(df_periodo)
    faccoes = servicos.por_faccao(df_periodo)
    clientes = servicos.top_clientes(df_periodo)
    evolucao = servicos.evolucao_mensal(df)

    # ---- Realizado × Meta ----
    meta_res = calcular_meta_faccoes(
        df_periodo[["DATA", "FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"]],
        ano_sel, mes_sel,
    )
    meta_total = meta_res["meta_mes_total"]
    total_real = meta_res["total_geral"]
    pct_meta = round(total_real / meta_total * 100, 1) if meta_total else None

    def _status(pct):
        if pct is None:
            return "neutro"
        if pct >= 100:
            return "good"
        if pct >= 80:
            return "warn"
        return "crit"

    metas_linhas = []
    for _, r in meta_res["rank_df"].iterrows():
        # só facções com meta cadastrada OU com produção no período
        if r["META_MES"] <= 0 and r["QUANTIDADE"] <= 0:
            continue
        pct = r["PCT"]
        metas_linhas.append({
            "faccao": str(r["FACCAO"]).title(),
            "realizado": int(r["QUANTIDADE"]),
            "meta": int(r["META_MES"]),
            "pct": pct,
            "pct_barra": min(100, pct) if pct is not None else 0,
            "status": _status(pct),
            "sem_meta": r["META_MES"] <= 0,
        })

    # ---- dados dos gráficos (Plotly) ----
    # Barras horizontais: produção por grupo (ordem asc para o maior ficar no topo)
    grupos_asc = list(reversed(grupos))
    grafico_grupo = {
        "y": [g for g, _ in grupos_asc],
        "x": [v for _, v in grupos_asc],
        "cores": [CORES_FACCAO.get(g, _COR_PADRAO) for g, _ in grupos_asc],
    }
    # Linha: evolução mensal
    grafico_evolucao = {
        "x": [lbl for lbl, _ in evolucao],
        "y": [v for _, v in evolucao],
        "cor": _COR_LINHA,
    }

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    contexto = {
        "titulo_pagina": "Análise de Produção",
        "sem_dados": False,
        "ano_sel": ano_sel,
        "mes_sel": mes_sel,
        "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "kpis": kpis,
        "faccoes": faccoes,
        "clientes": clientes,
        "grafico_grupo_json": grafico_grupo,
        "grafico_evolucao_json": grafico_evolucao,
        "n_grupos": len(grupos),
        "meta_total": meta_total,
        "total_real": total_real,
        "pct_meta": pct_meta,
        "pct_meta_status": _status(pct_meta),
        "pct_meta_barra": min(100, pct_meta) if pct_meta is not None else 0,
        "metas_linhas": metas_linhas,
    }
    return render(request, "producao/dashboard.html", contexto)
