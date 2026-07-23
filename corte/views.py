from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos, itaju_servicos, lencol_servicos

_COR_BARRA = "#1e3a8a"   # navy-soft
_PALETA = [
    "#dc2626", "#1e3a8a", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4338ca", "#15803d", "#b45309", "#a21caf", "#0e7490",
]


def _periodo_custom(request):
    """'de'/'ate' na querystring (dia único ou intervalo). (None, None) se
    ausente/inválido — mesmo padrão usado em Produção Diária."""
    de_raw = request.GET.get("de")
    if not de_raw:
        return None, None
    try:
        de = datetime.strptime(de_raw, "%Y-%m-%d").date()
        ate_raw = request.GET.get("ate")
        ate = datetime.strptime(ate_raw, "%Y-%m-%d").date() if ate_raw else de
        if ate < de:
            de, ate = ate, de
        return de, ate
    except ValueError:
        return None, None


@login_required
def dashboard(request):
    """Controle de Corte — Visão Geral por unidade (Arealva / Iacanga)."""
    unidades = servicos.UNIDADES
    unidade = request.GET.get("unidade", unidades[0][0])
    if unidade not in dict(unidades):
        unidade = unidades[0][0]
    unidade_label = dict(unidades)[unidade]

    df = servicos.carregar_corte(unidade)
    if df.empty:
        return render(request, "corte/dashboard.html", {
            "titulo_pagina": "Análise de Corte",
            "unidades": [{"chave": k, "label": l, "ativa": k == unidade} for k, l in unidades],
            "unidade_label": unidade_label,
            "sem_dados": True,
        })

    meses = servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    # ---- período: mês inteiro (padrão) ou dia único / intervalo customizado ----
    data_de, data_ate = _periodo_custom(request)
    periodo_custom = data_de is not None
    if periodo_custom:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()
    # Análises semanais/mensais não fazem sentido com um único dia selecionado
    # (é uma "visão do dia", não uma "visão do período").
    dia_unico = periodo_custom and data_de == data_ate

    # ---- filtros: OP · Estação · Produto · Tamanho (mesmos do original) ----
    opcoes = servicos.opcoes_filtro(df)
    ops_sel = [v for v in request.GET.getlist("ops") if v in opcoes["ops"]]
    estacoes_sel = [v for v in request.GET.getlist("estacoes") if v in opcoes["estacoes"]]
    produtos_sel = [v for v in request.GET.getlist("produtos") if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    df_periodo = servicos.aplicar_filtros(
        df_periodo, ops=ops_sel, estacoes=estacoes_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel,
    )

    kpis = servicos.resumo(df_periodo)
    diaria = servicos.producao_diaria(df_periodo)
    meta_total = servicos.meta_total_ponderada(unidade, df_periodo)
    diaria_estacao = servicos.producao_diaria_por_estacao(df_periodo)
    estacao = servicos.por_estacao(df_periodo)
    tamanho = servicos.por_tamanho(df_periodo)
    produto = servicos.por_produto(df_periodo)
    cores = servicos.top_cores(df_periodo)

    # ---- Produção por Estação: progresso vs meta diária (ponderada) ----
    progresso_estacao = servicos.progresso_por_estacao(unidade, df_periodo)
    # sobrepõe a meta/dia (linha tracejada) na mesma cor de cada série do gráfico
    meta_por_nome = {r["estacao"]: r["meta_dia"] for r in progresso_estacao}
    for s in diaria_estacao.get("series", []):
        m = meta_por_nome.get(s["name"])
        s["meta"] = m if m else None

    # ---- Produção semanal comparativa (com meta semanal ponderada) ----
    # Sem sentido para 1 dia único (é uma "visão do dia").
    semanal_json = ({"semanas": [], "series": []} if dia_unico else
                     servicos.producao_semanal_por_estacao(unidade, df_periodo))

    # ---- Acompanhamento por OP ----
    resumo_op = servicos.resumo_por_op(df_periodo)
    op_sel = request.GET.get("op") or (resumo_op[0]["op"] if resumo_op else None)
    if op_sel and op_sel not in {r["op"] for r in resumo_op}:
        op_sel = resumo_op[0]["op"] if resumo_op else None
    detalhe = servicos.detalhe_op(df_periodo, op_sel) if op_sel else None
    cor_op_json = None
    if detalhe and detalhe["cor_qtd"]:
        asc = list(reversed(detalhe["cor_qtd"]))
        cor_op_json = {"y": [c for c, _ in asc], "x": [v for _, v in asc], "cor": "#1e3a8a"}

    def _barh(itens, cor=_COR_BARRA):
        asc = list(reversed(itens))
        return {"y": [n for n, _ in asc], "x": [v for _, v in asc], "cor": cor}

    def _pizza(itens):
        return {"labels": [n for n, _ in itens], "valores": [v for _, v in itens],
                "cores": [_PALETA[i % len(_PALETA)] for i in range(len(itens))]}

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    contexto = {
        "titulo_pagina": "Análise de Corte",
        "sem_dados": False,
        "unidades": [{"chave": k, "label": l, "ativa": k == unidade} for k, l in unidades],
        "unidade": unidade,
        "unidade_label": unidade_label,
        "ano_sel": ano_sel,
        "mes_sel": mes_sel,
        "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "dia_unico": dia_unico,
        "periodo_custom": periodo_custom,
        "periodo_label": periodo_label,
        "data_de": data_de.isoformat() if data_de else "",
        "data_ate": data_ate.isoformat() if data_ate else "",
        "data_min": data_min,
        "data_max": data_max,
        # filtros (mesmos do original: OP · Estação · Produto · Tamanho)
        "filtros": [
            {"label": "OP", "name": "ops", "opcoes": opcoes["ops"], "selecionados": ops_sel},
            {"label": "Estação", "name": "estacoes", "opcoes": opcoes["estacoes"], "selecionados": estacoes_sel},
            {"label": "Produto", "name": "produtos", "opcoes": opcoes["produtos"], "selecionados": produtos_sel},
        ] + ([{"label": "Tamanho", "name": "tamanhos", "opcoes": opcoes["tamanhos"], "selecionados": tamanhos_sel}]
             if opcoes["tamanhos"] else []),
        "filtros_ativos": bool(ops_sel or estacoes_sel or produtos_sel or tamanhos_sel),
        # KPIs e gráficos
        "kpis": kpis,
        "diaria_json": {"x": diaria["x"], "y": diaria["y"], "mm5": diaria["mm5"], "meta": meta_total},
        "diaria_estacao_json": diaria_estacao,
        "estacao_json": _pizza(estacao),
        "tamanho_json": _pizza(tamanho),
        "tem_tamanho": bool(tamanho),
        "produto_json": _barh(produto),
        "cores_json": _barh(cores, cor="#be123c"),
        "progresso_estacao": progresso_estacao,
        "semanal_json": semanal_json,
        "resumo_op": resumo_op,
        "op_sel": op_sel,
        "detalhe_op": detalhe,
        "cor_op_json": cor_op_json,
    }
    return render(request, "corte/dashboard.html", contexto)


@login_required
def itaju_dashboard(request):
    """Controle de Corte · Itaju (Ponto Palito Marcelino) — fonte própria,
    sem metas: o indicador central é o caseamento Cima × Fundo × Fronha."""
    df = itaju_servicos.carregar_itaju()
    if df.empty:
        return render(request, "corte/itaju.html", {
            "titulo_pagina": "Análise de Corte · Itaju", "sem_dados": True,
        })

    meses = itaju_servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = _periodo_custom(request)
    periodo_custom = data_de is not None
    if periodo_custom:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()

    opcoes = itaju_servicos.opcoes_filtro(df)
    ops_sel = [v for v in request.GET.getlist("ops") if v in opcoes["ops"]]
    estacoes_sel = [v for v in request.GET.getlist("estacoes") if v in opcoes["estacoes"]]
    cores_sel = [v for v in request.GET.getlist("cores") if v in opcoes["cores"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    df_periodo = itaju_servicos.aplicar_filtros(
        df_periodo, ops=ops_sel, estacoes=estacoes_sel, cores=cores_sel, tamanhos=tamanhos_sel,
    )

    kpis = itaju_servicos.resumo(df_periodo)
    casea = itaju_servicos.caseamento(df_periodo)
    n_ok = sum(1 for r in casea if r["status"] == "ok")
    n_div = len(casea) - n_ok
    saldo = sum(r["diferenca"] for r in casea)

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    contexto = {
        "titulo_pagina": "Análise de Corte · Itaju",
        "sem_dados": False,
        "ano_sel": ano_sel, "mes_sel": mes_sel, "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "periodo_custom": periodo_custom, "periodo_label": periodo_label,
        "data_de": data_de.isoformat() if data_de else "",
        "data_ate": data_ate.isoformat() if data_ate else "",
        "data_min": data_min, "data_max": data_max,
        "filtros": [
            {"label": "OP", "name": "ops", "opcoes": opcoes["ops"], "selecionados": ops_sel},
        ] + ([{"label": "Estação", "name": "estacoes", "opcoes": opcoes["estacoes"], "selecionados": estacoes_sel}]
             if opcoes["estacoes"] else []) + ([
            {"label": "Cor", "name": "cores", "opcoes": opcoes["cores"], "selecionados": cores_sel},
        ] if opcoes["cores"] else []) + [
            {"label": "Tamanho", "name": "tamanhos", "opcoes": opcoes["tamanhos"], "selecionados": tamanhos_sel},
        ],
        "filtros_ativos": bool(ops_sel or estacoes_sel or cores_sel or tamanhos_sel),
        "kpis": kpis,
        "casea": casea, "casea_n_ok": n_ok, "casea_n_div": n_div, "casea_saldo": saldo,
        "detalhe_op": itaju_servicos.detalhe_por_op(df_periodo),
        "diaria_json": itaju_servicos.producao_diaria_por_produto(df_periodo),
        "mix_json": itaju_servicos.mix_produto(df_periodo),
        "tamanho_json": itaju_servicos.por_tamanho_produto(df_periodo),
        "cor_json": itaju_servicos.por_cor_ou_estacao(df_periodo),
    }
    return render(request, "corte/itaju.html", contexto)


@login_required
def lencol_dashboard(request):
    """Controle de Corte · Lençol (Arealva) — Visão Geral. Fonte própria
    (planilha separada das Mantas): caseamento Jogo Duplo × Fundo, produção
    mensal, market share por empresa, evolução diária, top categorias."""
    df = lencol_servicos.carregar_lencol()
    if df.empty:
        return render(request, "corte/lencol.html", {
            "titulo_pagina": "Análise de Corte · Lençol", "sem_dados": True,
        })

    meses = lencol_servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = _periodo_custom(request)
    periodo_custom = data_de is not None
    if periodo_custom:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
        dias_periodo = (data_ate - data_de).days + 1
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
        import calendar
        dias_periodo = calendar.monthrange(ano_sel, mes_sel)[1]
    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()
    dia_unico = periodo_custom and data_de == data_ate
    # "Produção mensal" (compara vários meses, ignora o período filtrado) só
    # aparece quando o período realmente abrange mais de 1 mês.
    mostra_mensal = periodo_custom and (data_ate.year, data_ate.month) != (data_de.year, data_de.month)

    opcoes = lencol_servicos.opcoes_filtro(df)
    prest_sel = [v for v in request.GET.getlist("prestadores") if v in opcoes["prestadores"]]
    emp_sel = [v for v in request.GET.getlist("empresas") if v in opcoes["empresas"]]
    cat_sel = [v for v in request.GET.getlist("categorias") if v in opcoes["categorias"]]
    df_periodo = lencol_servicos.aplicar_filtros(
        df_periodo, prestadores=prest_sel, empresas=emp_sel, categorias=cat_sel,
    )
    # "Produção mensal" é uma visão histórica (todos os meses) — não faz sentido
    # escopada a 1 mês só; segue o mesmo padrão de "Evolução mensal" da Produção
    # (usa a base toda, só com os filtros de prestador/empresa/categoria).
    df_hist = lencol_servicos.aplicar_filtros(
        df, prestadores=prest_sel, empresas=emp_sel, categorias=cat_sel,
    )

    kpis = lencol_servicos.resumo(df_periodo, dias_periodo)
    casea = lencol_servicos.caseamento_resumo(df_periodo)
    insights = lencol_servicos.insights(df_periodo, kpis["ticket_medio"], kpis["total_valor"])

    # ---- OPs: resumo + caseamento + detalhe de 1 OP selecionada ----
    resumo_op = lencol_servicos.resumo_por_op(df_periodo)
    casea_op = lencol_servicos.caseamento_por_op(df_periodo)
    op_sel = request.GET.get("op") or (resumo_op[0]["op"] if resumo_op else None)
    if op_sel and op_sel not in {r["op"] for r in resumo_op}:
        op_sel = resumo_op[0]["op"] if resumo_op else None
    detalhe_op = lencol_servicos.detalhe_op(df_periodo[df_periodo["OP"] == op_sel]) if op_sel else None

    # ---- Financeiro ----
    fin_kpis = lencol_servicos.financeiro_kpis(df_periodo, kpis["dias_com_dados"])

    # ---- Metas (aba METAS da mesma planilha) ----
    from .lencol_loader import load_metas_lencol
    metas_comp = lencol_servicos.comparar_metas(df_periodo, load_metas_lencol())
    metas_res = lencol_servicos.metas_resumo(metas_comp)

    # ---- Ranking ----
    ranking = lencol_servicos.ranking_geral(df_periodo)

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    tab_sel = request.GET.get("tab", "geral")
    if tab_sel not in ("geral", "prestadores", "ops", "empresas", "categorias",
                       "temporal", "financeiro", "metas", "ranking"):
        tab_sel = "geral"

    contexto = {
        "titulo_pagina": "Análise de Corte · Lençol",
        "sem_dados": False,
        "tab_sel": tab_sel,
        "ano_sel": ano_sel, "mes_sel": mes_sel, "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "periodo_custom": periodo_custom, "periodo_label": periodo_label,
        "data_de": data_de.isoformat() if data_de else "",
        "data_ate": data_ate.isoformat() if data_ate else "",
        "data_min": data_min, "data_max": data_max,
        "filtros": [
            {"label": "Prestador", "name": "prestadores", "opcoes": opcoes["prestadores"], "selecionados": prest_sel},
            {"label": "Empresa", "name": "empresas", "opcoes": opcoes["empresas"], "selecionados": emp_sel},
            {"label": "Categoria", "name": "categorias", "opcoes": opcoes["categorias"], "selecionados": cat_sel},
        ],
        "filtros_ativos": bool(prest_sel or emp_sel or cat_sel),
        "dia_unico": dia_unico,
        "mostra_mensal": mostra_mensal,
        "kpis": kpis,
        "casea": casea,
        "insights": insights,
        "mensal_json": lencol_servicos.producao_mensal(df_hist),
        "empresa_json": lencol_servicos.market_share_empresa(df_periodo),
        "diaria_json": lencol_servicos.evolucao_diaria(df_periodo),
        "categorias_json": lencol_servicos.top_categorias(df_periodo),

        # ── Prestadores ──────────────────────────────────────────────────────
        "prestadores_tabela": lencol_servicos.por_prestador_tabela(df_periodo),
        "prest_rank_json": lencol_servicos.por_prestador_ranking(df_periodo),
        "prest_evol_json": (lencol_servicos.evolucao_mensal_por_prestador(df_hist)
                            if mostra_mensal else {"x": [], "series": []}),
        "prest_heat_json": lencol_servicos.heatmap_prestador_empresa(df_periodo),

        # ── OPs ──────────────────────────────────────────────────────────────
        "resumo_op": resumo_op,
        "casea_op": casea_op,
        "op_sel": op_sel,
        "detalhe_op": detalhe_op,

        # ── Empresas ─────────────────────────────────────────────────────────
        "empresas_tabela": lencol_servicos.por_empresa_tabela(df_periodo),
        "emp_vol_valor_json": lencol_servicos.volume_valor_por_empresa(df_periodo),
        "emp_evol_json": (lencol_servicos.evolucao_mensal_por_empresa(df_hist)
                          if mostra_mensal else {"x": [], "series": []}),

        # ── Categorias ───────────────────────────────────────────────────────
        "cat_vol_valor_json": lencol_servicos.por_categoria_volume_valor(df_periodo),
        "cat_treemap_json": lencol_servicos.treemap_empresa_categoria(df_periodo),
        "cat_heat_json": lencol_servicos.heatmap_empresa_categoria(df_periodo),

        # ── Temporal (semanal/calendário só fazem sentido com mais de 1 dia) ──
        "temp_diaria_json": lencol_servicos.diaria_ma7_acumulado(df_periodo),
        "temp_semanal_json": ({"x": [], "y": []} if dia_unico else
                              lencol_servicos.producao_semanal(df_periodo)),
        "temp_calendario_json": ({"x": [], "y": [], "z": []} if dia_unico else
                                 lencol_servicos.calendario_semana_dia(df_periodo)),
        "temp_media_dia_semana_json": ({"x": [], "y": []} if dia_unico else
                                       lencol_servicos.media_por_dia_semana(df_periodo)),

        # ── Financeiro ───────────────────────────────────────────────────────
        "fin_kpis": fin_kpis,
        "fin_rank_json": lencol_servicos.ranking_financeiro_prestador(df_periodo),
        "fin_ticket_json": lencol_servicos.ticket_medio_empresa(df_periodo),
        "fin_evol_json": (lencol_servicos.evolucao_financeira_mensal(df_hist)
                          if mostra_mensal else {"x": [], "series": []}),
        "fin_scatter_json": lencol_servicos.dispersao_qtd_valor(df_periodo),

        # ── Metas ────────────────────────────────────────────────────────────
        "metas_comp": metas_comp,
        "metas_meta_total": metas_res["meta_total"],
        "metas_real_total": metas_res["real_total"],
        "metas_atingimento_geral": metas_res["atingimento_geral"],
        "metas_n_atingidas": metas_res["n_atingidas"],
        "metas_bar_json": metas_res["grafico"],

        # ── Ranking ──────────────────────────────────────────────────────────
        "ranking": ranking,
        "radar_json": lencol_servicos.radar_performance(ranking),
    }
    return render(request, "corte/lencol.html", contexto)
