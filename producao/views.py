from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from integracao.fontes import CORES_FACCAO
from integracao.normalize import normalize_text
from . import servicos
from .metas_calc import calcular_meta_faccoes
from .unificada import grupo_de

_COR_PADRAO = "#1e3a8a"   # navy-soft — barras sem cor de facção definida
_COR_LINHA = "#dc2626"    # red-600 — linha de evolução

# cores por faixa de atingimento (comparação visual com a meta)
_COR_GOOD, _COR_WARN, _COR_CRIT, _COR_NEUTRO = "#059669", "#d97706", "#be123c", "#1e3a8a"


def _cor_ating(pct):
    if pct is None:
        return _COR_NEUTRO
    return _COR_GOOD if pct >= 100 else _COR_WARN if pct >= 80 else _COR_CRIT


def _pct(prod, meta):
    return round(prod / meta * 100, 1) if meta else None


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
        if pct is None or pct != pct:  # trata NaN (facção sem meta) como sem valor
            pct = None
        metas_linhas.append({
            "faccao": str(r["FACCAO"]).title(),
            "realizado": int(r["QUANTIDADE"]),
            "meta": int(r["META_MES"]),
            "pct": pct,
            "pct_barra": min(100, pct) if pct is not None else 0,
            "status": _status(pct),
            "sem_meta": r["META_MES"] <= 0,
        })

    # meta/dia e meta período (mensal) por facção e por grupo — comparação visual
    meta_por_fac = {}
    meta_por_grupo = {}
    meta_dia_total = 0
    for _, r in meta_res["rank_df"].iterrows():
        md = int(r.get("META_DIA", 0) or 0)
        mm = int(r.get("META_MES", 0) or 0)
        meta_por_fac[normalize_text(str(r["FACCAO"]))] = {"meta_dia": md, "meta_mes": mm}
        meta_dia_total += md
        g = grupo_de(str(r["FACCAO"]))
        meta_por_grupo[g] = meta_por_grupo.get(g, 0) + mm

    # ---- dados dos gráficos (Plotly) ----
    # Produção por grupo — Produzido × Meta (ordem asc, maior no topo)
    grupos_asc = list(reversed(grupos))
    grafico_grupo = {
        "y": [g for g, _ in grupos_asc],
        "x": [v for _, v in grupos_asc],
        "meta": [int(meta_por_grupo.get(g, 0)) for g, _ in grupos_asc],
        "pct": [_pct(v, meta_por_grupo.get(g, 0)) for g, v in grupos_asc],
        "cores": [_cor_ating(_pct(v, meta_por_grupo.get(g, 0))) for g, v in grupos_asc],
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

    # ---- Aba "Análise por Facção" (por Grupo e por Facção granular) ----
    diaria_g = servicos.diaria_empilhada(df_periodo, dim="GRUPO")
    diaria_f = servicos.diaria_empilhada(df_periodo, dim="FACCAO")
    acum_g = servicos.acumulada(df_periodo, dim="GRUPO")
    acum_f = servicos.acumulada(df_periodo, dim="FACCAO")
    heat_g = servicos.heatmap_dim_dia(df_periodo, dim="GRUPO")
    heat_f = servicos.heatmap_dim_dia(df_periodo, dim="FACCAO")
    consist = servicos.consistencia(df_periodo, dim="FACCAO")

    # cruza meta/dia e meta período em cada linha de consistência (comparação visual)
    for c in consist:
        mf = meta_por_fac.get(c["faccao_n"], {})
        c["meta_dia"] = mf.get("meta_dia", 0)
        c["meta_periodo"] = mf.get("meta_mes", 0)
        # atingimento diário: média/dia vs meta/dia (comparação visual)
        if c["meta_dia"] > 0:
            pct = c["media_dia"] / c["meta_dia"] * 100
            c["ating_dia"] = round(pct, 0)
            c["ating_status"] = "good" if pct >= 100 else "warn" if pct >= 80 else "crit"
            c["ating_barra"] = min(100, pct)
        else:
            c["ating_dia"] = None
            c["ating_status"] = "neutro"
            c["ating_barra"] = 0

    # ranking por facção (granular) — Produzido × Meta
    fac_rank = servicos.por_faccao(df_periodo, limite=30)
    fac_rank_asc = list(reversed(fac_rank))
    def _meta_fac(f):
        return meta_por_fac.get(normalize_text(f), {}).get("meta_mes", 0)
    grafico_fac_rank = {
        "y": [f.title() for f, _ in fac_rank_asc],
        "x": [v for _, v in fac_rank_asc],
        "meta": [int(_meta_fac(f)) for f, _ in fac_rank_asc],
        "pct": [_pct(v, _meta_fac(f)) for f, v in fac_rank_asc],
        "cores": [_cor_ating(_pct(v, _meta_fac(f))) for f, v in fac_rank_asc],
    }

    # ---- Aba "Produtos" ----
    prod_rank = servicos.ranking_produtos(df_periodo)
    prod_rank_asc = list(reversed(prod_rank))
    grafico_prod_rank = {
        "y": [p for p, _ in prod_rank_asc],
        "x": [v for _, v in prod_rank_asc],
        "cor": "#1e3a8a",
    }
    prod_mix = servicos.mix_produtos(df_periodo)
    prod_evol = servicos.evolucao_top_produtos(df_periodo)
    heat_prod_cli = servicos.heatmap_produto_cliente(df_periodo)
    treemap = servicos.treemap_produto_cliente(df_periodo)

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
        # aba Análise por Facção (dois níveis: grupo e facção)
        "diaria_grupo_json": diaria_g,
        "diaria_faccao_json": diaria_f,
        "acum_grupo_json": acum_g,
        "acum_faccao_json": acum_f,
        "heat_grupo_json": heat_g,
        "heat_faccao_json": heat_f,
        "fac_rank_json": grafico_fac_rank,
        "meta_dia_total": meta_dia_total,
        "consistencia": consist,
        # aba Produtos
        "prod_rank_json": grafico_prod_rank,
        "prod_mix_json": prod_mix,
        "prod_evol_json": prod_evol,
        "heat_prod_cli_json": heat_prod_cli,
        "treemap_json": treemap,
    }
    return render(request, "producao/dashboard.html", contexto)
